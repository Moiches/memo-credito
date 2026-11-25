"""Extracción por documento (lo que hace cada iteración del Map) y armado del expediente."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from memo_credito.config import Config
from memo_credito.dominio.errores import DocumentoRechazado, ErrorMemo, ExpedienteIncompleto, FormatoNoSoportado
from memo_credito.dominio.modelos import (
    ConstanciaFiscal,
    DocumentoRechazadoInfo,
    EstadoCuenta,
    EstadosFinancieros,
    Expediente,
    RefDocumento,
    Solicitud,
)
from memo_credito.extraccion import constancia, eeff, estado_cuenta
from memo_credito.extraccion.pdf_util import leer_pdf
from memo_credito.extraccion.seguridad import DetectorInstrucciones, validar_archivo
from memo_credito.extraccion.solicitud import parsear_solicitud


class ResultadoExtraccion(BaseModel):
    """Salida serializable de extraer un documento (se guarda en S3 trabajo/)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ref: RefDocumento | None = None
    rechazo: DocumentoRechazadoInfo | None = None
    estado_cuenta: EstadoCuenta | None = None
    constancia: ConstanciaFiscal | None = None
    estados_financieros: EstadosFinancieros | None = None
    solicitud: Solicitud | None = None


def sha256_de(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def detector_de(config: Config) -> DetectorInstrucciones:
    return DetectorInstrucciones(config.seguridad.patrones_instruccion, config.limites.max_caracteres_concepto)


def extraer_documento(nombre: str, datos: bytes, config: Config) -> ResultadoExtraccion:
    sha = sha256_de(datos)
    detector = detector_de(config)
    try:
        tipo = validar_archivo(nombre, datos, config.limites)
        if tipo == "solicitud":
            sol = parsear_solicitud(datos, nombre, sha, detector, config.limites)
            return ResultadoExtraccion(
                ref=RefDocumento(nombre=nombre, sha256=sha, bytes=len(datos), tipo=tipo), solicitud=sol
            )
        doc = leer_pdf(datos, config.limites.pdf_max_paginas)
        ref = RefDocumento(nombre=nombre, sha256=sha, bytes=len(datos), tipo=tipo, paginas=doc.n_paginas)
        if tipo == "estado_cuenta":
            return ResultadoExtraccion(
                ref=ref, estado_cuenta=estado_cuenta.parsear_estado_cuenta(doc, nombre, sha, detector)
            )
        if tipo == "constancia":
            return ResultadoExtraccion(ref=ref, constancia=constancia.parsear_constancia(doc, nombre, sha, detector))
        return ResultadoExtraccion(ref=ref, estados_financieros=eeff.parsear_eeff(doc, nombre, sha, detector))
    except (DocumentoRechazado, FormatoNoSoportado) as exc:
        motivo = f"{exc.codigo}: {exc.detalle}" if exc.detalle else exc.codigo
        return ResultadoExtraccion(rechazo=DocumentoRechazadoInfo(nombre=nombre, sha256=sha, motivo=motivo))


def armar_expediente(resultados: Iterable[ResultadoExtraccion]) -> Expediente:
    lista = list(resultados)
    solicitudes = [r.solicitud for r in lista if r.solicitud is not None]
    if len(solicitudes) != 1:
        raise ExpedienteIncompleto("se requiere exactamente una solicitud.json válida")
    constancias = [r.constancia for r in lista if r.constancia is not None]
    eeffs = [r.estados_financieros for r in lista if r.estados_financieros is not None]
    estados = sorted(
        (r.estado_cuenta for r in lista if r.estado_cuenta is not None), key=lambda e: (e.periodo.orden, e.documento)
    )
    return Expediente(
        solicitud=solicitudes[0],
        estados_cuenta=tuple(estados),
        constancia=constancias[0] if constancias else None,
        estados_financieros=eeffs[0] if eeffs else None,
        documentos=tuple(sorted((r.ref for r in lista if r.ref is not None), key=lambda d: d.nombre)),
        rechazados=tuple(sorted((r.rechazo for r in lista if r.rechazo is not None), key=lambda d: d.nombre)),
    )


def leer_carpeta(carpeta: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(carpeta.iterdir()) if p.is_file()}


def extraer_archivos(archivos: dict[str, bytes], config: Config) -> tuple[Expediente, Sequence[ResultadoExtraccion]]:
    if len(archivos) > config.limites.max_archivos:
        raise ExpedienteIncompleto(f"{len(archivos)} archivos (máximo {config.limites.max_archivos})")
    resultados = [extraer_documento(nombre, datos, config) for nombre, datos in sorted(archivos.items())]
    return armar_expediente(resultados), resultados


__all__ = [
    "ErrorMemo",
    "ResultadoExtraccion",
    "armar_expediente",
    "extraer_archivos",
    "extraer_documento",
    "leer_carpeta",
]
