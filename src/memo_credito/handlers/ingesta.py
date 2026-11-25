"""Ingesta: valida el manifiesto (sha256 de cada documento) y arranca la ejecución con nombre idempotente.

Disparo: EventBridge (S3 Object Created en entrada/{folio}/_manifest.json).
Idempotencia: el nombre de la ejecución es {folio}-{input_hash[:16]}[-rN]; Step Functions trata como idempotente un
StartExecution con el mismo nombre y la misma entrada.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import unquote_plus

from aws_lambda_powertools.metrics import MetricUnit
from botocore.exceptions import ClientError

from memo_credito.extraccion.seguridad import tipo_por_nombre
from memo_credito.handlers._comun import (
    actualizar_estado,
    config,
    correlacionar,
    env,
    leer_estado,
    leer_s3,
    logger,
    metrics,
    s3,
    sfn,
    tracer,
)

RE_FOLIO = re.compile(r"[A-Z0-9][A-Z0-9-]{2,39}")
RE_NOMBRE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
ACTIVOS = {"RECIBIDA", "PROCESANDO", "MEMO_GENERADO", "PENDIENTE_REVISION", "REVISION_RECIBIDA"}


def _sha256_objeto(bucket: str, clave: str) -> str:
    try:
        attrs = s3().get_object_attributes(Bucket=bucket, Key=clave, ObjectAttributes=["Checksum"])
        b64 = attrs.get("Checksum", {}).get("ChecksumSHA256")
        if b64 and "-" not in b64:
            return base64.b64decode(b64).hex()
    except ClientError:
        pass
    return hashlib.sha256(leer_s3(bucket, clave)).hexdigest()


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    cfg = config()
    bucket = event["detail"]["bucket"]["name"]
    clave = unquote_plus(event["detail"]["object"]["key"])
    _, folio, _ = clave.split("/", 2)
    if not RE_FOLIO.fullmatch(folio):
        raise ValueError("folio con formato inválido")
    manifiesto = json.loads(leer_s3(bucket, clave).decode("utf-8"))
    if manifiesto.get("folio") != folio:
        raise ValueError("el folio del manifiesto no coincide con la carpeta")
    documentos = manifiesto.get("documentos", [])
    if not documentos or len(documentos) > cfg.limites.max_archivos:
        raise ValueError(f"manifiesto con {len(documentos)} documentos")
    entrada = []
    for doc in documentos:
        nombre = doc["nombre"]
        if not RE_NOMBRE.fullmatch(nombre) or nombre == "_manifest.json":
            raise ValueError("nombre de documento inválido en el manifiesto")
        clave_doc = f"entrada/{folio}/{nombre}"
        sha = _sha256_objeto(bucket, clave_doc)
        if sha != doc["sha256"]:
            raise ValueError(f"el sha256 de {nombre} no coincide con el manifiesto")
        entrada.append(
            {"nombre": nombre, "clave": clave_doc, "sha256": sha, "tipo": tipo_por_nombre(nombre) or "desconocido"}
        )
    input_hash = hashlib.sha256(
        "|".join(f"{d['nombre']}:{d['sha256']}" for d in sorted(entrada, key=lambda d: d["nombre"])).encode()
    ).hexdigest()
    reproceso = int(manifiesto.get("reproceso", 0))
    nombre_ejecucion = f"{folio}-{input_hash[:16]}" + (f"-r{reproceso}" if reproceso else "")
    correlacionar(folio, nombre_ejecucion)
    modo = env("MODO_LLM", cfg.llm.modo)
    if env("PERMITIR_OVERRIDE_MODO", "false") == "true" and manifiesto.get("modo_llm") in ("simulado", "bedrock"):
        modo = manifiesto["modo_llm"]

    previo = leer_estado(folio)
    if reproceso and previo.get("estado") in ACTIVOS and previo.get("ejecucion_arn"):
        sfn().stop_execution(executionArn=previo["ejecucion_arn"], cause="reproceso solicitado")
        logger.info("ejecución previa detenida por reproceso")
    try:
        # Deduplicación atómica: un evento repetido del mismo manifiesto no regresa el estado ni arranca otra ejecución
        actualizar_estado(
            folio,
            condicion="attribute_not_exists(#ejecucion) OR #ejecucion <> :ejecucion",
            estado="RECIBIDA",
            input_hash=input_hash,
            ejecucion=nombre_ejecucion,
            reprocesos=reproceso,
            modo_llm=modo,
            motivo="-",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        metrics.add_metric(name="ManifiestosDuplicados", unit=MetricUnit.Count, value=1)
        logger.info("manifiesto ya recibido con la misma entrada: no se duplica")
        return {"folio": folio, "ejecucion": nombre_ejecucion, "duplicada": True}

    entrada_sfn = {
        "folio": folio,
        "input_hash": input_hash,
        "documentos": entrada,
        "modo_llm": modo,
        "reproceso": reproceso,
    }
    resp = sfn().start_execution(
        stateMachineArn=env("MAQUINA_ESTADOS_ARN"), name=nombre_ejecucion, input=json.dumps(entrada_sfn)
    )
    actualizar_estado(folio, ejecucion_arn=resp["executionArn"])
    metrics.add_metric(name="SolicitudesRecibidas", unit=MetricUnit.Count, value=1)
    logger.info("ejecución iniciada", extra={"documentos": len(entrada), "modo_llm": modo})
    return {"folio": folio, "ejecucion": nombre_ejecucion, "duplicada": False}
