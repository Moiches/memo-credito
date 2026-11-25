"""Estados financieros: partidas por etiqueta exacta y monto al final de la línea."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from memo_credito.dominio.errores import FormatoNoSoportado
from memo_credito.dominio.formato import NUMERO_MES, es_monto, parsear_monto
from memo_credito.dominio.modelos import (
    PARTIDAS_BALANCE,
    PARTIDAS_RESULTADOS,
    EstadosFinancieros,
    HallazgoSeguridad,
    Procedencia,
    ValorExtraido,
)
from memo_credito.extraccion.pdf_util import DocumentoPdf
from memo_credito.extraccion.seguridad import DetectorInstrucciones

ETIQUETAS = {
    "Activo circulante": "activo_circulante",
    "Activo fijo neto": "activo_fijo_neto",
    "Total activo": "total_activo",
    "Pasivo a corto plazo": "pasivo_corto_plazo",
    "Pasivo a largo plazo": "pasivo_largo_plazo",
    "Total pasivo": "total_pasivo",
    "Capital contable": "capital_contable",
    "Total pasivo + capital": "total_pasivo_capital",
    "Ventas netas": "ventas_netas",
    "Costo de ventas": "costo_ventas",
    "Utilidad bruta": "utilidad_bruta",
    "Gastos de operación": "gastos_operacion",
    "Utilidad de operación": "utilidad_operacion",
    "Gastos financieros": "gastos_financieros",
    "Utilidad antes de impuestos": "utilidad_antes_impuestos",
    "ISR": "isr",
    "Utilidad neta": "utilidad_neta",
}
LINEAS_CONOCIDAS = ("Balance general", "Concepto Importe (MXN)", "Estado de resultados del ejercicio")
RE_RFC = re.compile(r"RFC:\s*(\S+)")
RE_CIERRE = re.compile(r"al (\d{1,2}) de ([a-záéíóú]+) de (\d{4})", re.IGNORECASE)


def reconoce(doc: DocumentoPdf) -> bool:
    return doc.n_paginas > 0 and "Estados financieros al" in doc.texto_pagina(1)


def parsear_eeff(doc: DocumentoPdf, nombre: str, sha256: str, detector: DetectorInstrucciones) -> EstadosFinancieros:
    if not reconoce(doc):
        raise FormatoNoSoportado("estados financieros no reconocidos")

    def proc(campo: str, pag: int, ren: int) -> Procedencia:
        return Procedencia(documento=nombre, sha256=sha256, pagina=pag, renglon=ren, campo=campo)

    razon: ValorExtraido[str] | None = None
    rfc: ValorExtraido[str] | None = None
    cierre: date | None = None
    leyenda = ""
    partidas: dict[str, ValorExtraido[Decimal]] = {}
    hallazgos: list[HallazgoSeguridad] = []
    for pagina in doc.paginas:
        for renglon, linea in enumerate(pagina.lineas, start=1):
            texto = linea.texto
            if pagina.es_pie(linea):
                continue
            if razon is None:
                razon = ValorExtraido[str](
                    valor=texto, texto_original=texto, procedencia=proc("razon_social", pagina.numero, renglon)
                )
                continue
            if rfc is None and (m := RE_RFC.search(texto)):
                rfc = ValorExtraido[str](
                    valor=m.group(1), texto_original=m.group(1), procedencia=proc("rfc", pagina.numero, renglon)
                )
                if (c := RE_CIERRE.search(texto)) and c.group(2).lower() in NUMERO_MES:
                    cierre = date(int(c.group(3)), NUMERO_MES[c.group(2).lower()], int(c.group(1)))
                continue
            ultima = linea.palabras[-1].texto
            etiqueta = " ".join(p.texto for p in linea.palabras[:-1])
            if es_monto(ultima) and etiqueta in ETIQUETAS and ETIQUETAS[etiqueta] not in partidas:
                partidas[ETIQUETAS[etiqueta]] = ValorExtraido[Decimal](
                    valor=parsear_monto(ultima),
                    texto_original=ultima,
                    procedencia=proc(ETIQUETAS[etiqueta], pagina.numero, renglon),
                )
                continue
            if "dictaminar" in texto.lower() or "dictaminad" in texto.lower():
                leyenda = texto
                continue
            if texto.startswith(LINEAS_CONOCIDAS):
                continue
            deteccion = detector.evaluar(texto)
            if deteccion.dispara:
                hallazgos.append(
                    HallazgoSeguridad(
                        tipo="instruccion",
                        procedencia=proc("linea_no_reconocida", pagina.numero, renglon),
                        detalle=deteccion.detalle,
                    )
                )
    faltantes = [p for p in (*PARTIDAS_BALANCE, *PARTIDAS_RESULTADOS) if p not in partidas]
    if razon is None or rfc is None or cierre is None or faltantes:
        raise FormatoNoSoportado(f"estados financieros incompletos: {', '.join(faltantes) or 'encabezado'}")
    ley = leyenda.lower()
    hallazgos.extend(
        HallazgoSeguridad(
            tipo="texto_oculto",
            procedencia=Procedencia(documento=nombre, sha256=sha256, pagina=p, campo="texto_oculto"),
            detalle=d,
        )
        for p, d in doc.texto_oculto
    )
    return EstadosFinancieros(
        documento=nombre,
        sha256=sha256,
        razon_social=razon,
        rfc=rfc,
        fecha_cierre=cierre,
        ejercicio=cierre.year,
        partidas=partidas,
        dictaminados=("dictaminad" in ley) and ("sin dictaminar" not in ley) and ("no dictaminad" not in ley),
        leyenda=leyenda,
        paginas=doc.n_paginas,
        hallazgos=tuple(hallazgos),
    )
