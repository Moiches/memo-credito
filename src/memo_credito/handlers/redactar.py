"""Redacta el memo con el grafo de LangGraph (Bedrock o simulado), lo ensambla, verifica y audita.

Idempotente por (folio, ejecución, contexto, forzar_plantilla): un reintento de Step Functions después de un éxito
no vuelve a pagar tokens.
"""

from __future__ import annotations

import json
from typing import Any

from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.idempotency import DynamoDBPersistenceLayer, IdempotencyConfig, idempotent

from memo_credito.auditoria.canonico import sha256_texto
from memo_credito.auditoria.paquete import escribir_parte
from memo_credito.dominio.modelos import Analisis, Expediente
from memo_credito.extraccion.solicitud import canarios
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
from memo_credito.llm.puerto import prompt_id
from memo_credito.memo.ensamblador import Metadatos
from memo_credito.memo.modelos import HechosParaLLM
from memo_credito.memo.servicio import redactar_memo
from memo_credito.pipeline import ahora_mx, parte_redaccion

_persistencia = DynamoDBPersistenceLayer(table_name=env("TABLA_IDEMPOTENCIA", "idempotencia-local"))
_config_idem = IdempotencyConfig(
    event_key_jmespath="[folio, ejecucion, clave_contexto, forzar_plantilla]", expires_after_seconds=7 * 24 * 3600
)


def _cliente(modo: str) -> Any:
    if modo == "bedrock":
        from memo_credito.llm.bedrock import ClienteBedrock

        return ClienteBedrock(
            config(),
            modelo_invocacion=env("PERFIL_INFERENCIA_ARN", config().llm.modelo_id),
            guardrail_id=env("GUARDRAIL_ARN"),
            guardrail_version=env("GUARDRAIL_VERSION"),
        )
    from memo_credito.llm.simulado import ClienteSimulado

    return ClienteSimulado()


@logger.inject_lambda_context(clear_state=True)
@tracer.capture_lambda_handler
@metrics.log_metrics
@idempotent(persistence_store=_persistencia, config=_config_idem)
def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    folio, ejecucion = event["folio"], event["ejecucion"]
    correlacionar(folio, ejecucion)
    cfg = config()
    bucket = env("BUCKET_DATOS")
    contexto = json.loads(leer_s3(bucket, event["clave_contexto"]).decode("utf-8"))
    exp = Expediente.model_validate(contexto["expediente"])
    analisis = Analisis.model_validate(contexto["analisis"])
    hechos = HechosParaLLM.model_validate(contexto["hechos"])
    canarios_sol = canarios(leer_s3(bucket, f"entrada/{folio}/solicitud.json"))
    cliente = _cliente(event.get("modo_llm") or cfg.llm.modo)
    meta = Metadatos(
        fecha=ahora_mx().date().isoformat(),
        ejecucion=ejecucion,
        prompt_id=prompt_id(cfg),
        modelo=cliente.modelo,
        modo=cliente.modo,
        commit=env("GIT_COMMIT", "desconocido"),
    )
    res = redactar_memo(
        exp,
        analisis,
        hechos,
        cliente,
        cfg,
        meta,
        canarios_sol,
        forzar_plantilla=event.get("forzar_plantilla"),
        subsegmento=lambda nombre: tracer.provider.in_subsegment(nombre),
    )
    memo_sha = sha256_texto(res.memo)
    clave_memo = f"trabajo/{folio}/{ejecucion}/memo.md"
    escribir_s3(bucket, clave_memo, res.memo.encode("utf-8"), tipo="text/markdown; charset=utf-8")
    escribir_parte(
        AlmacenS3(env("BUCKET_AUDITORIA")),
        f"{folio}/{ejecucion}",
        "03_redaccion.json",
        parte_redaccion(res, cfg, meta, invocacion=getattr(cliente, "modelo_invocacion", None)),
    )

    tokens_in, tokens_out = res.tokens
    intervenciones = sum(1 for ll in res.estado.llamadas if ll.respuesta.guardrail.intervino)
    reintentos = sum(ll.respuesta.reintentos for ll in res.estado.llamadas)
    fallas = sum(len(errs) for errs in res.estado.errores_por_intento)
    for nombre, unidad, valor in [
        ("MemosGenerados", MetricUnit.Count, 1),
        ("MemoPlantilla", MetricUnit.Count, 1 if res.estado.origen == "plantilla" else 0),
        ("LlamadasLLM", MetricUnit.Count, len(res.estado.llamadas)),
        ("TokensEntrada", MetricUnit.Count, tokens_in),
        ("TokensSalida", MetricUnit.Count, tokens_out),
        ("CostoMemoUSD", MetricUnit.NoUnit, float(res.costo_usd)),  # única conversión a float: emisión de métricas
        ("GuardrailIntervenciones", MetricUnit.Count, intervenciones),
        ("BedrockThrottles", MetricUnit.Count, reintentos),
        ("VerificadorFallas", MetricUnit.Count, fallas),
    ]:
        metrics.add_metric(name=nombre, unit=unidad, value=valor)
    actualizar_estado(
        folio,
        estado="MEMO_GENERADO",
        memo_s3_key=clave_memo,
        memo_sha256=memo_sha,
        origen=res.estado.origen,
        costo_usd=str(res.costo_usd),
    )
    logger.info(
        "memo generado",
        extra={
            "origen": res.estado.origen,
            "llamadas": len(res.estado.llamadas),
            "costo_usd": str(res.costo_usd),
            "errores_finales": [e.regla for e in res.errores_finales],
        },
    )
    return {
        "memo_s3_key": clave_memo,
        "memo_sha256": memo_sha,
        "origen": res.estado.origen,
        "costo_usd": str(res.costo_usd),
        "llamadas": len(res.estado.llamadas),
    }
