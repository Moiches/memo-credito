"""Lectura de PDF a palabras/líneas con coordenadas, fuente y color; detección de texto oculto.

Las coordenadas son float (geometría del PDF); ningún monto pasa por float.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import pdfplumber
from pdfminer.pdftypes import resolve1

from memo_credito.dominio.errores import DocumentoRechazado

TOLERANCIA_LINEA = 2.0
MARGEN_PIE = 35.0


def normalizar_color(color: Any) -> tuple[float, ...]:
    if color is None:
        return (0.0,)
    if isinstance(color, (int, float)):
        return (float(color),)
    try:
        return tuple(float(x) for x in color)
    except TypeError:
        return (0.0,)


def es_blanco(color: tuple[float, ...]) -> bool:
    if len(color) == 4:  # CMYK
        return all(x <= 0.05 for x in color)
    return all(x >= 0.95 for x in color)


def luminancia(color: tuple[float, ...]) -> float:
    if len(color) == 1:
        return color[0]
    if len(color) == 3:
        return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
    if len(color) == 4:
        return (1 - color[3]) * (1 - max(color[:3]))
    return 1.0


@dataclass(frozen=True, slots=True)
class Palabra:
    texto: str
    x0: float
    x1: float
    top: float
    bottom: float
    fuente: str
    tamano: float
    color: tuple[float, ...]

    @property
    def negrita(self) -> bool:
        return "Bold" in self.fuente

    @property
    def cursiva(self) -> bool:
        return "Oblique" in self.fuente or "Italic" in self.fuente

    @property
    def blanca(self) -> bool:
        return es_blanco(self.color)


@dataclass(frozen=True, slots=True)
class Linea:
    pagina: int
    top: float
    palabras: tuple[Palabra, ...]

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palabras)

    @property
    def x0(self) -> float:
        return self.palabras[0].x0


@dataclass(frozen=True, slots=True)
class PaginaPdf:
    numero: int
    ancho: float
    alto: float
    lineas: tuple[Linea, ...]

    def es_pie(self, linea: Linea) -> bool:
        return linea.palabras[0].cursiva or linea.top > self.alto - MARGEN_PIE


@dataclass(frozen=True, slots=True)
class DocumentoPdf:
    paginas: tuple[PaginaPdf, ...]
    texto_oculto: tuple[tuple[int, str], ...]

    @property
    def n_paginas(self) -> int:
        return len(self.paginas)

    def texto_pagina(self, numero: int) -> str:
        return "\n".join(linea.texto for linea in self.paginas[numero - 1].lineas)


def agrupar_lineas(pagina: int, palabras: list[Palabra]) -> list[Linea]:
    ordenadas = sorted(palabras, key=lambda p: (round(p.top, 1), p.x0))
    grupos: list[list[Palabra]] = []
    for p in ordenadas:
        if grupos and abs(grupos[-1][0].top - p.top) <= TOLERANCIA_LINEA:
            grupos[-1].append(p)
        else:
            grupos.append([p])
    return [Linea(pagina, g[0].top, tuple(sorted(g, key=lambda p: p.x0))) for g in grupos]


def _rectangulos_oscuros(page: Any) -> list[tuple[float, float, float, float]]:
    oscuros = []
    for r in page.rects:
        if not r.get("fill", True):
            continue
        if luminancia(normalizar_color(r.get("non_stroking_color"))) < 0.5:
            oscuros.append((float(r["x0"]), float(r["x1"]), float(r["top"]), float(r["bottom"])))
    return oscuros


def _detectar_texto_oculto(numero: int, page: Any, palabras: list[Palabra]) -> list[tuple[int, str]]:
    oscuros = _rectangulos_oscuros(page)
    blancas_sin_fondo = 0
    diminutas = 0
    fuera = 0
    for p in palabras:
        cx, cy = (p.x0 + p.x1) / 2, (p.top + p.bottom) / 2
        if p.blanca and not any(x0 <= cx <= x1 and t <= cy <= b for x0, x1, t, b in oscuros):
            blancas_sin_fondo += 1
        if 0 < p.tamano < 4:
            diminutas += 1
        if p.x1 < 0 or p.x0 > float(page.width) or p.bottom < 0 or p.top > float(page.height):
            fuera += 1
    hallazgos = []
    if blancas_sin_fondo:
        hallazgos.append((numero, f"palabras_blancas_sin_fondo={blancas_sin_fondo}"))
    if diminutas:
        hallazgos.append((numero, f"palabras_fuente_menor_4pt={diminutas}"))
    if fuera:
        hallazgos.append((numero, f"palabras_fuera_de_pagina={fuera}"))
    return hallazgos


def _nombre(valor: Any) -> str:
    return str(getattr(valor, "name", valor))


def verificar_catalogo(pdf: Any) -> None:
    """Rechaza contenido activo declarado en el catálogo (acciones, JavaScript, adjuntos)."""
    catalogo = resolve1(pdf.doc.catalog) or {}
    if "AA" in catalogo:
        raise DocumentoRechazado("PDF con acciones automáticas (/AA)")
    nombres = resolve1(catalogo.get("Names")) or {}
    if isinstance(nombres, dict) and ({"JavaScript", "EmbeddedFiles"} & {str(k) for k in nombres}):
        raise DocumentoRechazado("PDF con JavaScript o archivos embebidos")
    accion = resolve1(catalogo.get("OpenAction"))
    if isinstance(accion, dict) and _nombre(resolve1(accion.get("S"))) in {"JavaScript", "Launch", "SubmitForm"}:
        raise DocumentoRechazado("PDF con acción de apertura activa")
    if "AcroForm" in catalogo:
        formulario = resolve1(catalogo.get("AcroForm")) or {}
        if isinstance(formulario, dict) and "XFA" in formulario:
            raise DocumentoRechazado("PDF con formulario XFA")


def leer_pdf(datos: bytes, max_paginas: int) -> DocumentoPdf:
    paginas: list[PaginaPdf] = []
    ocultos: list[tuple[int, str]] = []
    try:
        pdf_ctx = pdfplumber.open(io.BytesIO(datos))
    except Exception as exc:  # pdfminer lanza varios tipos; el mensaje no incluye contenido
        raise DocumentoRechazado(f"PDF ilegible o cifrado ({type(exc).__name__})") from exc
    with pdf_ctx as pdf:
        verificar_catalogo(pdf)
        if len(pdf.pages) > max_paginas:
            raise DocumentoRechazado(f"PDF con {len(pdf.pages)} páginas (máximo {max_paginas})")
        for numero, page in enumerate(pdf.pages, start=1):
            crudas = page.extract_words(extra_attrs=["fontname", "size", "non_stroking_color"])
            palabras = [
                Palabra(
                    texto=str(w["text"]),
                    x0=float(w["x0"]),
                    x1=float(w["x1"]),
                    top=float(w["top"]),
                    bottom=float(w["bottom"]),
                    fuente=str(w.get("fontname", "")),
                    tamano=float(w.get("size", 0.0)),
                    color=normalizar_color(w.get("non_stroking_color")),
                )
                for w in crudas
            ]
            paginas.append(
                PaginaPdf(numero, float(page.width), float(page.height), tuple(agrupar_lineas(numero, palabras)))
            )
            ocultos.extend(_detectar_texto_oculto(numero, page, palabras))
    return DocumentoPdf(tuple(paginas), tuple(ocultos))
