"""Registra la revisión: métricas de retroalimentación, candidatos de eval, estado final y partes 04 y 99 de la auditoría."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools.metrics import MetricUnit, single_metric

from memo_credito.auditoria.canonico import json_canonico, sha256_texto
from memo_credito.auditoria.paquete import escribir_manifiesto, escribir_parte
from memo_credito.handlers._comun import (
    AlmacenS3,
    actualizar_estado,
    ahora_iso,
    correlacionar,
    env,
    escribir_s3,
    leer_estado,
    leer_s3,
    logger,
    metrics,
    tabla,
    tracer,
)
from memo_credito.memo.verificador import RE_CORREO, RE_CURP, RE_TELEFONO


def distancia_edicion(a: str, b: str) -> float:
    """Levenshtein por palabras, normalizada a [0, 1]."""
    x, y = a.split(), b.split()
    if not x and not y:
        return 0.0
    previo = list(range(len(y) + 1))
    for i, px in enumerate(x, start=1):
        actual = [i] + [0] * len(y)
        for j, py in enumerate(y, start=1):
            actual[j] = min(previo[j] + 1, actual[j - 1] + 1, previo[j - 1] + (px != py))
        previo = actual
    return previo[-1] / max(len(x), len(y))


def contiene_pii(texto: str) -> bool:
    """CURP, correo o teléfono. El RFC de la empresa (persona moral) aparece legítimamente en el encabezado del memo."""
    return any(p.search(texto) for p in (RE_CURP, RE_CORREO, RE_TELEFONO))


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    folio, ejecucion = event["folio"], event["ejecucion"]
    correlacionar(folio, ejecucion)
    bucket = env("BUCKET_DATOS")
    rev = event["revision"]
    cuerpo = json.loads(leer_s3(bucket, rev["clave_revision"]).decode("utf-8"))
    borrador = leer_s3(bucket, event["redaccion"]["memo_s3_key"]).decode("utf-8")
    final = cuerpo.get("memo_final") or borrador
    distancia = distancia_edicion(borrador, final)
    sin_cambios = sha256_texto(final) == sha256_texto(borrador)
    estado_final = "LISTO_PARA_COMITE" if cuerpo["decision"] == "listo_para_comite" else "REQUIERE_CAMBIOS"
    escribir_s3(
        bucket, f"trabajo/{folio}/{ejecucion}/memo_final.md", final.encode("utf-8"), tipo="text/markdown; charset=utf-8"
    )

    item = leer_estado(folio)
    desde = item.get("pendiente_desde")
    minutos = (datetime.now(UTC) - datetime.fromisoformat(str(desde))).total_seconds() / 60 if desde else 0.0
    pii = contiene_pii(cuerpo.get("comentario", "")) or contiene_pii(final)
    registro = {
        "folio": folio,
        "ejecucion": ejecucion,
        "revisor_id": rev["revisor_id"],
        "decision": cuerpo["decision"],
        "calificaciones": cuerpo.get("calificaciones", {}),
        "categoria_error": cuerpo.get("categoria_error", "ninguna"),
        "comentario": None if pii else cuerpo.get("comentario", ""),
        "comentario_sha256": sha256_texto(cuerpo.get("comentario", "")),
        "memo_borrador_sha256": rev["memo_sha256"],
        "memo_final_sha256": sha256_texto(final),
        "sin_cambios": sin_cambios,
        "distancia_edicion": round(distancia, 4),
        "minutos_revision": round(minutos, 1),
        "pii_en_revision": pii,
        "registrado": ahora_iso(),
    }
    tabla().put_item(
        Item={
            "folio": folio,
            "sk": f"REVISION#{registro['registrado']}",
            **{k: (json.dumps(v) if isinstance(v, (dict, float)) else v) for k, v in registro.items() if k != "folio"},
        }
    )
    actualizar_estado(folio, estado=estado_final, token="-", revisor_id=rev["revisor_id"])  # noqa: S106 (se borra el task token)

    almacen = AlmacenS3(env("BUCKET_AUDITORIA"))
    prefijo = f"{folio}/{ejecucion}"
    # Objeción 8 del plan: el memo final entra a la auditoría inmutable solo si cambió y no trae PII
    parte = {**registro, "memo_final": None if (pii or sin_cambios) else final}
    escribir_parte(almacen, prefijo, "04_revision.json", parte)
    manifiesto = escribir_manifiesto(almacen, prefijo)

    if registro["categoria_error"] != "ninguna" or estado_final == "REQUIERE_CAMBIOS":
        redaccion = json.loads(almacen.leer(f"{prefijo}/03_redaccion.json").decode("utf-8"))
        candidato = {
            "folio": folio,
            "ejecucion": ejecucion,
            "categoria_error": registro["categoria_error"],
            "decision": cuerpo["decision"],
            "memo_borrador": redaccion.get("memo_md"),
            "comentario": registro["comentario"],
        }
        escribir_s3(
            bucket,
            f"trabajo/evals_candidatos/{folio}-{rev['memo_sha256'][:12]}.json",
            json_canonico(candidato).encode("utf-8"),
        )

    metrics.add_metric(name="TasaAceptacionSinCambios", unit=MetricUnit.Count, value=1 if sin_cambios else 0)
    metrics.add_metric(name="DistanciaEdicion", unit=MetricUnit.NoUnit, value=distancia)
    metrics.add_metric(name="TiempoRevisionMin", unit=MetricUnit.NoUnit, value=minutos)
    with single_metric(name="ErroresPorCategoria", unit=MetricUnit.Count, value=1, namespace=metrics.namespace) as m:
        m.add_dimension(name="categoria", value=registro["categoria_error"])
    for seccion, calificacion in (cuerpo.get("calificaciones") or {}).items():
        with single_metric(
            name="CalificacionSeccion", unit=MetricUnit.NoUnit, value=calificacion, namespace=metrics.namespace
        ) as m:
            m.add_dimension(name="seccion", value=seccion)
    logger.info("revisión registrada", extra={"estado_final": estado_final, "sin_cambios": sin_cambios})
    return {"estado_final": estado_final, "hash_final_auditoria": manifiesto["hash_final"]}
