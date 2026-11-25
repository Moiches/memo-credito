"""Indicadores del reto (definiciones_indicadores.md). "No calculable" es un estado con motivo, nunca un 0."""

from __future__ import annotations

from decimal import Decimal, localcontext

from memo_credito.config import Config
from memo_credito.dominio.formato import (
    MENOS,
    abrev_mes,
    formato_monto,
    formato_monto_corto,
    formato_razon,
    redondear,
)
from memo_credito.dominio.modelos import EstadoCuenta, Expediente, Indicador, Periodo
from memo_credito.validacion.periodos import etiqueta

PRECISION = 34


def pago_mensual(monto: Decimal, plazo: int, tasa_anual: Decimal) -> Decimal:
    """Cuota fija con amortización francesa: M * i / (1 - (1 + i)^-n), redondeada al final."""
    with localcontext() as ctx:
        ctx.prec = PRECISION
        i = tasa_anual / Decimal(12)
        valor = monto * i / (Decimal(1) - (Decimal(1) + i) ** (-plazo))
    return redondear(valor)


def _no_calculable(id_: str, nombre: str, motivo: str, formula: str, fuente: str) -> Indicador:
    return Indicador(
        id=id_,
        nombre=nombre,
        estado="no_calculable",
        valor=None,
        valor_texto="No calculable",
        motivo=motivo,
        formula=formula,
        entradas={},
        fuente_memo=fuente,
    )


def _texto_tasa(tasa_anual: Decimal) -> str:
    pct = tasa_anual * 100
    return f"{int(pct)}%" if pct == pct.to_integral_value() else f"{pct.normalize()}%"


