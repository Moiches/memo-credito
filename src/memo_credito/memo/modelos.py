"""Contratos del ciclo de redacción: hechos (entrada del LLM), salida estructurada y estado del grafo."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AlertaHecho(_M):
    codigo: str
    categoria: str
    severidad: str
    titulo: str


class IndicadorHecho(_M):
    estado: Literal["calculado", "no_calculable"]
    motivo: str | None = None


class HechosParaLLM(_M):
    """Contrato hechos/v1: lo ÚNICO que ve el LLM. Sin PII ni texto crudo de documentos."""

    contrato: Literal["hechos/v1"] = "hechos/v1"
    folio: str
    empresa: dict[str, str]
    credito: dict[str, str]
    cifras: dict[str, str]
    textos: dict[str, str]
    indicadores: dict[str, IndicadorHecho]
    hechos: dict[str, bool | str | None]
    alertas: tuple[AlertaHecho, ...]

    @property
    def referencias(self) -> set[str]:
        return set(self.cifras) | set(self.textos)


class Vineta(_M):
    texto: str = Field(max_length=2000)
    alertas_referidas: tuple[str, ...] = ()


class SalidaLLM(_M):
    resumen: str = Field(max_length=3000)
    observaciones: tuple[Vineta, ...]
    puntos_comite: tuple[Vineta, ...]


class ErrorVerificacion(_M):
    regla: str
    detalle: str
    ubicacion: str


class UsoTokens(_M):
    entrada: int = 0
    salida: int = 0
    cache_lectura: int = 0
    cache_escritura: int = 0


class ResultadoGuardrail(_M):
    intervino: bool = False
    origen: Literal["entrada", "salida"] | None = None
    politicas: tuple[str, ...] = ()
    unidades: dict[str, int] = {}


class PeticionRedaccion(_M):
    hechos: HechosParaLLM
    borrador_previo: SalidaLLM | None = None
    errores: tuple[ErrorVerificacion, ...] = ()


class RespuestaLLM(_M):
    salida: SalidaLLM | None
    texto_crudo: str
    stop_reason: str
    uso: UsoTokens = UsoTokens()
    guardrail: ResultadoGuardrail = ResultadoGuardrail()
    latencia_ms: int = 0
    reintentos: int = 0
    modelo: str
    peticion: dict[str, Any] = {}
    request_id: str | None = None


class RegistroLlamada(_M):
    intento: int
    tipo: Literal["redactar", "corregir"]
    respuesta: RespuestaLLM
    costo_usd: Decimal


class EstadoGrafo(_M):
    hechos: HechosParaLLM
    borrador: SalidaLLM | None = None
    errores: tuple[ErrorVerificacion, ...] = ()
    correcciones: int = 0
    llamadas: tuple[RegistroLlamada, ...] = ()
    origen: Literal["llm", "plantilla"] | None = None
    requiere_revision: bool = False
    motivo_plantilla: str | None = None
    errores_por_intento: tuple[tuple[ErrorVerificacion, ...], ...] = ()
