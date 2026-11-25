"""Aplica la revisión del analista: valida y envía el task token a Step Functions.

Disparo: EventBridge (S3 Object Created en revisiones/<aws:userid>/<folio>/<memo_sha256>.json).
La identidad del revisor sale de la CLAVE, que IAM obliga a coincidir con ${aws:userid}: no la declara el cliente.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Literal
from urllib.parse import unquote_plus

from aws_lambda_powertools.metrics import MetricUnit
from botocore.exceptions import ClientError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from memo_credito.handlers._comun import (
    actualizar_estado,
    correlacionar,
    leer_estado,
    leer_s3,
    logger,
    metrics,
    sfn,
    tracer,
)

Seccion = Literal["resumen", "indicadores", "validaciones", "observaciones", "puntos"]


class RevisionEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    folio: str
    memo_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["listo_para_comite", "requiere_cambios"]
    calificaciones: dict[Seccion, int] = {}
    categoria_error: Literal[
        "ninguna", "cifra", "alerta_omitida", "alerta_falsa", "redaccion", "tono", "dato_faltante", "otro"
    ]
    comentario: str = Field(default="", max_length=2000)
    memo_final_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    memo_final: str | None = Field(default=None, max_length=60000)


def _rechazar(folio: str, motivo: str) -> dict[str, Any]:
    metrics.add_metric(name="RevisionesRechazadas", unit=MetricUnit.Count, value=1)
    logger.warning("revisión rechazada", extra={"motivo": motivo})
    with contextlib.suppress(ClientError):
        actualizar_estado(folio, ultima_revision_rechazada=motivo)
    return {"aplicada": False, "motivo": motivo}


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    bucket = event["detail"]["bucket"]["name"]
    clave = unquote_plus(event["detail"]["object"]["key"])
    partes = clave.split("/")
    if len(partes) != 4 or not partes[3].endswith(".json"):
        return _rechazar("desconocido", "clave de revisión con formato inválido")
    _, revisor_id, folio, archivo = partes
    try:
        revision = RevisionEntrada.model_validate_json(leer_s3(bucket, clave))
    except ValidationError as exc:
        return _rechazar(folio, f"revisión no cumple el esquema ({len(exc.errors())} errores)")
    if revision.folio != folio or revision.memo_sha256 != archivo[:-5]:
        return _rechazar(folio, "la revisión no corresponde a la clave")
    item = leer_estado(folio)
    correlacionar(folio, str(item.get("ejecucion", "?")))
    if item.get("estado") != "PENDIENTE_REVISION":
        return _rechazar(folio, f"la solicitud no está pendiente (estado={item.get('estado')})")
    if item.get("memo_sha256") != revision.memo_sha256:
        return _rechazar(folio, "el memo revisado no es el borrador vigente (hash distinto)")
    try:
        actualizar_estado(
            folio,
            condicion="#estado = :pendiente AND memo_sha256 = :memo",
            valores_condicion={":pendiente": "PENDIENTE_REVISION", ":memo": revision.memo_sha256},
            estado="REVISION_RECIBIDA",
            revisor_id=revisor_id,
            clave_revision=clave,
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return _rechazar(folio, "la revisión ya se había aplicado")
        raise
    salida = {
        "revisor_id": revisor_id,
        "clave_revision": clave,
        "decision": revision.decision,
        "memo_sha256": revision.memo_sha256,
    }
    sfn().send_task_success(taskToken=item["token"], output=json.dumps(salida))
    metrics.add_metric(name="RevisionesAplicadas", unit=MetricUnit.Count, value=1)
    logger.info("revisión aplicada", extra={"decision": revision.decision})
    return {"aplicada": True}
