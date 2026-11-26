"""Funciones puras de la consola: validan la entrada y leen el memo y el historial para mostrarlos.

No importan Streamlit ni AWS, así que se prueban sin red (tests/unit/test_ui.py).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from memo_credito.config import Limites
from memo_credito.extraccion.seguridad import RE_EEFF, RE_ESTADO_CUENTA, tipo_por_nombre

# Mismos patrones que Solicitud.folio (dominio) y RE_NOMBRE (ingesta); la prueba los compara con su origen.
RE_FOLIO = re.compile(r"^[A-Z]{2,4}-[A-Z0-9-]{1,40}$")
RE_NOMBRE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


@dataclass(frozen=True)
class EstiloEstado:
    etiqueta: str
    color: str
    icono: str
    descripcion: str


ESTADOS: dict[str, EstiloEstado] = {
    "RECIBIDA": EstiloEstado("Recibida", "blue", ":material/inbox:", "La ingesta validó el manifiesto."),
    "PROCESANDO": EstiloEstado(
        "Procesando", "blue", ":material/autorenew:", "Step Functions extrae, valida y redacta el borrador."
    ),
    "MEMO_GENERADO": EstiloEstado("Memo generado", "blue", ":material/description:", "El borrador ya está escrito."),
    "PENDIENTE_REVISION": EstiloEstado(
        "Pendiente de revisión", "orange", ":material/pending_actions:", "Espera la revisión de un analista."
    ),
    "REVISION_RECIBIDA": EstiloEstado(
        "Revisión recibida", "blue", ":material/fact_check:", "Se validó la revisión y se reanuda el flujo."
    ),
    "LISTO_PARA_COMITE": EstiloEstado(
        "Listo para comité", "green", ":material/task_alt:", "El analista lo dio por bueno; decide el comité."
    ),
    "REQUIERE_CAMBIOS": EstiloEstado(
        "Requiere cambios", "orange", ":material/undo:", "El analista devolvió el expediente."
    ),
    "ERROR": EstiloEstado("Error", "red", ":material/error:", "Un paso falló; el motivo queda registrado."),
    "REVISION_VENCIDA": EstiloEstado(
        "Revisión vencida", "red", ":material/timer_off:", "Se agotaron los escalamientos por SLA."
    ),
}
ESTADOS_FINALES = frozenset({"LISTO_PARA_COMITE", "REQUIERE_CAMBIOS", "ERROR", "REVISION_VENCIDA"})
# En estos estados no hay nada que esperar en segundo plano: el seguimiento deja de refrescarse.
ESTADOS_EN_REPOSO = ESTADOS_FINALES | {"PENDIENTE_REVISION"}

COLOR_SEVERIDAD = {"alta": "red", "media": "orange", "baja": "blue", "info": "gray"}

DECISIONES = {"listo_para_comite": "Listo para comité", "requiere_cambios": "Requiere cambios"}
SECCIONES = {
    "resumen": "Resumen",
    "indicadores": "Indicadores",
    "validaciones": "Validaciones",
    "observaciones": "Observaciones",
    "puntos": "Puntos para el comité",
}
CATEGORIAS_ERROR = {
    "ninguna": "Ninguno: el borrador estaba bien",
    "cifra": "Una cifra",
    "alerta_omitida": "Faltó una alerta",
    "alerta_falsa": "Una alerta que no aplica",
    "redaccion": "Redacción",
    "tono": "Tono o lenguaje de recomendación",
    "dato_faltante": "Faltó un dato",
    "otro": "Otro",
}


# ---------------------------------------------------------------------------- entrada


@dataclass(frozen=True)
class Problema:
    archivo: str
    mensaje: str
    bloquea: bool


def validar_folio(folio: str) -> str | None:
    if not folio:
        return "Escribe un folio."
    if not RE_FOLIO.fullmatch(folio):
        return "Usa de 2 a 4 letras mayúsculas, un guion y luego letras o números; por ejemplo, UI-0001."
    return None


def sugerir_folio(ahora: datetime) -> str:
    """Un folio nuevo en cada envío: el mismo folio con los mismos documentos se deduplica."""
    return f"UI-{ahora:%Y%m%d-%H%M%S}"


def es_folio_de_prueba(folio: str) -> bool:
    """IT- y LT- son de las pruebas de integración y de carga del pipeline."""
    return folio.startswith(("IT-", "LT-"))


def validar_archivos(archivos: Mapping[str, int], limites: Limites) -> list[Problema]:
    """Lo que rechazarían la ingesta (bloquea el envío) o la extracción (se reporta como alerta S02)."""
    problemas: list[Problema] = []
    if "solicitud.json" not in archivos:
        problemas.append(
            Problema("solicitud.json", "Falta la solicitud: sin ella no hay folio, monto ni lista de documentos.", True)
        )
    if len(archivos) > limites.max_archivos:
        problemas.append(
            Problema("(expediente)", f"Son {len(archivos)} archivos y el máximo es {limites.max_archivos}.", True)
        )
    for nombre, tamano_bytes in sorted(archivos.items()):
        if not RE_NOMBRE.fullmatch(nombre) or nombre == "_manifest.json":
            problemas.append(
                Problema(
                    nombre,
                    "El nombre lleva espacios, acentos o símbolos: la ingesta rechazaría el expediente completo.",
                    True,
                )
            )
            continue
        tipo = tipo_por_nombre(nombre)
        if tipo is None:
            problemas.append(
                Problema(
                    nombre,
                    "Nombre no reconocido: se rechazará como alerta S02. Usa estado_cuenta_AAAA-MM.pdf, "
                    "constancia_situacion_fiscal.pdf, estados_financieros_AAAA.pdf o solicitud.json.",
                    False,
                )
            )
        elif tipo == "solicitud" and tamano_bytes > limites.json_max_bytes:
            problemas.append(Problema(nombre, f"Pesa más de {tamano(limites.json_max_bytes)}.", True))
        elif tipo != "solicitud" and tamano_bytes > limites.pdf_max_bytes:
            problemas.append(
                Problema(nombre, f"Pesa más de {tamano(limites.pdf_max_bytes)}: se rechazará como S02.", False)
            )
    return problemas


@dataclass(frozen=True)
class Requisito:
    nombre: str
    cumple: bool
    detalle: str


def requisitos(nombres: Iterable[str], meses: int) -> list[Requisito]:
    """Lista de verificación del expediente; lo que falte no bloquea, pero genera alertas (A03, A15)."""
    lista = list(nombres)
    periodos = sorted(f"{m.group(1)}-{m.group(2)}" for n in lista if (m := RE_ESTADO_CUENTA.match(n)))
    eeff = sorted(n for n in lista if RE_EEFF.match(n))
    return [
        Requisito("Solicitud", "solicitud.json" in lista, "solicitud.json"),
        Requisito(
            "Estados de cuenta",
            len(periodos) >= meses,
            f"{len(periodos)} de {meses}" + (f" ({', '.join(periodos)})" if periodos else ""),
        ),
        Requisito(
            "Constancia de situación fiscal",
            "constancia_situacion_fiscal.pdf" in lista,
            "constancia_situacion_fiscal.pdf",
        ),
        Requisito("Estados financieros", bool(eeff), ", ".join(eeff) or "estados_financieros_AAAA.pdf"),
    ]


# ---------------------------------------------------------------------------- memo


@dataclass(frozen=True)
class AlertaMemo:
    codigo: str
    titulo: str
    severidad: str
    detalle: str


@dataclass(frozen=True)
class FilaIndicador:
    nombre: str
    valor: str
    fuente: str


_RE_ALERTA = re.compile(
    r"^- \*\*Alerta (?P<codigo>[A-Z]\d{2}) · (?P<titulo>.+?) \((?P<severidad>alta|media|baja|info)\)\.\*\* (?P<detalle>.*)$",
    re.MULTILINE,
)
_RE_METADATOS = re.compile(r"<!--\s*(?P<cuerpo>.*?)\s*-->\s*\Z", re.DOTALL)


def alertas_del_memo(texto: str) -> list[AlertaMemo]:
    return [AlertaMemo(m["codigo"], m["titulo"], m["severidad"], m["detalle"]) for m in _RE_ALERTA.finditer(texto)]


def metadatos_del_memo(texto: str) -> dict[str, str]:
    """La última línea del memo: folio, ejecución, prompt, modelo, modo, origen y commit."""
    m = _RE_METADATOS.search(texto)
    if not m:
        return {}
    pares = (p.split("=", 1) for p in m["cuerpo"].split(" · ") if "=" in p)
    return {k.strip(): v.strip() for k, v in pares}


def cuerpo_del_memo(texto: str) -> str:
    """El memo sin el comentario de metadatos, para mostrarlo como Markdown."""
    return _RE_METADATOS.sub("", texto).rstrip() + "\n"


def indicadores_del_memo(texto: str) -> list[FilaIndicador]:
    filas: list[FilaIndicador] = []
    dentro = False
    for linea in texto.splitlines():
        if linea.startswith("## "):
            dentro = linea.strip() == "## Indicadores"
            continue
        if not dentro or not linea.startswith("|"):
            continue
        celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
        if len(celdas) < 3 or celdas[0] in {"Indicador", ""} or set(celdas[0]) <= {"-", " "}:
            continue
        filas.append(FilaIndicador(celdas[0], celdas[1], " | ".join(celdas[2:])))
    return filas


def valor_indicador(filas: Iterable[FilaIndicador], nombre: str) -> str:
    return next((f.valor for f in filas if f.nombre == nombre), "—")


def bajar_titulos(texto: str) -> str:
    """Baja un nivel los títulos del memo para que la página conserve un solo h1."""
    return re.sub(r"^(#{1,5}) ", r"#\1 ", texto, flags=re.MULTILINE)


_RE_RENGLON_CON_ETIQUETA = re.compile(r"^(\*\*[^*\n]+:\*\* .*\S)$", re.MULTILINE)


def para_mostrar(texto: str) -> str:
    """Memo listo para Markdown: sin metadatos, títulos un nivel abajo y un salto real tras cada «**Etiqueta:**»."""
    return _RE_RENGLON_CON_ETIQUETA.sub(r"\1  ", bajar_titulos(cuerpo_del_memo(texto)))


def texto_insignia(texto: str) -> str:
    """Quita los corchetes que romperían la sintaxis :color-badge[...] de Streamlit."""
    return texto.replace("[", "(").replace("]", ")")


_MD_ESPECIALES = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~:$])")


def escapar_md(texto: str) -> str:
    """Escapa texto del solicitante (razón social, destino, nombres de archivo) para mostrarlo literal.

    Evita que se interprete como Markdown, enlaces, LaTeX o directivas de Streamlit (:color[...], :material/...:).
    """
    return _MD_ESPECIALES.sub(r"\\\1", texto)


# ---------------------------------------------------------------------------- flujo


@dataclass
class ResumenEstado:
    entradas: int = 0
    salidas: int = 0
    primera_entrada: datetime | None = None
    ultima_salida: datetime | None = None


@dataclass(frozen=True)
class Paso:
    clave: str
    etiqueta: str
    descripcion: str
    estado: Literal["completado", "en_curso", "pendiente", "fallido", "omitido"]
    detalle: str


PASOS: tuple[tuple[str, str, str], ...] = (
    ("RegistrarInicio", "Inicio", "La ingesta validó el manifiesto y Step Functions marca el folio en proceso."),
    ("ExtraerDocumentos", "Extraer documentos", "Un parser por archivo, en paralelo (Map)."),
    ("ValidarYCalcular", "Validar y calcular", "Conciliación al centavo, validaciones, alertas e indicadores."),
    ("RedactarMemo", "Redactar memo", "LangGraph: el LLM redacta y un verificador de código lo revisa."),
    ("EsperarRevision", "Esperar revisión", "Pausa con token hasta que un analista registre su revisión."),
    ("RegistrarRevision", "Registrar revisión", "Guarda la revisión en la auditoría y cierra el folio."),
)
# Ramas alternas: aparecen solo si ocurrieron, junto al paso del que salen.
ALTERNOS: dict[str, tuple[str, str, str]] = {
    "RedactarConPlantilla": (
        "RedactarMemo",
        "Plantilla determinista",
        "El LLM no estuvo disponible tras los reintentos; se usó la plantilla.",
    ),
    "EscalarRevision": ("EsperarRevision", "Escalamiento por SLA", "La revisión excedió el SLA y se avisó por SNS."),
    "MarcarRevisionVencida": ("EsperarRevision", "Revisión vencida", "Se agotaron los escalamientos."),
    "MarcarError": ("", "Error", "Un paso falló; el motivo queda en el estado del folio."),
}


def resumir_historial(eventos: Iterable[Mapping[str, Any]]) -> dict[str, ResumenEstado]:
    """Cuenta entradas y salidas de cada estado en el historial de Step Functions."""
    resumen: dict[str, ResumenEstado] = {}
    for ev in eventos:
        tipo = str(ev.get("type", ""))
        if tipo.endswith("StateEntered"):
            r = resumen.setdefault(ev["stateEnteredEventDetails"]["name"], ResumenEstado())
            r.entradas += 1
            r.primera_entrada = r.primera_entrada or ev["timestamp"]
        elif tipo.endswith("StateExited"):
            r = resumen.setdefault(ev["stateExitedEventDetails"]["name"], ResumenEstado())
            r.salidas += 1
            r.ultima_salida = ev["timestamp"]
    return resumen


def _paso(clave: str, etiqueta: str, descripcion: str, r: ResumenEstado | None, corriendo: bool) -> Paso:
    if r is None or r.entradas == 0:
        return Paso(clave, etiqueta, descripcion, "pendiente" if corriendo else "omitido", "—")
    if r.salidas >= r.entradas and r.primera_entrada and r.ultima_salida:
        return Paso(
            clave,
            etiqueta,
            descripcion,
            "completado",
            duracion((r.ultima_salida - r.primera_entrada).total_seconds()),
        )
    return Paso(clave, etiqueta, descripcion, "en_curso" if corriendo else "fallido", "en curso" if corriendo else "")


def pasos_del_flujo(resumen: Mapping[str, ResumenEstado], estado_ejecucion: str) -> list[Paso]:
    corriendo = estado_ejecucion == "RUNNING"
    pasos: list[Paso] = []
    for clave, etiqueta, descripcion in PASOS:
        paso = _paso(clave, etiqueta, descripcion, resumen.get(clave), corriendo)
        if clave == "ExtraerDocumentos" and (docs := resumen.get("ExtraerDocumento")) and docs.entradas:
            detalle = f"{docs.entradas} documentos" + (f" · {paso.detalle}" if paso.estado == "completado" else "")
            paso = Paso(paso.clave, paso.etiqueta, paso.descripcion, paso.estado, detalle)
        if clave == "EsperarRevision" and paso.estado == "en_curso":
            paso = Paso(paso.clave, paso.etiqueta, paso.descripcion, paso.estado, "esperando al analista")
        pasos.append(paso)
        for alterno, (despues_de, etq, desc) in ALTERNOS.items():
            if despues_de == clave and alterno in resumen:
                pasos.append(_paso(alterno, etq, desc, resumen[alterno], corriendo))
    if "MarcarError" in resumen:
        _, etq, desc = ALTERNOS["MarcarError"]
        pasos.append(Paso("MarcarError", etq, desc, "fallido", ""))
    return pasos


def url_ejecucion(region: str, arn_ejecucion: str) -> str:
    return f"https://{region}.console.aws.amazon.com/states/home?region={region}#/v2/executions/details/{arn_ejecucion}"


# ---------------------------------------------------------------------------- formato breve


def enmascarar_cuenta(cuenta: str) -> str:
    return "••••••••" + cuenta[-4:] if len(cuenta) >= 4 else "••••"


def nombre_corto(arn: str) -> str:
    """arn:aws:iam::<cuenta>:user/ana → user/ana; sin el ID de la cuenta."""
    return arn.split(":", 5)[-1] if arn.count(":") >= 5 else arn


def duracion(segundos: float) -> str:
    if segundos < 60:
        return f"{segundos:.1f} s"
    minutos, resto = divmod(int(segundos), 60)
    return f"{minutos} min {resto} s"


def hace(momento_iso: str, ahora: datetime) -> str:
    try:
        momento = datetime.fromisoformat(momento_iso)
    except ValueError:
        return momento_iso or "—"
    segundos = max(0, int((ahora - momento).total_seconds()))
    if segundos < 60:
        return f"hace {segundos} s"
    if segundos < 3600:
        return f"hace {segundos // 60} min"
    if segundos < 86400:
        return f"hace {segundos // 3600} h"
    return f"hace {segundos // 86400} d"


def moneda(valor: Any) -> str:
    try:
        return f"${Decimal(str(valor)):,.2f}"
    except (InvalidOperation, ValueError):
        return str(valor)


def tamano(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024 * 1024:
        return f"{n_bytes / 1024:.1f} KB"
    return f"{n_bytes / (1024 * 1024):.1f} MB"
