"""Adaptador Bedrock Converse (boto3 directo, sin abstracciones de LangChain).

- Structured outputs (outputConfig.textFormat json_schema) y effort (outputConfig.effort).
- Guardrail en cada llamada (guardrailConfig con trace) cuando hay id configurado; IAM lo vuelve obligatorio en AWS.
- Sin temperature/top_p/top_k: los modelos Claude recientes no los aceptan (ADR-0007).
- La petición exacta y la respuesta cruda se guardan para el paquete de auditoría.
- Credenciales: rol de la Lambda en AWS; en local, la cadena por defecto o AWS_BEARER_TOKEN_BEDROCK (corto plazo).
"""

from __future__ import annotations

import json
import time
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from pydantic import ValidationError

from memo_credito.auditoria.canonico import json_canonico
from memo_credito.config import Config
from memo_credito.dominio.errores import LLMErrorConfiguracion, LLMNoDisponible
from memo_credito.llm.puerto import cargar_esquema_texto, cargar_prompt
from memo_credito.memo.modelos import (
    PeticionRedaccion,
    RespuestaLLM,
    ResultadoGuardrail,
    SalidaLLM,
    UsoTokens,
)

REINTENTABLES = {
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "InternalServerException",
    "ModelNotReadyException",
    "TooManyRequestsException",
}
_POLITICAS = {
    "topicPolicy": ("topics",),
    "contentPolicy": ("filters",),
    "sensitiveInformationPolicy": ("piiEntities", "regexes"),
    "wordPolicy": ("customWords", "managedWordLists"),
}


def cliente_bedrock(region: str) -> Any:
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=BotoConfig(connect_timeout=5, read_timeout=60, retries={"mode": "adaptive", "max_attempts": 3}),
    )


def parsear_guardrail(traza: dict[str, Any] | None, stop_reason: str) -> ResultadoGuardrail:
    if not traza:
        return ResultadoGuardrail(intervino=stop_reason == "guardrail_intervened")
    unidades: dict[str, int] = {}
    politicas: set[str] = set()
    bloqueo_entrada = False

    def revisar(evaluacion: dict[str, Any], es_entrada: bool) -> None:
        nonlocal bloqueo_entrada
        uso = evaluacion.get("invocationMetrics", {}).get("usage", {})
        for clave, valor in uso.items():
            if isinstance(valor, int):
                unidades[clave] = unidades.get(clave, 0) + valor
        for politica, listas in _POLITICAS.items():
            for lista in listas:
                for item in evaluacion.get(politica, {}).get(lista, []) or []:
                    if item.get("action") in ("BLOCKED", "ANONYMIZED") and item.get("detected", True):
                        politicas.add(f"{politica}:{item.get('type') or item.get('name') or 'regla'}")
                        bloqueo_entrada = bloqueo_entrada or es_entrada

    for evaluacion in (traza.get("inputAssessment") or {}).values():
        revisar(evaluacion, True)
    for evaluaciones in (traza.get("outputAssessments") or {}).values():
        for evaluacion in evaluaciones:
            revisar(evaluacion, False)
    intervino = stop_reason == "guardrail_intervened"
    origen = ("entrada" if bloqueo_entrada else "salida") if intervino else None
    return ResultadoGuardrail(intervino=intervino, origen=origen, politicas=tuple(sorted(politicas)), unidades=unidades)


class ClienteBedrock:
    modo = "bedrock"

    def __init__(
        self,
        cfg: Config,
        modelo_invocacion: str | None = None,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        cliente: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.modelo = cfg.llm.modelo_id  # id para precios y auditoría
        self.modelo_invocacion = modelo_invocacion or cfg.llm.modelo_id  # p. ej. ARN del perfil de aplicación
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version
        self.prompt = cargar_prompt(cfg)
        self.esquema = cargar_esquema_texto(cfg)
        self._cliente = cliente or cliente_bedrock(cfg.llm.region)

    def construir_peticion(self, peticion: PeticionRedaccion) -> dict[str, Any]:
        texto = f"<hechos>{json_canonico(peticion.hechos)}</hechos>"
        if peticion.errores:
            borrador = json_canonico(peticion.borrador_previo) if peticion.borrador_previo else "null"
            errores = json_canonico([e.model_dump() for e in peticion.errores])
            texto += f"\n<borrador_anterior>{borrador}</borrador_anterior>\n<errores_verificacion>{errores}</errores_verificacion>"
        salida: dict[str, Any] = {
            "textFormat": {
                "type": "json_schema",
                "structure": {"jsonSchema": {"schema": json.dumps(json.loads(self.esquema)), "name": "memo_v1"}},
            }
        }
        if self.cfg.llm.effort:
            salida["effort"] = self.cfg.llm.effort
        req: dict[str, Any] = {
            "modelId": self.modelo_invocacion,
            "system": [{"text": self.prompt}],
            "messages": [{"role": "user", "content": [{"text": texto}]}],
            "inferenceConfig": {"maxTokens": self.cfg.llm.max_tokens},
            "outputConfig": salida,
        }
        if self.guardrail_id:
            req["guardrailConfig"] = {
                "guardrailIdentifier": self.guardrail_id,
                "guardrailVersion": self.guardrail_version or "DRAFT",
                "trace": "enabled",
            }
        return req

    def redactar(self, peticion: PeticionRedaccion) -> RespuestaLLM:
        req = self.construir_peticion(peticion)
        inicio = time.perf_counter()
        try:
            resp = self._cliente.converse(**req)
        except ClientError as exc:
            codigo = exc.response.get("Error", {}).get("Code", "ClientError")
            if codigo in REINTENTABLES:
                raise LLMNoDisponible(codigo) from None
            raise LLMErrorConfiguracion(codigo) from None
        except (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError) as exc:
            raise LLMNoDisponible(type(exc).__name__) from None
        latencia = int((time.perf_counter() - inicio) * 1000)
        contenido = resp.get("output", {}).get("message", {}).get("content", [])
        texto = "".join(c.get("text", "") for c in contenido if isinstance(c, dict))
        stop = str(resp.get("stopReason", ""))
        salida = None
        if stop == "end_turn":
            try:
                salida = SalidaLLM.model_validate_json(texto)
            except ValidationError:
                salida = None
        u = resp.get("usage", {})
        meta = resp.get("ResponseMetadata", {})
        return RespuestaLLM(
            salida=salida,
            texto_crudo=texto,
            stop_reason=stop,
            uso=UsoTokens(
                entrada=int(u.get("inputTokens", 0)),
                salida=int(u.get("outputTokens", 0)),
                cache_lectura=int(u.get("cacheReadInputTokens", 0)),
                cache_escritura=int(u.get("cacheWriteInputTokens", 0)),
            ),
            guardrail=parsear_guardrail(resp.get("trace", {}).get("guardrail"), stop),
            latencia_ms=latencia,
            reintentos=int(meta.get("RetryAttempts", 0)),
            modelo=self.modelo,
            peticion=req,
            request_id=meta.get("RequestId"),
        )
