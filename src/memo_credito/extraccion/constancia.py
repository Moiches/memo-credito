"""Constancia de situación fiscal: pares etiqueta -> valor con etiquetas en lista blanca."""

from __future__ import annotations

import re
from datetime import date

from pydantic import SecretStr

from memo_credito.dominio.errores import FormatoNoSoportado
from memo_credito.dominio.modelos import ConstanciaFiscal, HallazgoSeguridad, Procedencia, ValorExtraido, ValorPii
from memo_credito.extraccion.pdf_util import DocumentoPdf
from memo_credito.extraccion.seguridad import DetectorInstrucciones

TITULO = "CONSTANCIA DE SITUACIÓN FISCAL"
ETIQUETAS = {
    "RFC": "rfc",
    "Denominación o razón social": "razon_social",
    "Régimen de capital": "regimen_capital",
    "Régimen fiscal": "regimen_fiscal",
    "Fecha de inicio de operaciones": "inicio_operaciones",
    "Estatus en el padrón": "estatus",
    "Domicilio fiscal": "domicilio_fiscal",
    "Actividad económica": "actividad_economica",
}
RE_EMISION = re.compile(r"Fecha de emisión:\s*(\d{4}-\d{2}-\d{2})")


def reconoce(doc: DocumentoPdf) -> bool:
    return doc.n_paginas > 0 and TITULO in doc.texto_pagina(1)


def parsear_constancia(
    doc: DocumentoPdf, nombre: str, sha256: str, detector: DetectorInstrucciones
) -> ConstanciaFiscal:
    if not reconoce(doc):
        raise FormatoNoSoportado("constancia no reconocida")
    valores: dict[str, tuple[str, int, int]] = {}
    emision: tuple[str, int, int] | None = None
    hallazgos: list[HallazgoSeguridad] = []
    for pagina in doc.paginas:
        for renglon, linea in enumerate(pagina.lineas, start=1):
            texto = linea.texto
            if pagina.es_pie(linea) or texto == TITULO:
                continue
            if (m := RE_EMISION.search(texto)) and emision is None:
                emision = (m.group(1), pagina.numero, renglon)
                continue
            for etiqueta, campo in ETIQUETAS.items():
                if texto.startswith(etiqueta + " ") and campo not in valores:
                    valores[campo] = (texto[len(etiqueta) + 1 :].strip(), pagina.numero, renglon)
                    break
            else:
                deteccion = detector.evaluar(texto)
                if deteccion.dispara:
                    hallazgos.append(
                        HallazgoSeguridad(
                            tipo="instruccion",
                            procedencia=Procedencia(
                                documento=nombre,
                                sha256=sha256,
                                pagina=pagina.numero,
                                renglon=renglon,
                                campo="linea_no_reconocida",
                            ),
                            detalle=deteccion.detalle,
                        )
                    )
    faltantes = [c for c in ETIQUETAS.values() if c not in valores]
    if faltantes or emision is None:
        raise FormatoNoSoportado(f"campos de constancia faltantes: {', '.join(faltantes) or 'fecha de emisión'}")

    def ve(campo: str) -> ValorExtraido[str]:
        texto, pag, ren = valores[campo]
        return ValorExtraido[str](
            valor=texto,
            texto_original=texto,
            procedencia=Procedencia(documento=nombre, sha256=sha256, pagina=pag, renglon=ren, campo=campo),
        )

    def vf(texto: str, pag: int, ren: int, campo: str) -> ValorExtraido[date]:
        return ValorExtraido[date](
            valor=date.fromisoformat(texto),
            texto_original=texto,
            procedencia=Procedencia(documento=nombre, sha256=sha256, pagina=pag, renglon=ren, campo=campo),
        )

    dom_texto, dom_pag, dom_ren = valores["domicilio_fiscal"]
    ocultos = [
        HallazgoSeguridad(
            tipo="texto_oculto",
            procedencia=Procedencia(documento=nombre, sha256=sha256, pagina=p, campo="texto_oculto"),
            detalle=d,
        )
        for p, d in doc.texto_oculto
    ]
    return ConstanciaFiscal(
        documento=nombre,
        sha256=sha256,
        rfc=ve("rfc"),
        razon_social=ve("razon_social"),
        regimen_capital=ve("regimen_capital"),
        regimen_fiscal=ve("regimen_fiscal"),
        inicio_operaciones=vf(*valores["inicio_operaciones"], campo="inicio_operaciones"),
        estatus=ve("estatus"),
        fecha_emision=vf(*emision, campo="fecha_emision"),
        actividad_economica=ve("actividad_economica"),
        domicilio_fiscal=ValorPii(
            valor=SecretStr(dom_texto),
            procedencia=Procedencia(
                documento=nombre, sha256=sha256, pagina=dom_pag, renglon=dom_ren, campo="domicilio_fiscal"
            ),
        ),
        paginas=doc.n_paginas,
        hallazgos=tuple(hallazgos + ocultos),
    )
