"""Hechos derivados que recibe el LLM (como texto ya formateado) y la plantilla."""

from __future__ import annotations

import statistics
from datetime import date
from decimal import Decimal, localcontext

from memo_credito.config import Config
from memo_credito.dominio.formato import redondear
from memo_credito.dominio.modelos import DepositoAtipico, EstadoCuenta, Expediente, HechosDerivados, Indicador, Periodo


def anios_completos(desde: date, hasta: date) -> int:
    """Años completos comparando (mes, día): funciona con 29 de febrero (hallazgo H4)."""
    return hasta.year - desde.year - ((hasta.month, hasta.day) < (desde.month, desde.day))


def calcular_derivados(
    exp: Expediente,
    usables: dict[str, EstadoCuenta],
    esperados: tuple[Periodo, ...],
    indicadores: tuple[Indicador, ...],
    cfg: Config,
) -> HechosDerivados:
    sol = exp.solicitud
    cons, ef = exp.constancia, exp.estados_financieros
    completos = len(usables) == len(esperados) and len(esperados) > 0
    antiguedad = anios_completos(cons.inicio_operaciones.valor, sol.fecha_solicitud) if cons else None
    ventas = ef.partidas["ventas_netas"].valor if ef else None
    utilidad = ef.partidas["utilidad_neta"].valor if ef else None
    pct_ventas = None
    if ventas is not None and ventas > 0:
        with localcontext() as ctx:
            ctx.prec = 34
            pct_ventas = redondear(sol.credito.monto_solicitado / ventas * 100)

    flujos = {i.id: i for i in indicadores if i.id.startswith("flujo_neto_")}
    alto = bajo = None
    positivos = None
    if completos:
        valores = [(flujos[f"flujo_neto_{p.anio}_{p.mes:02d}"].valor, p) for p in esperados]
        valores_ok = [(v, p) for v, p in valores if v is not None]
        alto = max(valores_ok, key=lambda t: (t[0], t[1].orden))[1]
        bajo = min(valores_ok, key=lambda t: (t[0], -t[1].orden))[1]
        positivos = all(v > 0 for v, _ in valores_ok)

    pct_efectivo = None
    atipicos: tuple[DepositoAtipico, ...] | None = None
    if completos:
        estados = [usables[p.clave] for p in esperados]
        total_abonos = sum((e.total_abonos.valor for e in estados), Decimal(0))
        efectivo = sum(
            (m.abono for e in estados for m in e.movimientos if m.abono is not None and m.clasificacion == "efectivo"),
            Decimal(0),
        )
        if total_abonos > 0:
            with localcontext() as ctx:
                ctx.prec = 34
                pct_efectivo = redondear(efectivo / total_abonos * 100)
        abonos = [m for e in estados for m in e.movimientos if m.abono is not None]
        if abonos:
            mediana = Decimal(statistics.median([m.abono for m in abonos if m.abono is not None]))
            umbral = mediana * cfg.politica.factor_deposito_atipico
            atipicos = tuple(
                DepositoAtipico(fecha=m.fecha, monto=m.abono, procedencia=m.procedencia)
                for m in abonos
                if m.abono is not None and m.abono > umbral
            )
        else:
            atipicos = ()

    return HechosDerivados(
        antiguedad_anios=antiguedad,
        ventas_netas=ventas,
        utilidad_neta=utilidad,
        monto_pct_ventas=pct_ventas,
        mes_flujo_mas_alto=alto,
        mes_flujo_mas_bajo=bajo,
        flujos_positivos=positivos,
        pct_abonos_efectivo=pct_efectivo,
        depositos_atipicos=atipicos,
        eeff_dictaminados=ef.dictaminados if ef else None,
        constancia_estatus=cons.estatus.valor if cons else None,
        constancia_dias=(sol.fecha_solicitud - cons.fecha_emision.valor).days if cons else None,
    )
