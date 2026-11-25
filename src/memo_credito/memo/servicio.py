"""Servicio de redacción: hechos -> grafo (o plantilla de respaldo) -> memo ensamblado y verificado.

Lo usan la CLI local y la Lambda `redactar_memo`. Si el modelo no está disponible (después de los reintentos),
sale la plantilla marcada "requiere revisión": el memo siempre se entrega.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from memo_credito.config import Config
from memo_credito.costos import costo_llamada
from memo_credito.dominio.errores import LLMErrorConfiguracion, LLMNoDisponible
from memo_credito.dominio.modelos import Analisis, Expediente
from memo_credito.memo.ensamblador import Metadatos, ensamblar
from memo_credito.memo.grafo import Subsegmento, _sin_subsegmento, construir_grafo, ejecutar
from memo_credito.memo.modelos import ErrorVerificacion, EstadoGrafo, HechosParaLLM, RespuestaLLM, SalidaLLM
from memo_credito.memo.plantilla import plantilla
from memo_credito.memo.verificador import verificar


@dataclass(frozen=True)
class ResultadoRedaccion:
    estado: EstadoGrafo
    memo: str
    errores_finales: tuple[ErrorVerificacion, ...]

    @property
    def costo_usd(self) -> Decimal:
        return sum((ll.costo_usd for ll in self.estado.llamadas), Decimal(0))

    @property
    def tokens(self) -> tuple[int, int]:
        return (
            sum(ll.respuesta.uso.entrada for ll in self.estado.llamadas),
            sum(ll.respuesta.uso.salida for ll in self.estado.llamadas),
        )


def redactar_memo(
    exp: Expediente,
    analisis: Analisis,
    hechos: HechosParaLLM,
    cliente: Any,
    cfg: Config,
    meta: Metadatos,
    canarios: tuple[str, ...] = (),
    forzar_plantilla: str | None = None,
    subsegmento: Subsegmento = _sin_subsegmento,
) -> ResultadoRedaccion:
    def memo_de(salida: SalidaLLM, origen: str, motivo: str | None = None) -> str:
        return ensamblar(exp, analisis, hechos, salida, meta, origen, motivo)

    def verificador(salida: SalidaLLM | None) -> tuple[ErrorVerificacion, ...]:
        memo = memo_de(salida, "llm") if salida is not None else None
        return tuple(verificar(salida, memo, hechos, canarios))

    def costeador(respuesta: RespuestaLLM) -> Decimal:
        return costo_llamada(respuesta.uso, respuesta.guardrail, respuesta.modelo, cfg.precios)

    if forzar_plantilla is None:
        try:
            grafo = construir_grafo(cliente, verificador, plantilla, costeador, cfg.llm.max_correcciones, subsegmento)
            estado = ejecutar(grafo, hechos)
        except (LLMNoDisponible, LLMErrorConfiguracion) as exc:
            estado = EstadoGrafo(
                hechos=hechos,
                borrador=plantilla(hechos),
                origen="plantilla",
                requiere_revision=True,
                motivo_plantilla=f"modelo no disponible: {exc.codigo}",
            )
    else:
        estado = EstadoGrafo(
            hechos=hechos,
            borrador=plantilla(hechos),
            origen="plantilla",
            requiere_revision=True,
            motivo_plantilla=forzar_plantilla,
        )
    assert estado.borrador is not None and estado.origen is not None
    memo = memo_de(estado.borrador, estado.origen, estado.motivo_plantilla)
    errores_finales = tuple(verificar(estado.borrador, memo, hechos, canarios))
    return ResultadoRedaccion(estado=estado, memo=memo, errores_finales=errores_finales)
