"""Periodos esperados: los N meses calendario completos anteriores al mes de la solicitud."""

from __future__ import annotations

from datetime import date

from memo_credito.dominio.formato import GUION_RANGO, abrev_mes
from memo_credito.dominio.modelos import Periodo


def periodos_esperados(fecha_solicitud: date, n: int = 3) -> tuple[Periodo, ...]:
    anio, mes = fecha_solicitud.year, fecha_solicitud.month
    periodos = []
    for _ in range(n):
        mes -= 1
        if mes == 0:
            anio, mes = anio - 1, 12
        periodos.append(Periodo(anio=anio, mes=mes))
    return tuple(sorted(periodos, key=lambda p: p.orden))


def etiqueta(p: Periodo) -> str:
    """ "jul 2026" """
    return f"{abrev_mes(p.mes)} {p.anio}"


def rango(periodos: tuple[Periodo, ...], presentes: set[str]) -> str:
    """ "jul–sep 2026" si están todos; "jul y sep 2026 (falta ago)" si no."""
    if not periodos:
        return ""
    anio = periodos[-1].anio
    mismos_anio = all(p.anio == anio for p in periodos)
    if all(p.clave in presentes for p in periodos):
        if mismos_anio:
            return f"{abrev_mes(periodos[0].mes)}{GUION_RANGO}{abrev_mes(periodos[-1].mes)} {anio}"
        return f"{etiqueta(periodos[0])}{GUION_RANGO}{etiqueta(periodos[-1])}"
    con = [p for p in periodos if p.clave in presentes]
    sin = [p for p in periodos if p.clave not in presentes]
    nombres = [abrev_mes(p.mes) for p in con]
    texto = (
        (", ".join(nombres[:-1]) + " y " + nombres[-1]) if len(nombres) > 1 else (nombres[0] if nombres else "ninguno")
    )
    faltan = ", ".join(abrev_mes(p.mes) for p in sin)
    return f"{texto} {anio} (falta {faltan})"
