"""Costo por llamada y por memo a partir de tokens y unidades de guardrail (precios en config con fuente y fecha)."""

from __future__ import annotations

from decimal import Decimal

from memo_credito.config import Precios
from memo_credito.dominio.formato import redondear
from memo_credito.memo.modelos import ResultadoGuardrail, UsoTokens

MILLON = Decimal(1_000_000)
MIL = Decimal(1_000)


def costo_llamada(uso: UsoTokens, guardrail: ResultadoGuardrail, modelo: str, precios: Precios) -> Decimal:
    costo = Decimal(0)
    p = precios.modelos.get(modelo)
    if p is not None:
        costo += (
            uso.entrada * p.entrada_mtok
            + uso.salida * p.salida_mtok
            + uso.cache_lectura * p.cache_lectura_mtok
            + uso.cache_escritura * p.cache_escritura_mtok
        ) / MILLON
    g = precios.guardrail_por_1k_unidades
    u = guardrail.unidades
    costo += (
        u.get("contentPolicyUnits", 0) * g.get("contenido", Decimal(0))
        + u.get("topicPolicyUnits", 0) * g.get("temas", Decimal(0))
        + u.get("sensitiveInformationPolicyUnits", 0) * g.get("pii", Decimal(0))
    ) / MIL
    return redondear(costo, 6)
