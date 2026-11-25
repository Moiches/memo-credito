"""Guarda el task token (tabla cifrada con KMS) y deja la solicitud en PENDIENTE_REVISION.

Step Functions se queda esperando (.waitForTaskToken) hasta que `aplicar_revision` envíe el token.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools.metrics import MetricUnit

from memo_credito.handlers._comun import actualizar_estado, ahora_iso, correlacionar, logger, metrics, tracer


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    folio, ejecucion = event["folio"], event["ejecucion"]
    correlacionar(folio, ejecucion)
    actualizar_estado(
        folio,
        estado="PENDIENTE_REVISION",
        token=event["token"],
        memo_sha256=event["memo_sha256"],
        memo_s3_key=event["memo_s3_key"],
        pendiente_desde=ahora_iso(),
        escalamientos=event.get("escalamientos", 0),
    )
    metrics.add_metric(name="RevisionesPendientes", unit=MetricUnit.Count, value=1)
    logger.info("en espera de revisión del analista")
    return {"esperando": True}
