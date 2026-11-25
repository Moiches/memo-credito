"""solicitud.json: lista blanca de campos; los no esperados se registran SOLO por nombre (nunca su valor)."""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from memo_credito.config import Limites
from memo_credito.dominio.errores import DocumentoRechazado
from memo_credito.dominio.modelos import HallazgoSeguridad, Procedencia, Solicitud
from memo_credito.extraccion.seguridad import DetectorInstrucciones

ESPERADOS: dict[str, set[str]] = {
    "": {"folio", "fecha_solicitud", "solicitante", "credito", "contacto", "documentos"},
    "solicitante": {"razon_social", "rfc"},
    "credito": {"monto_solicitado", "plazo_meses", "moneda", "destino"},
    "contacto": {"nombre", "puesto", "correo", "telefono_oficina"},
}
CAMPOS_LIBRES = (("credito", "destino"), ("solicitante", "razon_social"))


def _cargar(datos: bytes) -> dict[str, Any]:
    try:
        raw = json.loads(datos.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentoRechazado("solicitud.json no es JSON válido") from exc
    if not isinstance(raw, dict):
        raise DocumentoRechazado("solicitud.json debe ser un objeto")
    return raw


def campos_no_esperados(raw: dict[str, Any]) -> tuple[str, ...]:
    extras = [k for k in raw if k not in ESPERADOS[""]]
    for seccion in ("solicitante", "credito", "contacto"):
        valor = raw.get(seccion)
        if isinstance(valor, dict):
            extras += [f"{seccion}.{k}" for k in valor if k not in ESPERADOS[seccion]]
    return tuple(sorted(extras))


def parsear_solicitud(
    datos: bytes, nombre: str, sha256: str, detector: DetectorInstrucciones, limites: Limites
) -> Solicitud:
    raw = _cargar(datos)
    hallazgos: list[HallazgoSeguridad] = []
    for seccion, campo in CAMPOS_LIBRES:
        texto = raw.get(seccion, {}).get(campo) if isinstance(raw.get(seccion), dict) else None
        if not isinstance(texto, str):
            continue
        deteccion = detector.evaluar(texto)
        if deteccion.dispara or len(texto) > limites.texto_libre_max:
            detalle = deteccion.detalle if deteccion.dispara else f"longitud={len(texto)} > {limites.texto_libre_max}"
            hallazgos.append(
                HallazgoSeguridad(
                    tipo="instruccion",
                    procedencia=Procedencia(documento=nombre, sha256=sha256, campo=f"{seccion}.{campo}"),
                    detalle=detalle,
                )
            )
    filtrado = {k: raw[k] for k in ESPERADOS[""] if k in raw}
    credito = filtrado.get("credito")
    if isinstance(credito, dict) and isinstance(credito.get("monto_solicitado"), (int, Decimal)):
        credito = {**credito, "monto_solicitado": Decimal(str(credito["monto_solicitado"]))}
        filtrado["credito"] = credito
    try:
        return Solicitud.model_validate(
            {
                **filtrado,
                "documento": nombre,
                "sha256": sha256,
                "campos_no_esperados": campos_no_esperados(raw),
                "hallazgos": tuple(hallazgos),
            }
        )
    except ValidationError as exc:
        rutas = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise DocumentoRechazado(f"solicitud.json no cumple el esquema en: {', '.join(rutas)}") from None


def _normalizar(texto: str) -> str:
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return " ".join(sin_acentos.casefold().split())


def canarios(datos: bytes) -> tuple[str, ...]:
    """Valores PII de la solicitud (contacto y bloques no esperados). Solo viven en memoria: el verificador
    confirma que ninguno aparece en el prompt, el memo, los logs ni la auditoría."""
    raw = _cargar(datos)
    valores: list[str] = []

    def recolectar(nodo: Any) -> None:
        if isinstance(nodo, dict):
            for v in nodo.values():
                recolectar(v)
        elif isinstance(nodo, list):
            for v in nodo:
                recolectar(v)
        elif isinstance(nodo, str) and len(nodo.strip()) >= 5:
            valores.append(nodo.strip())

    if isinstance(raw.get("contacto"), dict):
        recolectar({k: v for k, v in raw["contacto"].items() if k != "puesto"})
    for clave in raw:
        if clave not in ESPERADOS[""]:
            recolectar(raw[clave])
    salida: set[str] = set()
    for v in valores:
        salida.add(_normalizar(v))
        digitos = "".join(ch for ch in v if ch.isdigit())
        if len(digitos) >= 8:
            salida.add(digitos)
    return tuple(sorted(salida))
