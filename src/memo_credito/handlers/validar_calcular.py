"""Arma el expediente, valida, calcula indicadores y hechos, y escribe las partes 00-02 de la auditoría."""

from __future__ import annotations

import json
from typing import Any

from aws_lambda_powertools.metrics import MetricUnit, single_metric

from memo_credito.auditoria.canonico import a_json, json_canonico
from memo_credito.auditoria.paquete import escribir_parte, proyectar_extraccion
from memo_credito.calculos.analisis import analizar
from memo_credito.extraccion.expediente import ResultadoExtraccion, armar_expediente
from memo_credito.handlers._comun import (
    AlmacenS3,
    actualizar_estado,
    config,
    correlacionar,
    env,
    escribir_s3,
    leer_s3,
    logger,
    metrics,
    tracer,
)
from memo_credito.memo.hechos import construir_hechos


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    folio, ejecucion = event["folio"], event["ejecucion"]
    correlacionar(folio, ejecucion)
    cfg = config()
    bucket = env("BUCKET_DATOS")
    actualizar_estado(folio, estado="PROCESANDO")
    resultados = [
        ResultadoExtraccion.model_validate_json(leer_s3(bucket, e["clave_extraccion"])) for e in event["extracciones"]
    ]
    exp = armar_expediente(resultados)
    analisis = analizar(exp, cfg)
    hechos = construir_hechos(exp, analisis, cfg)
    contexto = {"expediente": a_json(exp), "analisis": a_json(analisis), "hechos": a_json(hechos)}
    clave_contexto = f"trabajo/{folio}/{ejecucion}/contexto.json"
    escribir_s3(bucket, clave_contexto, json_canonico(contexto).encode("utf-8"))

    almacen = AlmacenS3(env("BUCKET_AUDITORIA"))
    prefijo = f"{folio}/{ejecucion}"
    escribir_parte(
        almacen,
        prefijo,
        "00_entrada.json",
        {
            "folio": folio,
            "input_hash": event.get("input_hash"),
            "documentos": event.get("documentos", []),
            "modo_llm": event.get("modo_llm"),
            "reproceso": event.get("reproceso", 0),
        },
    )
    escribir_parte(
        almacen, prefijo, "01_extraccion.json", {"documentos": [proyectar_extraccion(r) for r in resultados]}
    )
    escribir_parte(
        almacen,
        prefijo,
        "02_analisis.json",
        {
            "analisis": a_json(analisis),
            "hechos": a_json(hechos),
            "config": {
                "politica": a_json(cfg.politica),
                "limites": a_json(cfg.limites),
                "fuente_precios": cfg.precios.fuente,
            },
        },
    )

    conciliaciones_fallidas = sum(1 for c in analisis.conciliaciones if not c.ok)
    metrics.add_metric(name="ConciliacionFallida", unit=MetricUnit.Count, value=conciliaciones_fallidas)
    for codigo in analisis.codigos_alerta:
        with single_metric(name="AlertasEmitidas", unit=MetricUnit.Count, value=1, namespace=metrics.namespace) as m:
            m.add_dimension(name="codigo", value=codigo)
    logger.info(
        "análisis calculado",
        extra={
            "alertas": list(analisis.codigos_alerta),
            "no_calculables": [i.id for i in analisis.indicadores if i.estado == "no_calculable"],
        },
    )
    return {
        "clave_contexto": clave_contexto,
        "alertas": list(analisis.codigos_alerta),
        "indicadores": json.loads(json_canonico({i.id: i.estado for i in analisis.indicadores})),
    }
