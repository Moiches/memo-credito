"""Paquete de auditoría: partes write-once (Object Lock en AWS) encadenadas por hash.

Principio: lo que no se puede borrar no debe contener datos personales. La proyección de la extracción
reemplaza PII y texto crudo por su sha256 y sigue siendo un JSON válido de ResultadoExtraccion, así que se
puede rehidratar para recalcular el análisis y comprobar que da exactamente lo mismo (reproducibilidad).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from memo_credito.auditoria.canonico import a_json, json_canonico, sha256_texto
from memo_credito.extraccion.expediente import ResultadoExtraccion

PARTES = ("00_entrada.json", "01_extraccion.json", "02_analisis.json", "03_redaccion.json", "04_revision.json")
MANIFIESTO = "99_manifiesto.json"


class Almacen(Protocol):
    def escribir(self, clave: str, contenido: bytes) -> None: ...

    def leer(self, clave: str) -> bytes: ...

    def existe(self, clave: str) -> bool: ...


class AlmacenLocal:
    """Almacén en disco con semántica write-once (igual que el bucket con Object Lock)."""

    def __init__(self, base: Path) -> None:
        self.base = base

    def escribir(self, clave: str, contenido: bytes) -> None:
        ruta = self.base / clave
        if ruta.exists():
            if ruta.read_bytes() == contenido:
                return
            raise FileExistsError(f"la parte {clave} ya existe y es inmutable")
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)

    def leer(self, clave: str) -> bytes:
        return (self.base / clave).read_bytes()

    def existe(self, clave: str) -> bool:
        return (self.base / clave).exists()


def _hash_pii(valor: str) -> str:
    return "sha256:" + sha256_texto(valor)


def proyectar_extraccion(r: ResultadoExtraccion) -> dict[str, Any]:
    """Proyección sin PII ni texto crudo, válida como ResultadoExtraccion."""
    d: dict[str, Any] = a_json(r)
    if r.estado_cuenta is not None:
        ec = d["estado_cuenta"]
        ec["cuenta"]["valor"] = _hash_pii(r.estado_cuenta.cuenta.valor.get_secret_value())
        for mov_d, mov in zip(ec["movimientos"], r.estado_cuenta.movimientos, strict=True):
            mov_d["concepto"] = _hash_pii(mov.concepto)
    if r.constancia is not None:
        d["constancia"]["domicilio_fiscal"]["valor"] = _hash_pii(r.constancia.domicilio_fiscal.valor.get_secret_value())
    if r.solicitud is not None:
        d["solicitud"]["contacto"] = None
    return d


def rehidratar(proyecciones: Sequence[dict[str, Any]]) -> list[ResultadoExtraccion]:
    return [ResultadoExtraccion.model_validate(p) for p in proyecciones]


def serializar(obj: Any) -> bytes:
    return (json_canonico(obj) + "\n").encode("utf-8")


def escribir_parte(almacen: Almacen, prefijo: str, nombre: str, contenido: Any) -> str:
    datos = serializar(contenido)
    almacen.escribir(f"{prefijo}/{nombre}", datos)
    return sha256_texto(datos.decode("utf-8"))


def construir_manifiesto(almacen: Almacen, prefijo: str) -> dict[str, Any]:
    cadena = sha256_texto("")
    partes = []
    for nombre in PARTES:
        clave = f"{prefijo}/{nombre}"
        if not almacen.existe(clave):
            continue
        h = sha256_texto(almacen.leer(clave).decode("utf-8"))
        cadena = sha256_texto(cadena + h)
        partes.append({"nombre": nombre, "sha256": h, "cadena": cadena})
    return {"version": "auditoria/v1", "partes": partes, "hash_final": cadena}


def escribir_manifiesto(almacen: Almacen, prefijo: str) -> dict[str, Any]:
    manifiesto = construir_manifiesto(almacen, prefijo)
    almacen.escribir(f"{prefijo}/{MANIFIESTO}", serializar(manifiesto))
    return manifiesto


def verificar_paquete(almacen: Almacen, prefijo: str) -> list[str]:
    """Recalcula hashes y cadena; devuelve la lista de problemas (vacía si el paquete está íntegro)."""
    guardado = json.loads(almacen.leer(f"{prefijo}/{MANIFIESTO}").decode("utf-8"))
    actual = construir_manifiesto(almacen, prefijo)
    problemas = []
    for g, a in zip(guardado["partes"], actual["partes"], strict=False):
        if g["sha256"] != a["sha256"]:
            problemas.append(f"{g['nombre']}: el contenido no coincide con su hash")
    if len(guardado["partes"]) != len(actual["partes"]):
        problemas.append("faltan o sobran partes respecto al manifiesto")
    if guardado["hash_final"] != actual["hash_final"] and not problemas:
        problemas.append("la cadena de hashes no coincide")
    return problemas
