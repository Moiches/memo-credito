"""Primera capa de guardrails: tipo, tamaño y contenido activo; texto con forma de instrucción."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from memo_credito.config import Limites
from memo_credito.dominio.errores import DocumentoRechazado
from memo_credito.dominio.modelos import TipoDocumento

RE_ESTADO_CUENTA = re.compile(r"^estado_cuenta_(\d{4})-(\d{2})\.pdf$")
RE_EEFF = re.compile(r"^estados_financieros_(\d{4})\.pdf$")
TOKENS_ACTIVOS = (b"/JavaScript", b"/JS ", b"/JS(", b"/JS<", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/XFA")


def tipo_por_nombre(nombre: str) -> TipoDocumento | None:
    if RE_ESTADO_CUENTA.match(nombre):
        return "estado_cuenta"
    if nombre == "constancia_situacion_fiscal.pdf":
        return "constancia"
    if RE_EEFF.match(nombre):
        return "estados_financieros"
    if nombre == "solicitud.json":
        return "solicitud"
    return None


def validar_archivo(nombre: str, datos: bytes, limites: Limites) -> TipoDocumento:
    """Valida tipo real, tamaño y contenido activo antes de parsear. Lanza DocumentoRechazado."""
    tipo = tipo_por_nombre(nombre)
    if tipo is None:
        raise DocumentoRechazado("nombre o tipo de documento no reconocido")
    if tipo == "solicitud":
        if len(datos) > limites.json_max_bytes:
            raise DocumentoRechazado(f"JSON de {len(datos)} bytes (máximo {limites.json_max_bytes})")
        try:
            datos.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentoRechazado("JSON no es UTF-8 válido") from exc
        return tipo
    if len(datos) > limites.pdf_max_bytes:
        raise DocumentoRechazado(f"PDF de {len(datos)} bytes (máximo {limites.pdf_max_bytes})")
    if not datos.startswith(b"%PDF-"):
        raise DocumentoRechazado("el archivo no es un PDF (magic bytes)")
    if b"/Encrypt" in datos:
        raise DocumentoRechazado("PDF cifrado")
    for token in TOKENS_ACTIVOS:
        if token in datos:
            raise DocumentoRechazado("PDF con contenido activo (JavaScript, acciones o adjuntos)")
    return tipo


def normalizar(texto: str) -> str:
    """Mayúsculas y sin acentos, para comparar patrones."""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return sin_acentos.upper()


@dataclass(frozen=True, slots=True)
class Deteccion:
    dispara: bool
    patrones: int
    estructural: bool

    @property
    def detalle(self) -> str:
        return f"patrones={self.patrones}; estructural={'si' if self.estructural else 'no'}"


class DetectorInstrucciones:
    """Texto con forma de instrucción: >= 2 patrones, o 1 patrón + señal estructural."""

    def __init__(self, patrones: Sequence[str], max_caracteres: int) -> None:
        self._patrones = [re.compile(p) for p in patrones]
        self._max = max_caracteres

    def evaluar(self, texto: str, n_lineas: int = 1) -> Deteccion:
        normal = normalizar(texto)
        coincidencias = sum(1 for p in self._patrones if p.search(normal))
        estructural = n_lineas > 1 or len(texto) > self._max
        dispara = coincidencias >= 2 or (coincidencias >= 1 and estructural)
        return Deteccion(dispara, coincidencias, estructural)
