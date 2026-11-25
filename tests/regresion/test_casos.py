"""Regresión de los 8 casos: extracción, conciliación, indicadores exactos, alertas exactas y hechos derivados."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.conftest import FOLIOS


def _d(valor: str | None) -> Decimal | None:
    return None if valor is None else Decimal(valor)


@pytest.mark.parametrize("folio", FOLIOS)
def test_extraccion(folio, expedientes, casos):
    exp, caso = expedientes[folio], casos[folio]
    assert [len(e.movimientos) for e in exp.estados_cuenta] == caso["movimientos"]
    assert [e.paginas for e in exp.estados_cuenta] == caso["paginas"]
    assert exp.rechazados == ()


@pytest.mark.parametrize("folio", FOLIOS)
def test_conciliacion_al_centavo(folio, analisis):
    assert all(c.ok and c.fechas_ok for c in analisis[folio].conciliaciones)


@pytest.mark.parametrize("folio", FOLIOS)
def test_indicadores_exactos(folio, analisis, casos):
    a, caso = analisis[folio], casos[folio]
    for clave, esperado in caso["flujos"].items():
        anio, mes = clave.split("-")
        ind = a.indicador(f"flujo_neto_{anio}_{mes}")
        assert ind.valor == _d(esperado), clave
        assert (ind.estado == "no_calculable") == (esperado is None)
    for id_ in ("flujo_promedio", "pago_mensual", "capacidad_pago", "apalancamiento"):
        ind = a.indicador(id_)
        assert ind.valor == _d(caso[id_]), id_
        if caso[id_] is None:
            assert ind.estado == "no_calculable" and ind.motivo, id_


@pytest.mark.parametrize("folio", FOLIOS)
def test_alertas_todas_y_ninguna_de_mas(folio, analisis, casos):
    assert sorted(analisis[folio].codigos_alerta) == sorted(casos[folio]["alertas"])


@pytest.mark.parametrize("folio", FOLIOS)
def test_hechos_derivados(folio, analisis, casos):
    d, esperado = analisis[folio].derivados, casos[folio]["derivados"]
    assert d.antiguedad_anios == esperado["antiguedad_anios"]
    assert d.monto_pct_ventas == _d(esperado["monto_pct_ventas"])
    assert d.pct_abonos_efectivo == _d(esperado["pct_abonos_efectivo"])
    assert (d.mes_flujo_mas_alto.clave if d.mes_flujo_mas_alto else None) == esperado["mes_flujo_mas_alto"]
    assert (d.mes_flujo_mas_bajo.clave if d.mes_flujo_mas_bajo else None) == esperado["mes_flujo_mas_bajo"]
    if d.depositos_atipicos is not None:
        assert d.depositos_atipicos == ()


def test_sol0003_rfc_literal_sin_correccion(expedientes, casos):
    assert expedientes["SOL-0003"].estados_financieros.rfc.valor == casos["SOL-0003"]["rfc_eeff_literal"]


def test_sol0004_periodos_explicitos(analisis):
    a = analisis["SOL-0004"]
    assert [p.clave for p in a.periodos_esperados] == ["2026-07", "2026-08", "2026-09"]
    assert "2026-08" not in a.periodos_presentes
    assert not any(al.codigo == "A07" for al in a.alertas)  # el encadenamiento jul→sep no aplica (no son consecutivos)


def test_sol0001_tabla_identica_al_ejemplo(analisis):
    filas = {i.nombre: (i.valor_texto, i.fuente_memo) for i in analisis["SOL-0001"].indicadores}
    assert filas["Flujo neto jul 2026"] == (
        "$114,872.99",
        "estado_cuenta_2026-07.pdf (abonos $1,555,566.24 − cargos $1,440,693.25)",
    )
    assert filas["Pago mensual estimado"] == ("$63,445.32", "$1,200,000 a 24 meses, tasa de referencia 24% anual")
    assert filas["Capacidad de pago"] == ("1.85", "$117,373.84 / $63,445.32")
    assert filas["Apalancamiento"] == (
        "0.79",
        "Pasivo total $3,000,000.00 / capital contable $3,800,000.00 (estados_financieros_2025.pdf)",
    )