def calcular_indicadores(
    exp: Expediente,
    usables: dict[str, EstadoCuenta],
    esperados: tuple[Periodo, ...],
    motivos_mes: dict[str, str],
    cfg: Config,
) -> tuple[Indicador, ...]:
    indicadores: list[Indicador] = []
    flujos: dict[str, Decimal] = {}
    for p in esperados:
        id_ = f"flujo_neto_{p.anio}_{p.mes:02d}"
        nombre = f"Flujo neto {etiqueta(p)}"
        ec = usables.get(p.clave)
        if ec is not None:
            ab, ca = ec.total_abonos.valor, ec.total_cargos.valor
            neto = ab - ca
            flujos[p.clave] = neto
            indicadores.append(
                Indicador(
                    id=id_,
                    nombre=nombre,
                    estado="calculado",
                    valor=neto,
                    valor_texto=formato_monto(neto),
                    motivo=None,
                    formula="total de abonos − total de cargos",
                    entradas={"abonos": ab, "cargos": ca},
                    fuente_memo=f"{ec.documento} (abonos {formato_monto(ab)} {MENOS} cargos {formato_monto(ca)})",
                )
            )
        else:
            motivo = {
                "faltante": f"falta estado_cuenta_{p.clave}.pdf",
                "duplicado": f"hay más de un estado de cuenta de {etiqueta(p)}",
                "no_concilia": f"el estado de cuenta de {etiqueta(p)} no concilia",
            }.get(motivos_mes.get(p.clave, "faltante"), f"sin estado de cuenta utilizable de {etiqueta(p)}")
            indicadores.append(
                _no_calculable(id_, nombre, motivo, "total de abonos − total de cargos", motivo[0].upper() + motivo[1:])
            )

    n = len(esperados)
    if len(flujos) == n and n > 0:
        with localcontext() as ctx:
            ctx.prec = PRECISION
            promedio = redondear(sum(flujos.values(), Decimal(0)) / Decimal(n))
        flujo_prom: Indicador = Indicador(
            id="flujo_promedio",
            nombre="Flujo promedio mensual",
            estado="calculado",
            valor=promedio,
            valor_texto=formato_monto(promedio),
            motivo=None,
            formula=f"promedio de los {n} flujos netos mensuales",
            entradas={k: v for k, v in flujos.items()},
            fuente_memo=f"Promedio de los {n} meses",
        )
    else:
        faltan = [abrev_mes(p.mes) + f" {p.anio}" for p in esperados if p.clave not in flujos]
        motivo = f"requiere los {n} meses (falta {', '.join(faltan)})"
        flujo_prom = _no_calculable(
            "flujo_promedio",
            "Flujo promedio mensual",
            motivo,
            f"promedio de los {n} flujos netos",
            motivo[0].upper() + motivo[1:],
        )
    indicadores.append(flujo_prom)

    cr = exp.solicitud.credito
    tasa = cfg.politica.tasa_anual_referencia
    if cr.monto_solicitado > 0 and cr.plazo_meses > 0:
        pago = pago_mensual(cr.monto_solicitado, cr.plazo_meses, tasa)
        pago_ind: Indicador = Indicador(
            id="pago_mensual",
            nombre="Pago mensual estimado",
            estado="calculado",
            valor=pago,
            valor_texto=formato_monto(pago),
            motivo=None,
            formula="M × i / (1 − (1 + i)^(−n))",
            entradas={"monto": cr.monto_solicitado, "plazo_meses": Decimal(cr.plazo_meses), "tasa_anual": tasa},
            fuente_memo=f"{formato_monto_corto(cr.monto_solicitado)} a {cr.plazo_meses} meses, tasa de referencia "
            f"{_texto_tasa(tasa)} anual",
        )
    else:
        pago_ind = _no_calculable(
            "pago_mensual",
            "Pago mensual estimado",
            "monto o plazo no válidos",
            "M × i / (1 − (1 + i)^(−n))",
            "Monto o plazo no válidos",
        )
    indicadores.append(pago_ind)

    if flujo_prom.valor is not None and pago_ind.valor is not None and pago_ind.valor > 0:
        with localcontext() as ctx:
            ctx.prec = PRECISION
            capacidad = redondear(flujo_prom.valor / pago_ind.valor)
        indicadores.append(
            Indicador(
                id="capacidad_pago",
                nombre="Capacidad de pago",
                estado="calculado",
                valor=capacidad,
                valor_texto=formato_razon(capacidad),
                motivo=None,
                formula="flujo promedio mensual / pago mensual estimado",
                entradas={"flujo_promedio": flujo_prom.valor, "pago_mensual": pago_ind.valor},
                fuente_memo=f"{formato_monto(flujo_prom.valor)} / {formato_monto(pago_ind.valor)}",
            )
        )
    else:
        indicadores.append(
            _no_calculable(
                "capacidad_pago",
                "Capacidad de pago",
                "depende del flujo promedio mensual",
                "flujo promedio mensual / pago mensual estimado",
                "Depende del flujo promedio mensual",
            )
        )

    ef = exp.estados_financieros
    if ef is not None and ef.partidas["capital_contable"].valor > 0:
        tp, cc = ef.partidas["total_pasivo"].valor, ef.partidas["capital_contable"].valor
        with localcontext() as ctx:
            ctx.prec = PRECISION
            apal = redondear(tp / cc)
        indicadores.append(
            Indicador(
                id="apalancamiento",
                nombre="Apalancamiento",
                estado="calculado",
                valor=apal,
                valor_texto=formato_razon(apal),
                motivo=None,
                formula="pasivo total / capital contable",
                entradas={"pasivo_total": tp, "capital_contable": cc},
                fuente_memo=f"Pasivo total {formato_monto(tp)} / capital contable {formato_monto(cc)} ({ef.documento})",
            )
        )
    else:
        motivo = "capital contable no positivo" if ef is not None else "faltan estados financieros"
        indicadores.append(
            _no_calculable(
                "apalancamiento",
                "Apalancamiento",
                motivo,
                "pasivo total / capital contable",
                motivo[0].upper() + motivo[1:],
            )
        )
    return tuple(indicadores)
