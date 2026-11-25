"""Extrae un documento (una iteración del Map). Verifica el sha256 antes de parsear."""

from __future__ import annotations

import hashlib
from typing import Any

from aws_lambda_powertools.metrics import MetricUnit

from memo_credito.extraccion.expediente import extraer_documento
from memo_credito.handlers._comun import config, correlacionar, env, escribir_s3, leer_s3, logger, metrics, tracer


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    folio, ejecucion, doc = event["folio"], event["ejecucion"], event["doc"]
    correlacionar(folio, ejecucion)
    bucket = env("BUCKET_DATOS")
    datos = leer_s3(bucket, doc["clave"])
    if hashlib.sha256(datos).hexdigest() != doc["sha256"]:
        raise ValueError(f"el documento {doc['nombre']} cambió después de la ingesta")
    with tracer.provider.in_subsegment("extraer_documento") as sub:
        resultado = extraer_documento(doc["nombre"], datos, config())
        sub.put_annotation("rechazado", resultado.rechazo is not None)
    clave = f"trabajo/{folio}/{ejecucion}/extracciones/{doc['nombre']}.json"
    escribir_s3(bucket, clave, resultado.model_dump_json().encode("utf-8"))
    rechazado = resultado.rechazo is not None
    metrics.add_metric(
        name="DocumentosRechazados" if rechazado else "DocumentosExtraidos", unit=MetricUnit.Count, value=1
    )
    logger.info("documento extraído", extra={"documento": doc["nombre"], "rechazado": rechazado})
    return {"nombre": doc["nombre"], "clave_extraccion": clave, "rechazado": rechazado}
