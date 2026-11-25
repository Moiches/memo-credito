"""Dinero exacto y formato de cifras para el memo (sin depender del locale)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from memo_credito.dominio.errores import MontoInvalido

CENTAVO = Decimal("0.01")
MENOS = "−"  # signo menos tipográfico usado por el memo de ejemplo
GUION_RANGO = "–"  # "jul–sep"

MESES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}
MESES_ABREV = {
    1: "ene",
    2: "feb",
    3: "mar",
    4: "abr",
    5: "may",
    6: "jun",
    7: "jul",
    8: "ago",
    9: "sep",
    10: "oct",
    11: "nov",
    12: "dic",
}
NUMERO_MES = {nombre: numero for numero, nombre in MESES.items()}

_RE_MONTO = re.compile(r"^\$(\d{1,3}(?:,\d{3})*)\.(\d{2})$")


def parsear_monto(texto: str) -> Decimal:
    """Convierte "$1,234.56" (o "-$1,234.56" / "($1,234.56)") a Decimal exacto. Nunca usa float."""
    t = texto.strip()
    negativo = False
    if t.startswith("(") and t.endswith(")"):
        t, negativo = t[1:-1], True
    if t.startswith("-"):
        t, negativo = t[1:], True
    m = _RE_MONTO.match(t)
    if not m:
        raise MontoInvalido(f"formato de monto no reconocido ({len(texto)} caracteres)")
    valor = Decimal(f"{m.group(1).replace(',', '')}.{m.group(2)}")
    return -valor if negativo else valor


def es_monto(texto: str) -> bool:
    t = texto.strip().lstrip("(-").rstrip(")")
    return bool(_RE_MONTO.match(t))


def redondear(valor: Decimal, decimales: int = 2) -> Decimal:
    """Redondeo ROUND_HALF_UP al final del cálculo (regla del reto)."""
    return valor.quantize(Decimal(1).scaleb(-decimales), rounding=ROUND_HALF_UP)


def formato_monto(valor: Decimal) -> str:
    """$1,234,567.89"""
    q = redondear(valor)
    signo = "-" if q < 0 else ""
    entero, dec = f"{abs(q):.2f}".split(".")
    return f"{signo}${int(entero):,}.{dec}"


def formato_monto_corto(valor: Decimal) -> str:
    """$1,200,000 si no hay centavos (así lo usa el memo de ejemplo en la fuente del pago)."""
    q = redondear(valor)
    if q == q.to_integral_value():
        signo = "-" if q < 0 else ""
        return f"{signo}${int(abs(q)):,}"
    return formato_monto(q)


def formato_razon(valor: Decimal) -> str:
    """1.85"""
    return f"{redondear(valor):.2f}"


def formato_pct(valor: Decimal, decimales: int = 2) -> str:
    """6.06% (valor ya expresado en porcentaje)."""
    q = redondear(valor, decimales)
    return f"{q:.{decimales}f}%"


def formato_millones(valor: Decimal) -> str:
    """$19.8 millones (1 decimal, HALF_UP)."""
    q = redondear(valor / Decimal(1_000_000), 1)
    return f"${q:,.1f} millones"


def nombre_mes(mes: int) -> str:
    return MESES[mes]


def abrev_mes(mes: int) -> str:
    return MESES_ABREV[mes]
