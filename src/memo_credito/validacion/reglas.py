"""Reglas de validación: conciliación, periodos, identidad, constancia, EEFF, seguridad y datos.

Los textos de las alertas son deterministas y nunca citan texto con forma de instrucción (evita la
inyección de segundo orden); solo dan su ubicación.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise

from memo_credito.config import Config
from memo_credito.dominio.alertas import CATALOGO
from memo_credito.dominio.formato import formato_monto, nombre_mes
from memo_credito.dominio.modelos import (
    Alerta,
    Conciliacion,
    EstadoCuenta,
    Expediente,
    HallazgoSeguridad,
    Periodo,
    Procedencia,
    Validacion,
)
from memo_credito.validacion.periodos import etiqueta, periodos_esperados

RE_RFC_MORAL = re.compile(r"^[A-ZÑ&]{3}\d{6}[A-Z\d]{2}[A\d]$")
RE_RFC_FISICA = re.compile(r"^[A-ZÑ&]{4}\d{6}[A-Z\d]{2}[A\d]$")


def alerta(
    codigo: str,
    mensaje: str,
    evidencia: tuple[Procedencia, ...] = (),
    cifras: tuple[str, ...] = (),
    severidad: str | None = None,
) -> Alerta:
    _, categoria, sev, titulo = CATALOGO[codigo]
    return Alerta(
        codigo=codigo,
        categoria=categoria,
        severidad=severidad or sev,
        titulo=titulo,  # type: ignore[arg-type]
        mensaje=mensaje,
        evidencia=evidencia,
        cifras=cifras,
    )


def rfc_formato_valido(rfc: str) -> bool:
    """Valida patrón y fecha embebida. NO valida el dígito verificador (hallazgo H3: falla en todo el set)."""
    if RE_RFC_MORAL.match(rfc):
        digitos = rfc[3:9]
    elif RE_RFC_FISICA.match(rfc):
        digitos = rfc[4:10]
    else:
        return False
    aa, mm, dd = int(digitos[:2]), int(digitos[2:4]), int(digitos[4:6])
    for siglo in (2000, 1900):
        try:
            date(siglo + aa, mm, dd)
            return True
        except ValueError:
            continue
    return False


def conciliar(ec: EstadoCuenta) -> Conciliacion:
    cero = Decimal("0.00")
    abonos = sum((m.abono for m in ec.movimientos if m.abono is not None), cero)
    cargos = sum((m.cargo for m in ec.movimientos if m.cargo is not None), cero)
    si, ta, tc, sf = ec.saldo_inicial.valor, ec.total_abonos.valor, ec.total_cargos.valor, ec.saldo_final.valor
    diferencias: list[str] = []
    if abonos != ta:
        diferencias.append(f"suma de abonos {formato_monto(abonos)} frente a total {formato_monto(ta)}")
    if cargos != tc:
        diferencias.append(f"suma de cargos {formato_monto(cargos)} frente a total {formato_monto(tc)}")
    if si + ta - tc != sf:
        diferencias.append(f"saldo inicial + abonos − cargos ≠ saldo final ({formato_monto(sf)})")
    corrido = si
    renglones_ok = True
    for i, m in enumerate(ec.movimientos, start=1):
        if (m.cargo is None) == (m.abono is None) or m.saldo is None:
            renglones_ok = False
            diferencias.append(f"renglón {i}: se esperaba exactamente un cargo o un abono y un saldo")
            break
        corrido = corrido + (m.abono or cero) - (m.cargo or cero)
        if corrido != m.saldo:
            renglones_ok = False
            diferencias.append(
                f"renglón {i}: saldo calculado {formato_monto(corrido)} frente a {formato_monto(m.saldo)}"
            )
            break
    fechas = [m.fecha for m in ec.movimientos]
    fechas_ok = all(f.year == ec.periodo.anio and f.month == ec.periodo.mes for f in fechas) and fechas == sorted(
        fechas
    )
    if not fechas_ok:
        diferencias.append("hay movimientos fuera del periodo o desordenados")
    return Conciliacion(
        documento=ec.documento,
        periodo=ec.periodo.clave,
        suma_abonos_ok=abonos == ta,
        suma_cargos_ok=cargos == tc,
        saldo_final_ok=si + ta - tc == sf,
        renglones_ok=renglones_ok,
        fechas_ok=fechas_ok,
        diferencias=tuple(diferencias),
    )


def _normal(texto: str) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return " ".join(sin.upper().split())


def _lista(nombres: list[str]) -> str:
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]


@dataclass
class ResultadoValidacion:
    esperados: tuple[Periodo, ...]
    conciliaciones: list[Conciliacion]
    usables: dict[str, EstadoCuenta]
    motivos_mes: dict[str, str]
    validaciones: list[Validacion] = field(default_factory=list)
    alertas: list[Alerta] = field(default_factory=list)


def validar(exp: Expediente, cfg: Config) -> ResultadoValidacion:
    sol = exp.solicitud
    esperados = periodos_esperados(sol.fecha_solicitud, cfg.politica.meses_flujo)
    claves = {p.clave for p in esperados}
    concs = [conciliar(e) for e in exp.estados_cuenta]
    conc_por_doc = {c.documento: c for c in concs}
    alertas: list[Alerta] = []
    validaciones: list[Validacion] = []

    # --- Periodos ---------------------------------------------------------------------------------------
    por_periodo: dict[str, list[EstadoCuenta]] = defaultdict(list)
    for e in exp.estados_cuenta:
        por_periodo[e.periodo.clave].append(e)
        p0 = Procedencia(documento=e.documento, sha256=e.sha256, pagina=1, campo="periodo")
        if e.periodo.clave not in claves:
            alertas.append(
                alerta(
                    "A05",
                    f"Se recibió el estado de cuenta de {nombre_mes(e.periodo.mes)} de {e.periodo.anio} "
                    f"({e.documento}), fuera del periodo revisado; no se usó en los cálculos.",
                    (p0,),
                )
            )
        elif e.periodo_nombre_archivo is not None and e.periodo_nombre_archivo != e.periodo:
            alertas.append(
                alerta(
                    "A05",
                    f"El nombre de {e.documento} no coincide con el periodo de su contenido "
                    f"({nombre_mes(e.periodo.mes)} de {e.periodo.anio}).",
                    (p0,),
                )
            )
    motivos: dict[str, str] = {}
    for p in esperados:
        lista = por_periodo.get(p.clave, [])
        if len(lista) > 1:
            alertas.append(
                alerta(
                    "A04",
                    f"Se recibieron {len(lista)} estados de cuenta de {nombre_mes(p.mes)} de {p.anio} "
                    f"({_lista([e.documento for e in lista])}); el flujo no se calcula hasta aclararlo.",
                    tuple(Procedencia(documento=e.documento, sha256=e.sha256, campo="periodo") for e in lista),
                )
            )
            motivos[p.clave] = "duplicado"
    faltantes = [p for p in esperados if p.clave not in por_periodo]
    for p in faltantes:
        motivos[p.clave] = "faltante"
    if faltantes:
        nombres_faltan = _lista([f"{nombre_mes(p.mes)} de {p.anio}" for p in faltantes])
        listado = ", ".join(etiqueta(p) for p in esperados)
        listado_json = any(re.match(rf"^estado_cuenta_{p.clave}\.pdf$", d) for p in faltantes for d in sol.documentos)
        origen = "la solicitud lo lista pero no se recibió" if listado_json else "la solicitud no lo incluye"
        alertas.append(
            alerta(
                "A03",
                f"Falta el estado de cuenta de {nombres_faltan} (periodos esperados: {listado}); {origen}. "
                "El flujo promedio y la capacidad de pago no son calculables hasta recibirlo.",
            )
        )

    # --- Conciliación -----------------------------------------------------------------------------------
    for e in exp.estados_cuenta:
        c = conc_por_doc[e.documento]
        if not c.ok:
            alertas.append(
                alerta(
                    "A06",
                    f"El estado de cuenta de {nombre_mes(e.periodo.mes)} de {e.periodo.anio} "
                    f"({e.documento}) no concilia ({'; '.join(c.diferencias)}); ese mes no se usa en los cálculos.",
                    (Procedencia(documento=e.documento, sha256=e.sha256, campo="conciliacion"),),
                )
            )
            if e.periodo.clave in claves and e.periodo.clave not in motivos:
                motivos[e.periodo.clave] = "no_concilia"
    usables = {
        p.clave: por_periodo[p.clave][0]
        for p in esperados
        if len(por_periodo.get(p.clave, [])) == 1 and conc_por_doc[por_periodo[p.clave][0].documento].ok
    }

    # --- Encadenamiento ---------------------------------------------------------------------------------
    encadenan = True
    for a, b in pairwise(esperados):
        if a.clave in usables and b.clave in usables and b.orden == a.orden + 1:
            sf, si = usables[a.clave].saldo_final, usables[b.clave].saldo_inicial
            if sf.valor != si.valor:
                encadenan = False
                alertas.append(
                    alerta(
                        "A07",
                        f"El saldo final de {nombre_mes(a.mes)} ({formato_monto(sf.valor)}) no coincide con el "
                        f"saldo inicial de {nombre_mes(b.mes)} ({formato_monto(si.valor)}).",
                        (sf.procedencia, si.procedencia),
                    )
                )

    # --- Cuenta y titular -------------------------------------------------------------------------------
    cuentas = {e.cuenta.valor.get_secret_value() for e in exp.estados_cuenta}
    titulares = {_normal(e.titular.valor) for e in exp.estados_cuenta}
    if len(cuentas) > 1 or len(titulares) > 1:
        alertas.append(
            alerta(
                "A08",
                "Los estados de cuenta corresponden a cuentas o titulares distintos.",
                tuple(e.titular.procedencia for e in exp.estados_cuenta),
            )
        )

    # --- Identidad: RFC y razón social -----------------------------------------------------------------
    rfcs: list[tuple[str, str, Procedencia | None]] = [(sol.documento, sol.solicitante.rfc, None)]
    razones: list[tuple[str, str]] = [(sol.documento, sol.solicitante.razon_social)]
    if exp.constancia:
        rfcs.append((exp.constancia.documento, exp.constancia.rfc.valor, exp.constancia.rfc.procedencia))
        razones.append((exp.constancia.documento, exp.constancia.razon_social.valor))
    for e in exp.estados_cuenta:
        rfcs.append((e.documento, e.rfc.valor, e.rfc.procedencia))
        razones.append((e.documento, e.titular.valor))
    if exp.estados_financieros:
        ef = exp.estados_financieros
        rfcs.append((ef.documento, ef.rfc.valor, ef.rfc.procedencia))
        razones.append((ef.documento, ef.razon_social.valor))
    conteo = Counter(v for _, v, _ in rfcs)
    if len(conteo) > 1:
        mayoria = max(conteo, key=lambda v: (conteo[v], v == sol.solicitante.rfc))
        distintos = [(d, v, pr) for d, v, pr in rfcs if v != mayoria]
        docs_mayoria = [d for d, v, _ in rfcs if v == mayoria]
        partes = "; ".join(f"{d} indica {v}" for d, v, _ in distintos)
        alertas.append(
            alerta(
                "A01",
                f"El RFC no coincide entre documentos: {partes}; {_lista(docs_mayoria)} indican {mayoria}. "
                "No se corrigió ningún valor.",
                tuple(pr for _, _, pr in distintos if pr is not None),
            )
        )
    exactas = {v.strip() for _, v in razones}
    if len(exactas) > 1:
        normalizadas = {_normal(v) for _, v in razones}
        sev = "media" if len(normalizadas) == 1 else "alta"
        alertas.append(
            alerta(
                "A02",
                "La razón social no coincide entre documentos"
                + (" (solo difieren acentos o espacios)." if sev == "media" else "."),
                severidad=sev,
            )
        )
    if len(conteo) == 1 and len(exactas) == 1:
        validaciones.append(
            Validacion(
                id="identidad",
                resultado="ok",
                texto_memo="RFC y razón social coinciden en constancia, estados de cuenta y estados financieros.",
            )
        )
    else:
        validaciones.append(
            Validacion(
                id="identidad",
                resultado="falla",
                texto_memo="RFC o razón social no coinciden entre documentos (ver alertas).",
            )
        )
    for doc, valor, pr in rfcs:
        if not rfc_formato_valido(valor):
            alertas.append(alerta("A14", f"El RFC {valor} de {doc} no tiene un formato válido.", (pr,) if pr else ()))

    # --- Constancia -------------------------------------------------------------------------------------
    cons = exp.constancia
    if cons is not None:
        estatus = cons.estatus.valor
        emision = cons.fecha_emision.valor
        dias = (sol.fecha_solicitud - emision).days
        vigente = 0 <= dias <= cfg.politica.vigencia_constancia_dias
        if estatus.upper() != "ACTIVO":
            alertas.append(alerta("A09", f"La constancia reporta estatus {estatus}.", (cons.estatus.procedencia,)))
        if not vigente:
            relacion = f"{dias} días antes de la solicitud" if dias >= 0 else f"{-dias} días después de la solicitud"
            alertas.append(
                alerta(
                    "A10",
                    f"La constancia se emitió el {emision.isoformat()}, {relacion} "
                    f"(máximo {cfg.politica.vigencia_constancia_dias}).",
                    (cons.fecha_emision.procedencia,),
                )
            )
        validaciones.append(
            Validacion(
                id="constancia",
                resultado="ok" if estatus.upper() == "ACTIVO" and vigente else "falla",
                texto_memo=f"Constancia con estatus {estatus}, emitida el {emision.isoformat()}.",
            )
        )
    else:
        validaciones.append(
            Validacion(id="constancia", resultado="falla", texto_memo="No se recibió una constancia válida.")
        )

    # --- Estados de cuenta (completitud y encadenamiento) ---------------------------------------------
    n = len(esperados)
    if len(usables) == n and encadenan:
        validaciones.append(
            Validacion(
                id="estados_cuenta",
                resultado="ok",
                texto_memo=f"Estados de cuenta completos para los {n} meses; los saldos encadenan entre meses.",
            )
        )
    else:
        validaciones.append(
            Validacion(
                id="estados_cuenta",
                resultado="falla",
                texto_memo="Estados de cuenta incompletos o inconsistentes para el periodo revisado (ver alertas).",
            )
        )
    if exp.estados_cuenta and all(c.ok for c in concs):
        validaciones.append(
            Validacion(
                id="conciliacion",
                resultado="ok",
                texto_memo="Los movimientos de cada estado de cuenta concilian al centavo con sus totales y saldos.",
            )
        )
    elif exp.estados_cuenta:
        validaciones.append(
            Validacion(
                id="conciliacion",
                resultado="falla",
                texto_memo="Al menos un estado de cuenta no concilia con sus totales (ver alertas).",
            )
        )

    # --- Estados financieros ---------------------------------------------------------------------------
    eeff = exp.estados_financieros
    if eeff is not None:
        esperado = sol.fecha_solicitud.year - 1
        if eeff.ejercicio != esperado:
            alertas.append(
                alerta("A11", f"Los estados financieros son del ejercicio {eeff.ejercicio}; se esperaba {esperado}.")
            )
        partida = {k: v.valor for k, v in eeff.partidas.items()}
        problemas = []
        if partida["total_activo"] != partida["total_pasivo"] + partida["capital_contable"]:
            problemas.append("activo ≠ pasivo + capital")
        if partida["total_pasivo_capital"] != partida["total_activo"]:
            problemas.append("total pasivo + capital ≠ total activo")
        if partida["activo_circulante"] + partida["activo_fijo_neto"] != partida["total_activo"]:
            problemas.append("activo circulante + fijo ≠ total activo")
        if partida["pasivo_corto_plazo"] + partida["pasivo_largo_plazo"] != partida["total_pasivo"]:
            problemas.append("pasivo corto + largo ≠ total pasivo")
        cadena = [
            ("ventas_netas", "costo_ventas", "utilidad_bruta"),
            ("utilidad_bruta", "gastos_operacion", "utilidad_operacion"),
            ("utilidad_operacion", "gastos_financieros", "utilidad_antes_impuestos"),
            ("utilidad_antes_impuestos", "isr", "utilidad_neta"),
        ]
        problemas += [f"{a} − {b} ≠ {c}" for a, b, c in cadena if partida[a] - partida[b] != partida[c]]
        if problemas:
            alertas.append(
                alerta(
                    "A12",
                    f"Los estados financieros no cuadran: {'; '.join(problemas)}.",
                    (Procedencia(documento=eeff.documento, sha256=eeff.sha256, campo="balance"),),
                )
            )
        if partida["capital_contable"] <= 0:
            alertas.append(
                alerta(
                    "A13",
                    f"El capital contable es {formato_monto(partida['capital_contable'])}; "
                    "el apalancamiento no es calculable.",
                    (eeff.partidas["capital_contable"].procedencia,),
                )
            )

    # --- Documentos listados frente a recibidos -------------------------------------------------------
    recibidos = {d.nombre for d in exp.documentos} | {r.nombre for r in exp.rechazados}
    listados = set(sol.documentos)
    no_recibidos = sorted(listados - recibidos)
    no_listados = sorted(recibidos - listados - {"solicitud.json"})
    if no_recibidos or no_listados:
        partes_doc: list[str] = []
        if no_recibidos:
            partes_doc.append(f"lista {_lista(no_recibidos)} pero no se recibió")
        if no_listados:
            partes_doc.append(f"no lista {_lista(no_listados)}, que sí se recibió")
        alertas.append(alerta("A15", f"La solicitud {' y '.join(partes_doc)}."))
    if cons is None:
        alertas.append(alerta("A15", "No se recibió una constancia de situación fiscal válida."))
    if eeff is None:
        alertas.append(alerta("A15", "No se recibieron estados financieros válidos."))

    # --- Crédito ---------------------------------------------------------------------------------------
    cr = sol.credito
    problemas_credito = []
    if cr.monto_solicitado <= 0:
        problemas_credito.append("monto no positivo")
    if not 1 <= cr.plazo_meses <= 120:
        problemas_credito.append(f"plazo de {cr.plazo_meses} meses fuera de 1 a 120")
    if cr.moneda != "MXN":
        problemas_credito.append(f"moneda {cr.moneda}")
    if problemas_credito:
        alertas.append(alerta("A16", f"El monto, plazo o moneda no es válido: {'; '.join(problemas_credito)}."))

    # --- Seguridad -------------------------------------------------------------------------------------
    for e in exp.estados_cuenta:
        for m in e.movimientos:
            if m.marca_instruccion:
                tipo = "abono" if m.abono is not None else "cargo"
                monto = m.abono if m.abono is not None else m.cargo
                monto_txt = formato_monto(monto) if monto is not None else "monto no legible"
                pr = m.procedencia
                alertas.append(
                    alerta(
                        "S01",
                        f"El concepto del {tipo} del {m.fecha.isoformat()} por {monto_txt} "
                        f"({pr.documento}, pág. {pr.pagina}, renglón {pr.renglon}) contiene texto dirigido a un "
                        "sistema automatizado. No se procesó; el movimiento "
                        "sí cuenta en los totales. Señal de riesgo documental para el comité.",
                        (pr,),
                    )
                )
    hallazgos: list[HallazgoSeguridad] = list(sol.hallazgos)
    if cons is not None:
        hallazgos += list(cons.hallazgos)
    if ef is not None:
        hallazgos += list(ef.hallazgos)
    for e in exp.estados_cuenta:
        hallazgos += list(e.texto_oculto)
    for h in hallazgos:
        pr = h.procedencia
        ubicacion = (
            f"{pr.documento}"
            + (f", pág. {pr.pagina}" if pr.pagina else "")
            + (f", renglón {pr.renglon}" if pr.renglon else "")
        )
        if h.tipo == "instruccion":
            campo = f" (campo {pr.campo})" if pr.pagina is None else ""
            alertas.append(
                alerta(
                    "S01",
                    f"{ubicacion}{campo}: contiene texto dirigido a un sistema automatizado o fuera de "
                    "los límites permitidos. No se procesó.",
                    (pr,),
                )
            )
        else:
            alertas.append(alerta("S03", f"{ubicacion}: hay texto no visible ({h.detalle}); no se procesó.", (pr,)))
    for r in exp.rechazados:
        alertas.append(
            alerta(
                "S02",
                f"El archivo {r.nombre} se rechazó: {r.motivo}.",
                (Procedencia(documento=r.nombre, sha256=r.sha256, campo="archivo"),),
            )
        )

    # --- Datos ---------------------------------------------------------------------------------------------
    if sol.campos_no_esperados:
        alertas.append(
            alerta(
                "D01",
                f"La solicitud incluía campos no requeridos ({', '.join(sol.campos_no_esperados)}); "
                "se descartaron sin procesarse.",
            )
        )

    return ResultadoValidacion(
        esperados=esperados,
        conciliaciones=concs,
        usables=usables,
        motivos_mes=motivos,
        validaciones=validaciones,
        alertas=alertas,
    )
