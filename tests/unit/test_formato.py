from decimal import Decimal

import pytest

from memo_credito.dominio.errores import MontoInvalido
from memo_credito.dominio.formato import (
    formato_millones,
    formato_monto,
    formato_monto_corto,
    formato_pct,
    formato_razon,
    parsear_monto,
    redondear,
)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("$1,555,566.24", "1555566.24"),
        ("$0.05", "0.05"),
        ("-$5.00", "-5.00"),
        ("($1,000.10)", "-1000.10"),
        ("$999.99", "999.99"),
    ],
)
def test_parsear_monto_exacto(texto, esperado):
    assert parsear_monto(texto) == Decimal(esperado)
    assert isinstance(parsear_monto(texto), Decimal)


@pytest.mark.parametrize("texto", ["1555566.24", "$1.555,00", "$1,55.00", "$12.5", "$", "12 pesos", "$1,000.000"])
def test_parsear_monto_rechaza_formatos_ambiguos(texto):
    with pytest.raises(MontoInvalido):
        parsear_monto(texto)


def test_redondeo_half_up_exacto():
    assert redondear(Decimal("0.005")) == Decimal("0.01")
    assert redondear(Decimal("2.675")) == Decimal("2.68")  # con float daría 2.67
    assert redondear(Decimal("1.844999")) == Decimal("1.84")


def test_formatos():
    assert formato_monto(Decimal("1200000")) == "$1,200,000.00"
    assert formato_monto(Decimal("-63445.32")) == "-$63,445.32"
    assert formato_monto_corto(Decimal("1200000")) == "$1,200,000"
    assert formato_monto_corto(Decimal("1200000.50")) == "$1,200,000.50"
    assert formato_razon(Decimal("1.845")) == "1.85"
    assert formato_pct(Decimal("6.06")) == "6.06%"
    assert formato_millones(Decimal("19800000")) == "$19.8 millones"
    assert formato_millones(Decimal("1197000")) == "$1.2 millones"
