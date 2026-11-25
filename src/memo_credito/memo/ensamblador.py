"""Ensambla el memo en Markdown con el formato del memo de ejemplo.

Encabezado, tabla de indicadores y validaciones son deterministas. El LLM solo aporta Resumen, Observaciones y
Puntos para el comité; sus marcadores {{id}} se sustituyen aquí con las cifras de los hechos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from memo_credito.dominio.formato import formato_monto
from memo_credito.dominio.modelos import Analisis, Expediente
from memo_credito.memo.modelos import HechosParaLLM, SalidaLLM
from memo_credito.validacion.periodos import rango

RE_MARCADOR = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
FRASE_FINAL = "*La decisión corresponde al comité de crédito.*"
TITULOS = ("## Resumen", "## Indicadores", "## Validaciones", "## Observaciones", "## Puntos para el comité")


@dataclass(frozen=True)
class Metadatos:
    fecha: str
    ejecucion: str
    prompt_id: str
    modelo: str
    modo: str
    commit: str


def renderizar(texto: str, hechos: HechosParaLLM) -> str:
    valores = {**hechos.cifras, **hechos.textos}
    return RE_MARCADOR.sub(lambda m: valores.get(m.group(1), m.group(0)), texto)


def documentos_revisados(exp: Expediente, analisis: Analisis) -> str:
    partes = [f"estados de cuenta {rango(analisis.periodos_esperados, set(analisis.periodos_presentes))}"]
    if exp.constancia is not None:
        partes.append("constancia de situación fiscal")
    if exp.estados_financieros is not None:
        partes.append(f"estados financieros {exp.estados_financieros.ejercicio}")
    partes.append("solicitud.json")
    return ", ".join(partes)


def ensamblar(
    exp: Expediente,
    analisis: Analisis,
    hechos: HechosParaLLM,
    salida: SalidaLLM,
    meta: Metadatos,
    origen: str,
    motivo_plantilla: str | None = None,
) -> str:
    sol = exp.solicitud
    lineas = [f"# Memo de análisis de crédito — {sol.folio}", ""]
    lineas.append(
        "> Borrador generado por el sistema · pendiente de revisión del analista · no constituye una decisión de crédito"
    )
    if origen == "plantilla":
        motivo = f" ({motivo_plantilla})" if motivo_plantilla else ""
        lineas.append(">")
        lineas.append(
            f"> **Requiere revisión:** la redacción automática no pasó la verificación{motivo}; "
            "el texto proviene de la plantilla."
        )
    lineas.append("")
    lineas += [
        f"**Solicitante:** {sol.solicitante.razon_social} (RFC {sol.solicitante.rfc})",
        f"**Solicitud:** {formato_monto(sol.credito.monto_solicitado)} a {sol.credito.plazo_meses} meses · {hechos.credito['destino']}",
        f"**Elaborado por:** Sistema de análisis (borrador) · {meta.fecha}",
        f"**Documentos revisados:** {documentos_revisados(exp, analisis)}",
        "",
        "## Resumen",
        "",
        renderizar(salida.resumen, hechos),
        "",
        "## Indicadores",
        "",
        "| Indicador | Valor | Fuente |",
        "| --- | --- | --- |",
    ]
    lineas += [f"| {i.nombre} | {i.valor_texto} | {i.fuente_memo} |" for i in analisis.indicadores]
    lineas += ["", "## Validaciones", ""]
    lineas += [f"- {v.texto_memo}" for v in analisis.validaciones]
    lineas += [f"- **Alerta {a.codigo} · {a.titulo} ({a.severidad}).** {a.mensaje}" for a in analisis.alertas]
    lineas += ["", "## Observaciones", ""]
    lineas += [f"- {renderizar(v.texto, hechos)}" for v in salida.observaciones]
    lineas += ["", "## Puntos para el comité", ""]
    lineas += [f"- {renderizar(v.texto, hechos)}" for v in salida.puntos_comite]
    lineas += [
        "",
        FRASE_FINAL,
        "",
        f"<!-- folio={sol.folio} · ejecucion={meta.ejecucion} · prompt={meta.prompt_id} · modelo={meta.modelo} · "
        f"modo={meta.modo} · origen={origen} · commit={meta.commit} -->",
    ]
    return "\n".join(lineas) + "\n"


def secciones_redactadas(memo: str) -> str:
    """Texto de Resumen, Observaciones y Puntos (lo que aportó el LLM), ya renderizado."""
    partes = []
    for titulo, siguiente in (("## Resumen", "## Indicadores"), ("## Observaciones", "## Puntos para el comité")):
        if titulo in memo and siguiente in memo:
            partes.append(memo.split(titulo, 1)[1].split(siguiente, 1)[0])
    if "## Puntos para el comité" in memo:
        partes.append(memo.split("## Puntos para el comité", 1)[1].split(FRASE_FINAL, 1)[0])
    return "\n".join(partes)
