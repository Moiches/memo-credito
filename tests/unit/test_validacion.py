from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from memo_credito.calculos.derivados import anios_completos
from memo_credito.calculos.indicadores import pago_mensual
from memo_credito.validacion.periodos import periodos_esperados, rango
from memo_credito.validacion.reglas import conciliar, rfc_formato_valido


def test_periodos_esperados():
    assert [p.clave for p in periodos_esperados(date(2026, 10, 10), 3)] == ["2026-07", "2026-08", "2026-09"]
    assert [p.clave for p in periodos_esperados(date(2026, 1, 15), 3)] == ["2025-10", "2025-11", "2025-12"]


def test_rango_de_periodos():
    ps = periodos_esperados(date(2026, 10, 10), 3)
    assert rango(ps, {"2026-07", "2026-08", "2026-09"}) == "jul–sep 2026"
    assert rango(ps, {"2026-07", "2026-09"}) == "jul y sep 2026 (falta ago)"


@pytest.mark.parametrize(
    ("rfc", "valido"),
    [
        ("ASP170418QK2", True),
        ("TCG150817KP7", True),
        ("AVA160229LC1", True),
        ("GODE561231GR8", True),
        ("TCG159931KP2", False),
        ("ABC", False),
        ("asp170418qk2", False),
        ("ASP170418QK", False),
    ],
)
def test_rfc_formato_sin_digito_verificador(rfc, valido):
    assert rfc_formato_valido(rfc) is valido


def test_antiguedad_con_29_de_febrero():
    nacio = date(2016, 2, 29)
    assert anios_completos(nacio, date(2026, 10, 15)) == 10
    assert anios_completos(nacio, date(2026, 2, 28)) == 9
    assert anios_completos(nacio, date(2026, 3, 1)) == 10


def test_pago_mensual_amortizacion_francesa():
    assert pago_mensual(Decimal("1200000"), 24, Decimal("0.24")) == Decimal("63445.32")
    assert pago_mensual(Decimal("3000000"), 36, Decimal("0.24")) == Decimal("117698.56")


def test_conciliacion_detecta_un_centavo(expedientes):
    ec = expedientes["SOL-0001"].estados_cuenta[0]
    alterado = ec.model_copy(
        update={"total_abonos": ec.total_abonos.model_copy(update={"valor": ec.total_abonos.valor + Decimal("0.01")})}
    )
    c = conciliar(alterado)
    assert not c.ok and not c.suma_abonos_ok and c.diferencias
    assert conciliar(ec).ok
