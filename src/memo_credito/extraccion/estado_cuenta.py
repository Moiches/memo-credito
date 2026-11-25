"""Parser de estados de cuenta del formato `banco_ficticio_v1` (BANCO FICTICIO DEL CENTRO).

Reglas (verificadas contra los 23 estados del set, ver docs/datos.md):
- Columnas detectadas en cada página desde la fila de encabezado blanca en negrita (la página 2 la repite, H1).
- Encabezados alineados a la izquierda y montos a la derecha: se clasifica por el x1 del monto contra el
  x0 de la columna siguiente (x1 <= Abono.x0 -> cargo; x1 <= Saldo.x0 -> abono; si no, saldo).
- Un movimiento empieza en una línea con fecha; las líneas sin fecha continúan el concepto (H2).
- Se descartan el pie de página (cursiva) y el encabezado repetido.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import SecretStr

from memo_credito.dominio.errores import FormatoNoSoportado, MontoInvalido
from memo_credito.dominio.formato import NUMERO_MES, es_monto, parsear_monto
from memo_credito.dominio.modelos import (
    EstadoCuenta,
    HallazgoSeguridad,
    Movimiento,
    Periodo,
    Procedencia,
    ValorExtraido,
    ValorPii,
)
from memo_credito.extraccion.pdf_util import DocumentoPdf, Linea, Palabra
from memo_credito.extraccion.seguridad import RE_ESTADO_CUENTA, DetectorInstrucciones

FORMATO = "banco_ficticio_v1"
HUELLA = ("BANCO FICTICIO DEL CENTRO, S.A.", "Estado de cuenta empresarial")
COLUMNAS = ("Fecha", "Concepto", "Cargo", "Abono", "Saldo")
RE_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_PERIODO = re.compile(r"(\d{1,2}) al (\d{1,2}) de ([a-záéíóú]+) de (\d{4})", re.IGNORECASE)


def reconoce(doc: DocumentoPdf) -> bool:
    texto = doc.texto_pagina(1) if doc.n_paginas else ""
    return all(h in texto for h in HUELLA)


def clasificar(concepto: str) -> str:
    c = concepto.upper()
    if c.startswith("DEPOSITO EN EFECTIVO"):
        return "efectivo"
    if c.startswith("DEPOSITO TPV"):
        return "tpv"
    if c.startswith(("SPEI", "TRANSFERENCIA")):
        return "spei"
    return "otro"


def _periodo_de_texto(texto: str) -> Periodo:
    m = RE_PERIODO.search(texto)
    if not m or m.group(3).lower() not in NUMERO_MES:
        raise FormatoNoSoportado("periodo del estado de cuenta no reconocido")
    return Periodo(anio=int(m.group(4)), mes=NUMERO_MES[m.group(3).lower()])


def _periodo_de_nombre(nombre: str) -> Periodo | None:
    m = RE_ESTADO_CUENTA.match(nombre)
    return Periodo(anio=int(m.group(1)), mes=int(m.group(2))) if m else None


def _es_encabezado_tabla(linea: Linea) -> bool:
    textos = {p.texto for p in linea.palabras}
    return "Fecha" in textos and "Saldo" in textos and all(p.blanca and p.negrita for p in linea.palabras)


def _columnas(linea: Linea) -> dict[str, float]:
    return {p.texto: p.x0 for p in linea.palabras if p.texto in COLUMNAS}


def parsear_estado_cuenta(doc: DocumentoPdf, nombre: str, sha256: str, detector: DetectorInstrucciones) -> EstadoCuenta:
    if not reconoce(doc):
        raise FormatoNoSoportado("huella de banco no reconocida")

    def proc(campo: str, pagina: int | None = 1, renglon: int | None = None) -> Procedencia:
        return Procedencia(documento=nombre, sha256=sha256, pagina=pagina, renglon=renglon, campo=campo)

    pagina1 = doc.paginas[0]
    campos: dict[str, str] = {}
    resumen: Linea | None = None
    for linea in pagina1.lineas:
        etiqueta = linea.palabras[0].texto
        if etiqueta in ("Titular", "RFC", "Cuenta", "Periodo") and etiqueta not in campos and len(linea.palabras) > 1:
            campos[etiqueta] = " ".join(p.texto for p in linea.palabras[1:])
        elif resumen is None and len(linea.palabras) == 4 and all(es_monto(p.texto) for p in linea.palabras):
            resumen = linea
    if len(campos) < 4 or resumen is None:
        raise FormatoNoSoportado("encabezado del estado de cuenta incompleto")
    try:
        si, ta, tc, sf = (parsear_monto(p.texto) for p in resumen.palabras)
    except MontoInvalido as exc:
        raise FormatoNoSoportado("resumen del estado de cuenta ilegible") from exc

    columnas: dict[str, float] | None = None
    crudos: list[dict[str, Any]] = []
    actual: dict[str, Any] | None = None
    for pagina in doc.paginas:
        inicio_tabla = None
        for linea in pagina.lineas:
            if _es_encabezado_tabla(linea):
                columnas, inicio_tabla = _columnas(linea), linea.top
                break
        if columnas is None:
            raise FormatoNoSoportado("no se encontró la tabla de movimientos")
        for linea in pagina.lineas:
            if pagina.es_pie(linea) or _es_encabezado_tabla(linea):
                continue
            if inicio_tabla is not None and linea.top <= inicio_tabla:
                continue
            palabras = list(linea.palabras)
            if RE_FECHA.match(palabras[0].texto) and palabras[0].x0 < columnas["Concepto"] - 2:
                if actual is not None:
                    crudos.append(actual)
                actual = {
                    "fecha": palabras[0].texto,
                    "concepto": [],
                    "montos": [],
                    "pagina": pagina.numero,
                    "lineas": 1,
                }
                resto: list[Palabra] = palabras[1:]
            elif actual is not None:
                actual["lineas"] += 1
                resto = palabras
            else:
                continue
            for p in resto:
                if es_monto(p.texto) and p.x1 > columnas["Cargo"]:
                    actual["montos"].append(p)
                else:
                    actual["concepto"].append(p.texto)
    if actual is not None:
        crudos.append(actual)
    if columnas is None:
        raise FormatoNoSoportado("no se encontró la tabla de movimientos")

    movimientos: list[Movimiento] = []
    for i, c in enumerate(crudos, start=1):
        cargo = abono = saldo = None
        for p in c["montos"]:
            valor = parsear_monto(p.texto)
            if p.x1 <= columnas["Abono"]:
                cargo = valor if cargo is None else cargo
            elif p.x1 <= columnas["Saldo"]:
                abono = valor if abono is None else abono
            else:
                saldo = valor if saldo is None else saldo
        concepto = " ".join(c["concepto"])
        deteccion = detector.evaluar(concepto, c["lineas"])
        movimientos.append(
            Movimiento(
                fecha=date.fromisoformat(c["fecha"]),
                concepto=concepto,
                cargo=cargo,
                abono=abono,
                saldo=saldo,
                lineas_concepto=c["lineas"],
                clasificacion=clasificar(concepto),  # type: ignore[arg-type]
                marca_instruccion=deteccion.dispara,
                procedencia=proc("movimiento", c["pagina"], i),
            )
        )

    ocultos = tuple(
        HallazgoSeguridad(tipo="texto_oculto", procedencia=proc("texto_oculto", pag), detalle=detalle)
        for pag, detalle in doc.texto_oculto
    )
    return EstadoCuenta(
        documento=nombre,
        sha256=sha256,
        formato=FORMATO,
        banco=HUELLA[0],
        titular=ValorExtraido[str](
            valor=campos["Titular"], texto_original=campos["Titular"], procedencia=proc("titular")
        ),
        rfc=ValorExtraido[str](valor=campos["RFC"], texto_original=campos["RFC"], procedencia=proc("rfc")),
        cuenta=ValorPii(valor=SecretStr(campos["Cuenta"]), procedencia=proc("cuenta")),
        periodo=_periodo_de_texto(campos["Periodo"]),
        periodo_nombre_archivo=_periodo_de_nombre(nombre),
        saldo_inicial=_valor(si, resumen.palabras[0].texto, proc("saldo_inicial")),
        total_abonos=_valor(ta, resumen.palabras[1].texto, proc("total_abonos")),
        total_cargos=_valor(tc, resumen.palabras[2].texto, proc("total_cargos")),
        saldo_final=_valor(sf, resumen.palabras[3].texto, proc("saldo_final")),
        movimientos=tuple(movimientos),
        paginas=doc.n_paginas,
        texto_oculto=ocultos,
    )


def _valor(valor: Decimal, texto: str, procedencia: Procedencia) -> ValorExtraido[Decimal]:
    return ValorExtraido[Decimal](valor=valor, texto_original=texto, procedencia=procedencia)
