"""Análisis completo de un expediente: validaciones + indicadores + hechos + señales de riesgo.

Es una función pura: misma extracción y misma configuración => mismo análisis byte a byte (prueba de
reproducibilidad en tests/regresion).
"""

from __future__ import annotations

from memo_credito.calculos.derivados import calcular_derivados
from memo_credito.calculos.indicadores import calcular_indicadores
from memo_credito.config import Config
from memo_credito.dominio.alertas import ORDEN
from memo_credito.dominio.formato import formato_monto, formato_razon
from memo_credito.dominio.modelos import Alerta, Analisis, Expediente, HechosDerivados, Indicador
from memo_credito.validacion.reglas import alerta, validar


def alertas_de_riesgo(indicadores: tuple[Indicador, ...], derivados: HechosDerivados, cfg: Config) -> list[Alerta]:
    pol = cfg.politica
    por_id = {i.id: i for i in indicadores}
    alertas: list[Alerta] = []
    cap = por_id["capacidad_pago"].valor
    if cap is not None:
        if cap < pol.umbral_capacidad_minima:
            alertas.append(
                alerta(
                    "R01",
                    f"La capacidad de pago ({formato_razon(cap)}) es menor a "
                    f"{formato_razon(pol.umbral_capacidad_minima)}: "
                    "el flujo promedio no cubre la cuota estimada.",
                    cifras=("capacidad_pago", "umbral_capacidad_minima"),
                )
            )
        elif cap < pol.umbral_capacidad_holgura:
            alertas.append(
                alerta(
                    "R02",
                    f"La capacidad de pago ({formato_razon(cap)}) está por debajo de la holgura de referencia "
                    f"({formato_razon(pol.umbral_capacidad_holgura)}).",
                    cifras=("capacidad_pago", "umbral_capacidad_holgura"),
                )
            )
    apal = por_id["apalancamiento"].valor
    if apal is not None and apal >= pol.umbral_apalancamiento:
        alertas.append(
            alerta(
                "R03",
                f"El apalancamiento ({formato_razon(apal)}) es igual o mayor a la referencia "
                f"({formato_razon(pol.umbral_apalancamiento)}).",
                cifras=("apalancamiento", "umbral_apalancamiento"),
            )
        )
    if derivados.depositos_atipicos:
        detalle = "; ".join(
            f"{d.fecha.isoformat()} por {formato_monto(d.monto)} "
            f"({d.procedencia.documento}, pág. {d.procedencia.pagina})"
            for d in derivados.depositos_atipicos
        )
        alertas.append(
            alerta(
                "R04",
                f"{len(derivados.depositos_atipicos)} abonos superan {pol.factor_deposito_atipico} veces "
                f"la mediana de los abonos del periodo: {detalle}.",
                tuple(d.procedencia for d in derivados.depositos_atipicos),
            )
        )
    return alertas


def _clave_orden(a: Alerta) -> tuple[int, str, int, int]:
    ev = a.evidencia[0] if a.evidencia else None
    return (
        ORDEN.index(a.codigo),
        ev.documento if ev else "",
        (ev.pagina or 0) if ev else 0,
        (ev.renglon or 0) if ev else 0,
    )


def analizar(exp: Expediente, cfg: Config) -> Analisis:
    rv = validar(exp, cfg)
    indicadores = calcular_indicadores(exp, rv.usables, rv.esperados, rv.motivos_mes, cfg)
    derivados = calcular_derivados(exp, rv.usables, rv.esperados, indicadores, cfg)
    alertas = sorted(rv.alertas + alertas_de_riesgo(indicadores, derivados, cfg), key=_clave_orden)
    return Analisis(
        folio=exp.solicitud.folio,
        periodos_esperados=rv.esperados,
        periodos_presentes=tuple(sorted({e.periodo.clave for e in exp.estados_cuenta})),
        conciliaciones=tuple(rv.conciliaciones),
        validaciones=tuple(rv.validaciones),
        alertas=tuple(alertas),
        indicadores=indicadores,
        derivados=derivados,
    )
