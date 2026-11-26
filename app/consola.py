"""Consola local del analista de memo-credito (Streamlit).

Corre en tu equipo con tu propio perfil de AWS: al registrar una revisión, IAM sigue imponiendo tu identidad.
La lógica vive en memo_credito.ui (probada sin Streamlit); aquí solo está la vista. Ver ADR-0013.

    uv run python tareas.py ui          # o: uv run --group ui streamlit run app/consola.py
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError

from memo_credito.config import Config, cargar_config
from memo_credito.ui import formato as fmt
from memo_credito.ui import servicios as srv

ENTORNO = os.environ.get("ENTORNO", "dev")
INTERVALO_S = 2

st.set_page_config(
    page_title="memo-crédito · Consola del analista", page_icon=":material/request_quote:", layout="wide"
)

# === Estilos ===================================================================================================
# El tema (colores, IBM Plex, radios) vive en .streamlit/config.toml; aquí solo lo que el tema no cubre.
# Bloque estático: nunca se interpola contenido del usuario ni del memo con unsafe_allow_html.
st.markdown(
    """
<style>
:root { --mc-tinta: #0F172A; }
/* Texto oscuro sobre el dorado de los botones primarios activos: contraste 8:1 en lugar de 2:1 con blanco */
button[data-testid^="stBaseButton-primary"]:not(:disabled),
button[data-testid^="stBaseButton-primary"]:not(:disabled) p {
  color: var(--mc-tinta) !important; font-weight: 600;
}
/* Cifras de ancho fijo que ajustan en lugar de cortarse con "…" */
[data-testid="stMetricValue"] { font-size: 1.5rem; font-variant-numeric: tabular-nums; }
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {
  white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
}
code { font-variant-numeric: tabular-nums; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0s !important; transition-duration: 0s !important; }
}
</style>
""",
    unsafe_allow_html=True,
)


# === Conexión y datos ==========================================================================================


@st.cache_resource(show_spinner=False)
def config() -> Config:
    return cargar_config(ENTORNO)


@st.cache_resource(ttl=900, show_spinner="Conectando con AWS…")
def _conexion() -> srv.Conexion:
    return srv.conectar(ENTORNO)


def mensaje_aws(exc: BaseException) -> str:
    """Causa y cómo resolverla; nunca el detalle crudo de la excepción."""
    if isinstance(exc, SystemExit):
        return f"No hay recursos publicados para «{ENTORNO}» en SSM. ¿Está desplegado ese entorno?"
    if isinstance(exc, ClientError):
        codigo = exc.response.get("Error", {}).get("Code", "")
        if codigo in {"ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException", "InvalidClientTokenId"}:
            return "Tu sesión de AWS expiró o no es válida. Vuelve a iniciar sesión con tu perfil y pulsa Reintentar."
        if codigo in {"AccessDenied", "AccessDeniedException"}:
            return "Tu rol no tiene permiso para esta operación. Pídelo a quien administra la cuenta."
        return f"AWS respondió {codigo or 'con un error'}. Intenta de nuevo en unos segundos."
    nombre = type(exc).__name__
    if "SSO" in nombre or "Token" in nombre:
        return "Tu sesión de SSO expiró. Ejecuta `aws sso login` con tu perfil y pulsa Reintentar."
    if nombre == "NoCredentialsError":
        return "No encontré credenciales. Define AWS_PROFILE antes de abrir la consola y pulsa Reintentar."
    return f"No se pudo hablar con AWS ({nombre}). Revisa tu conexión y pulsa Reintentar."


def obtener_conexion() -> tuple[srv.Conexion | None, str]:
    try:
        return _conexion(), ""
    except (BotoCoreError, ClientError, SystemExit) as exc:
        return None, mensaje_aws(exc)


def con_aws[T](accion: str, fn: Callable[[], T]) -> T | None:
    try:
        return fn()
    except (BotoCoreError, ClientError) as exc:
        st.error(f"No se pudo {accion}. {mensaje_aws(exc)}", icon=":material/error:")
        return None


@st.cache_data(ttl=15, show_spinner=False)
def pendientes_cache(_con: srv.Conexion) -> list[srv.Pendiente]:
    return srv.pendientes(_con)


@st.cache_data(ttl=600, show_spinner=False, max_entries=50)
def borrador_cache(_con: srv.Conexion, folio: str, clave: str) -> srv.Borrador:
    return srv.borrador(_con, folio, clave)


@st.cache_data(ttl=600, show_spinner=False, max_entries=50)
def analisis_cache(_con: srv.Conexion, folio: str, ejecucion: str) -> dict[str, Any]:
    return srv.analisis_auditado(_con, folio, ejecucion)


def ahora() -> datetime:
    return datetime.now(UTC)


# === Navegación ================================================================================================

PAGINAS: dict[str, Any] = {}


def ir_a(pagina: str, folio: str | None = None) -> None:
    st.switch_page(PAGINAS[pagina], query_params={"folio": folio} if folio else None)


def folio_de_la_url() -> str:
    return str(st.query_params.get("folio") or st.session_state.get("folio") or "")


# === Componentes ===============================================================================================


def encabezado(titulo: str, subtitulo: str) -> None:
    st.title(titulo, anchor=False)
    st.caption(subtitulo)


def insignia(texto: str, color: str, icono: str = "") -> str:
    contenido = (f"{icono} " if icono else "") + fmt.texto_insignia(texto)
    return f":{color}-badge[{contenido}]"


def requiere_conexion() -> srv.Conexion | None:
    con, error = obtener_conexion()
    if con is None:
        with st.container(border=True):
            st.markdown("**Esta pantalla necesita conexión con AWS**")
            st.caption(error)
            st.page_link(PAGINAS["local"], label="Probar sin AWS en el modo local", icon=":material/computer:")
    return con


def vacio(mensaje: str, sugerencia: str, destino: str, etiqueta: str, icono: str) -> None:
    with st.container(border=True):
        st.markdown(f"**{mensaje}**")
        st.caption(sugerencia)
        st.page_link(PAGINAS[destino], label=etiqueta, icon=icono)


def tarjeta_estado(folio: str, item: dict[str, Any]) -> None:
    codigo = str(item.get("estado") or "")
    estilo = fmt.ESTADOS.get(codigo, fmt.EstiloEstado(codigo or "Sin estado", "gray", ":material/help:", ""))
    with st.container(border=True):
        izq, der = st.columns([3, 2], vertical_alignment="center")
        with izq:
            st.subheader(folio, anchor=False)
            st.markdown(f"{insignia(estilo.etiqueta, estilo.color, estilo.icono)} {estilo.descripcion}")
        with der:
            st.caption(f"Actualizado {fmt.hace(str(item.get('actualizado') or ''), ahora())}")
            motivo = str(item.get("motivo") or "-")
            if motivo != "-":
                st.caption(f"Motivo: {fmt.escapar_md(motivo)}")


ESTADO_PASO = {
    "completado": (":green[:material/check_circle:]", "Completado"),
    "en_curso": (":orange[:material/progress_activity:]", "En curso"),
    "pendiente": (":gray[:material/radio_button_unchecked:]", "Pendiente"),
    "fallido": (":red[:material/cancel:]", "Falló"),
    "omitido": (":gray[:material/remove:]", "No se ejecutó"),
}


def linea_de_tiempo(pasos: list[fmt.Paso]) -> None:
    with st.container(border=True):
        st.markdown("**Pasos en Step Functions**")
        for paso in pasos:
            icono, estado = ESTADO_PASO[paso.estado]
            c_icono, c_paso, c_estado = st.columns([0.4, 5, 2.4], vertical_alignment="center")
            c_icono.markdown(icono)
            c_paso.markdown(f"**{paso.etiqueta}**  \n:gray[{paso.descripcion}]")
            detalle = f" · {paso.detalle}" if paso.detalle and paso.detalle != "—" else ""
            c_estado.markdown(f"{estado}{detalle}")


ORIGEN_REDACCION = {"llm": "LLM", "plantilla": "Plantilla"}


def resumen_memo(texto: str) -> None:
    alertas = fmt.alertas_del_memo(texto)
    filas = fmt.indicadores_del_memo(texto)
    origen = fmt.metadatos_del_memo(texto).get("origen", "")
    c = st.columns(4)
    c[0].metric(
        "Capacidad de pago",
        fmt.valor_indicador(filas, "Capacidad de pago"),
        border=True,
        help="Flujo promedio mensual entre pago mensual estimado.",
    )
    c[1].metric(
        "Apalancamiento", fmt.valor_indicador(filas, "Apalancamiento"), border=True, help="Pasivo total entre capital."
    )
    c[2].metric("Alertas", len(alertas), border=True)
    c[3].metric(
        "Redacción",
        ORIGEN_REDACCION.get(origen, origen or "—"),
        border=True,
        help="LLM: texto del modelo que pasó el verificador de código. Plantilla: respaldo determinista.",
    )
    if alertas:
        st.markdown(
            "  ".join(
                insignia(
                    f"{a.codigo} · {a.titulo} · {a.severidad}",
                    fmt.COLOR_SEVERIDAD.get(a.severidad, "gray"),
                    ":material/warning:",
                )
                for a in alertas
            )
        )
    else:
        st.markdown(insignia("Sin alertas", "green", ":material/task_alt:"))


def vista_memo(texto: str) -> None:
    with st.container(border=True):
        st.markdown(fmt.para_mostrar(texto))


def vista_evidencia(indicadores: list[dict[str, Any]]) -> None:
    st.caption("Cada cifra la calcula el código con aritmética decimal exacta; el modelo solo la cita con un marcador.")
    for ind in indicadores:
        calculado = ind.get("estado") == "calculado"
        with st.expander(
            f"{ind.get('nombre')}: {ind.get('valor_texto')}",
            icon=":material/calculate:" if calculado else ":material/block:",
        ):
            st.markdown(f"**Fórmula:** {ind.get('formula')}")
            st.markdown(f"**En el memo:** {ind.get('fuente_memo')}")
            if ind.get("motivo"):
                st.markdown(f"**Por qué no se calculó:** {ind.get('motivo')}")
            if entradas := ind.get("entradas") or {}:
                st.dataframe(
                    [{"Entrada": k, "Valor": str(v)} for k, v in entradas.items()], hide_index=True, width="stretch"
                )


def vista_metadatos(texto: str) -> None:
    meta = fmt.metadatos_del_memo(texto)
    if not meta:
        st.caption("El memo no trae metadatos de ejecución.")
        return
    etiquetas = {
        "folio": "Folio",
        "ejecucion": "Ejecución",
        "modelo": "Modelo",
        "modo": "Modo de redacción",
        "origen": "Origen del texto",
        "prompt": "Versión del prompt",
        "commit": "Commit desplegado",
    }
    st.dataframe([{"Dato": etiquetas.get(k, k), "Valor": v} for k, v in meta.items()], hide_index=True, width="stretch")


# === Elección de expediente ====================================================================================


@dataclass(frozen=True)
class Eleccion:
    nombre: str
    archivos: dict[str, int]
    carpeta: Path | None = None
    contenido: dict[str, bytes] | None = None


ORIGENES = {"reto": "Casos del reto", "demo": "Casos de demo", "subir": "Subir archivos"}


def selector_expediente(clave: str) -> Eleccion | None:
    origen = st.radio(
        "Origen del expediente", list(ORIGENES), format_func=ORIGENES.get, horizontal=True, key=f"{clave}-origen"
    )
    if origen == "subir":
        subidos = st.file_uploader(
            "Documentos del expediente",
            type=["pdf", "json"],
            accept_multiple_files=True,
            key=f"{clave}-archivos",
            help="estado_cuenta_AAAA-MM.pdf (uno por mes), constancia_situacion_fiscal.pdf, "
            "estados_financieros_AAAA.pdf y solicitud.json. Hasta 5 MB por archivo.",
        )
        if not subidos:
            st.caption("Arrastra los PDF y la solicitud.json, o pulsa para elegirlos.")
            return None
        contenido = {f.name: f.getvalue() for f in subidos}
        return Eleccion("Archivos subidos", {n: len(b) for n, b in contenido.items()}, contenido=contenido)
    base = srv.DATOS_RETO if origen == "reto" else srv.DATOS_DEMO
    expedientes = {e.nombre: e for e in srv.listar_expedientes(base)}
    if not expedientes:
        generador = srv.RAIZ / "demo" / "generar_casos.py"
        como = (
            "Genéralos con `uv run python demo/generar_casos.py`."
            if generador.exists()
            else "Pon cada expediente en su carpeta: `demo/casos/<FOLIO>/`."
        )
        st.info(f"Todavía no hay casos de demo. {como}", icon=":material/info:")
        return None
    nombre = st.selectbox(
        "Caso",
        list(expedientes),
        format_func=lambda n: f"{n} · {expedientes[n].razon_social}" if expedientes[n].razon_social else n,
        key=f"{clave}-caso-{origen}",
    )
    exp = expedientes[nombre]
    if exp.credito:
        destino = fmt.escapar_md(str(exp.credito.get("destino", "")))
        monto = fmt.escapar_md(fmt.moneda(exp.credito.get("monto_solicitado")))
        st.caption(f"Pide {monto} a {exp.credito.get('plazo_meses')} meses · {destino}")
    return Eleccion(exp.nombre, exp.archivos, carpeta=exp.ruta)


def vista_archivos(eleccion: Eleccion, cfg: Config) -> bool:
    """Lista de verificación y problemas junto al campo; devuelve False si algo bloquearía el envío."""
    problemas = fmt.validar_archivos(eleccion.archivos, cfg.limites)
    with st.container(border=True):
        for req in fmt.requisitos(eleccion.archivos, cfg.politica.meses_flujo):
            icono = ":green[:material/check_circle:]" if req.cumple else ":orange[:material/warning:]"
            estado = "listo" if req.cumple else "falta"
            st.markdown(f"{icono} **{req.nombre}** · {estado} · :gray[{fmt.escapar_md(req.detalle)}]")
    for p in problemas:
        aviso = st.error if p.bloquea else st.warning
        aviso(
            f"**{fmt.escapar_md(p.archivo)}**: {p.mensaje}",
            icon=":material/error:" if p.bloquea else ":material/warning:",
        )
    with st.expander(f"{len(eleccion.archivos)} archivos"):
        st.dataframe(
            [{"Archivo": n, "Tamaño": fmt.tamano(b)} for n, b in sorted(eleccion.archivos.items())],
            hide_index=True,
            width="stretch",
        )
    return not any(p.bloquea for p in problemas)


# === Páginas ===================================================================================================


def pagina_enviar() -> None:
    encabezado(
        "Enviar expediente",
        "Sube un expediente a dev. Los documentos van primero y el manifiesto al final: el manifiesto dispara el flujo.",
    )
    con = requiere_conexion()
    if con is None:
        return
    cfg = config()
    izq, der = st.columns([3, 2], gap="large")
    with izq:
        st.subheader("1 · Expediente", anchor=False)
        eleccion = selector_expediente("enviar")
        valido = eleccion is not None and vista_archivos(eleccion, cfg)
    with der:
        st.subheader("2 · Envío", anchor=False)
        if st.session_state.pop("renovar_folio", False) or "enviar-folio" not in st.session_state:
            st.session_state["enviar-folio"] = fmt.sugerir_folio(datetime.now())
        folio = st.text_input(
            "Folio *",
            key="enviar-folio",
            max_chars=45,
            help="Usa uno nuevo en cada envío: el mismo folio con los mismos documentos se deduplica.",
        ).strip()
        if error_folio := fmt.validar_folio(folio):
            st.error(error_folio, icon=":material/error:")
        modo = st.radio(
            "Redacción",
            ["simulado", "bedrock"],
            format_func={"simulado": "LLM simulado · sin costo", "bedrock": "Bedrock (Claude) · unos centavos"}.get,
            key="enviar-modo",
        )
        with st.container(border=True):
            st.markdown("**Qué pasa al enviar**")
            st.markdown(
                "1. Se suben los documentos a `entrada/<folio>/`: todavía no pasa nada.\n"
                "2. Se sube `_manifest.json` con el SHA-256 de cada documento.\n"
                "3. EventBridge ve el manifiesto, la ingesta lo valida y arranca Step Functions."
            )
        if st.button(
            "Enviar a dev",
            type="primary",
            icon=":material/send:",
            disabled=not (valido and not error_folio),
            width="stretch",
        ):
            assert eleccion is not None
            with st.spinner("Subiendo documentos y manifiesto…"):
                enviado = enviar(con, eleccion, folio, str(modo))
            if enviado:
                recientes: list[str] = st.session_state.setdefault("recientes", [])
                recientes.insert(0, enviado)
                st.session_state["renovar_folio"] = True
                st.session_state["folio"] = enviado
                ir_a("seguimiento", enviado)


def enviar(con: srv.Conexion, eleccion: Eleccion, folio: str, modo: str) -> str | None:
    try:
        if eleccion.carpeta is not None:
            return srv.enviar(con, eleccion.carpeta, folio, modo)
        with srv.carpeta_temporal(eleccion.contenido or {}) as carpeta:
            return srv.enviar(con, carpeta, folio, modo)
    except (BotoCoreError, ClientError, SystemExit) as exc:
        st.error(f"No se pudo enviar. {mensaje_aws(exc)}", icon=":material/error:")
        return None


def pagina_seguimiento() -> None:
    encabezado("Seguimiento", "Cada paso de Step Functions para un folio. Se actualiza solo mientras el flujo avanza.")
    con = requiere_conexion()
    if con is None:
        return
    izq, der = st.columns([3, 1], vertical_alignment="bottom")
    folio = izq.text_input("Folio", value=folio_de_la_url(), placeholder="Por ejemplo, EXI-0102").strip()
    der.button("Consultar", icon=":material/search:", width="stretch")
    if recientes := st.session_state.get("recientes"):
        st.caption("Enviados en esta sesión")
        columnas = st.columns(min(len(recientes), 4))
        for columna, reciente in zip(columnas, recientes[:4], strict=False):
            columna.page_link(
                PAGINAS["seguimiento"], label=reciente, icon=":material/history:", query_params={"folio": reciente}
            )
    if not folio:
        vacio(
            "Escribe un folio para seguirlo.",
            "O envía un expediente: al terminar, la consola te trae aquí.",
            "enviar",
            "Enviar un expediente",
            ":material/upload_file:",
        )
        return
    if error := fmt.validar_folio(folio):
        st.error(error, icon=":material/error:")
        return
    if st.query_params.get("folio") != folio:
        st.query_params["folio"] = folio
    panel_flujo(con, folio)


def panel_flujo(con: srv.Conexion, folio: str) -> None:
    clave = f"vivo-{folio}"
    vivo = bool(st.session_state.get(clave, True))

    @st.fragment(run_every=INTERVALO_S if vivo else None)
    def panel() -> None:
        item = con_aws("leer el estado", lambda: srv.estado(con, folio))
        if item is None:
            return
        if not item:
            visto = st.session_state.setdefault(f"visto-{folio}", time.monotonic())
            if time.monotonic() - visto > 60:
                st.warning(
                    "No hay registro de este folio. Revisa que esté bien escrito y que el manifiesto se haya subido.",
                    icon=":material/search_off:",
                )
                detener(clave, vivo)
            else:
                st.info(
                    "Esperando a la ingesta: el estado aparece uno o dos segundos después del manifiesto.",
                    icon=":material/hourglass_top:",
                )
            return
        estado = str(item.get("estado") or "")
        tarjeta_estado(folio, item)
        if ejecucion := str(item.get("ejecucion") or ""):
            if historial := con_aws("leer el historial de Step Functions", lambda: srv.historial(con, ejecucion)):
                estado_ejecucion, eventos = historial
                linea_de_tiempo(fmt.pasos_del_flujo(fmt.resumir_historial(eventos), estado_ejecucion))
            st.link_button(
                "Abrir la ejecución en la consola de AWS",
                fmt.url_ejecucion(con.region, srv.arn_ejecucion(con, ejecucion)),
                icon=":material/open_in_new:",
            )
        if estado == "PENDIENTE_REVISION" and st.button(
            "Revisar el memo", type="primary", icon=":material/rate_review:", key=f"revisar-{folio}"
        ):
            ir_a("revisar", folio)
        if estado in fmt.ESTADOS_EN_REPOSO:
            detener(clave, vivo)

    panel()
    if not vivo and st.button("Actualizar", icon=":material/refresh:", key=f"actualizar-{folio}"):
        st.session_state[clave] = True
        st.rerun()


def detener(clave: str, vivo: bool) -> None:
    """Deja de refrescar: no hay nada que esperar en segundo plano."""
    if vivo:
        st.session_state[clave] = False
        st.rerun()


def pagina_bandeja() -> None:
    encabezado("Bandeja de revisión", "Expedientes con el borrador listo que esperan a un analista.")
    con = requiere_conexion()
    if con is None:
        return
    izq, der = st.columns([3, 1], vertical_alignment="bottom")
    ocultar = izq.toggle("Ocultar folios de pruebas automáticas (IT-, LT-)", value=True)
    if der.button("Actualizar", icon=":material/refresh:", width="stretch"):
        pendientes_cache.clear()
    filas = con_aws("leer la bandeja", lambda: pendientes_cache(con))
    if filas is None:
        return
    visibles = [f for f in filas if not (ocultar and fmt.es_folio_de_prueba(f.folio))]
    ocultos = len(filas) - len(visibles)
    st.caption(f"{len(visibles)} por revisar" + (f" · {ocultos} de pruebas ocultos" if ocultos else ""))
    if not visibles:
        vacio(
            "No hay expedientes por revisar.",
            "Cuando un expediente llegue a revisión aparecerá aquí.",
            "enviar",
            "Enviar un expediente",
            ":material/upload_file:",
        )
        return
    momento = ahora()
    seleccion = st.dataframe(
        [
            {"Folio": f.folio, "Pendiente desde": fmt.hace(f.desde, momento), "Borrador (sha256)": f.memo_sha256[:12]}
            for f in visibles
        ],
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key="bandeja-tabla",
    )
    filas_elegidas = seleccion.selection.rows
    elegido = visibles[filas_elegidas[0]].folio if filas_elegidas else None
    if st.button("Revisar el seleccionado", type="primary", icon=":material/rate_review:", disabled=elegido is None):
        ir_a("revisar", elegido)
    if elegido is None:
        st.caption("Selecciona una fila para revisarla.")


@dataclass(frozen=True)
class Revision:
    decision: str
    calificaciones: dict[str, int]
    categoria: str
    comentario: str
    memo_final: str | None


def pagina_revisar() -> None:
    encabezado(
        "Revisar memo",
        "Lee el borrador, revisa de dónde sale cada cifra y registra tu decisión. El sistema no aprueba créditos.",
    )
    con = requiere_conexion()
    if con is None:
        return
    if aviso := st.session_state.pop("aviso_revision", None):
        st.success(aviso, icon=":material/task_alt:")
    filas = con_aws("leer la bandeja", lambda: pendientes_cache(con)) or []
    opciones = [f.folio for f in filas if not fmt.es_folio_de_prueba(f.folio)]
    actual = folio_de_la_url()
    if actual and actual not in opciones:
        opciones.insert(0, actual)
    if not opciones:
        vacio(
            "No hay memos por revisar.",
            "Cuando un expediente llegue a revisión aparecerá en la bandeja.",
            "bandeja",
            "Ir a la bandeja",
            ":material/inbox:",
        )
        return
    folio = st.selectbox("Folio", opciones, index=opciones.index(actual) if actual in opciones else 0)
    if st.query_params.get("folio") != folio:
        st.query_params["folio"] = folio
    item = con_aws("leer el estado", lambda: srv.estado(con, folio))
    if item is None:
        return
    if item.get("estado") != "PENDIENTE_REVISION":
        tarjeta_estado(folio, item)
        st.page_link(
            PAGINAS["seguimiento"],
            label="Ver el flujo de este folio",
            icon=":material/account_tree:",
            query_params={"folio": folio},
        )
        return
    borrador = con_aws("descargar el borrador", lambda: borrador_cache(con, folio, str(item["memo_s3_key"])))
    if borrador is None:
        return
    resumen_memo(borrador.texto)
    izq, der = st.columns([3, 2], gap="large")
    with izq:
        t_memo, t_evidencia, t_ejecucion = st.tabs(["Memo", "Evidencia de cifras", "Ejecución"])
        with t_memo:
            vista_memo(borrador.texto)
        with t_evidencia:
            ejecucion = str(item.get("ejecucion") or "")
            analisis = con_aws("leer la evidencia en la auditoría", lambda: analisis_cache(con, folio, ejecucion))
            if analisis:
                vista_evidencia(list(analisis.get("indicadores", [])))
        with t_ejecucion:
            vista_metadatos(borrador.texto)
            st.page_link(
                PAGINAS["seguimiento"],
                label="Ver el flujo en Step Functions",
                icon=":material/account_tree:",
                query_params={"folio": folio},
            )
    with der:
        formulario_revision(con, folio, borrador)


def formulario_revision(con: srv.Conexion, folio: str, borrador: srv.Borrador) -> None:
    with st.container(border=True):
        st.subheader("Tu revisión", anchor=False)
        st.caption(
            f"Queda firmada como **{fmt.escapar_md(fmt.nombre_corto(con.identidad.arn))}**: la identidad la impone IAM "
            "y el registro entra a la auditoría inmutable."
        )
        with st.form(f"revision-{folio}", border=False):
            decision = st.radio(
                "Decisión *",
                list(fmt.DECISIONES),
                format_func=fmt.DECISIONES.get,
                help="No existe «aprobado»: el sistema prepara y el comité decide.",
            )
            st.markdown("**Calificación por sección** · opcional, de 1 (mala) a 5 (excelente)")
            calificaciones: dict[str, int] = {}
            for seccion, etiqueta in fmt.SECCIONES.items():
                valor = st.segmented_control(etiqueta, [1, 2, 3, 4, 5], key=f"calificacion-{folio}-{seccion}")
                if valor:
                    calificaciones[seccion] = int(valor)
            categoria = st.selectbox(
                "Error principal del borrador",
                list(fmt.CATEGORIAS_ERROR),
                format_func=fmt.CATEGORIAS_ERROR.get,
                help="Alimenta los evals: los errores vuelven como casos de prueba.",
            )
            comentario = st.text_area(
                "Comentario",
                max_chars=2000,
                placeholder="Por ejemplo: pedir la constancia correcta y el estado de cuenta original de agosto.",
                help="Queda en la auditoría. No escribas datos personales.",
            )
            with st.expander("Corregir el texto del memo (opcional)", icon=":material/edit:"):
                editado = st.text_area(
                    "Texto final del memo", value=borrador.texto, height=320, max_chars=60000, key=f"memo-{folio}"
                )
            enviado = st.form_submit_button(
                "Registrar revisión", type="primary", icon=":material/verified:", width="stretch"
            )
        if enviado:
            revision = Revision(
                str(decision),
                calificaciones,
                str(categoria),
                comentario.strip(),
                editado if editado != borrador.texto else None,
            )
            confirmar_revision(con, folio, revision)


@st.dialog("Confirmar revisión", width="medium")
def confirmar_revision(con: srv.Conexion, folio: str, revision: Revision) -> None:
    st.markdown(f"Vas a registrar **{fmt.DECISIONES[revision.decision]}** para **{folio}**.")
    detalles = [
        f"Secciones calificadas: {len(revision.calificaciones)} de {len(fmt.SECCIONES)}",
        f"Error principal: {fmt.CATEGORIAS_ERROR[revision.categoria]}",
        "Texto del memo corregido" if revision.memo_final is not None else "Sin cambios al texto del memo",
    ]
    st.markdown("\n".join(f"- {d}" for d in detalles))
    st.caption("Queda firmada con tu identidad de IAM y entra a la auditoría inmutable: no se puede deshacer.")
    cancelar, registrar = st.columns(2)
    if cancelar.button("Cancelar", width="stretch"):
        st.rerun()
    if registrar.button("Registrar", type="primary", icon=":material/verified:", width="stretch"):
        try:
            with st.spinner("Registrando y esperando a que el flujo la aplique…"):
                srv.registrar_revision(
                    con,
                    folio,
                    revision.decision,
                    revision.calificaciones,
                    revision.categoria,
                    revision.comentario,
                    revision.memo_final,
                )
                item = srv.esperar_estado(con, folio, set(fmt.ESTADOS_FINALES), timeout_s=60)
        except srv.EstadoNoPendiente as exc:
            st.error(f"El folio ya no está pendiente (estado {exc}). Vuelve a la bandeja.", icon=":material/error:")
            return
        except (BotoCoreError, ClientError) as exc:
            st.error(f"No se pudo registrar. {mensaje_aws(exc)}", icon=":material/error:")
            return
        final = fmt.ESTADOS.get(str(item.get("estado")))
        resultado = final.etiqueta if final else "la revisión sigue aplicándose; mírala en Seguimiento"
        st.session_state["aviso_revision"] = f"Revisión de {folio} registrada: {resultado}."
        pendientes_cache.clear()
        st.rerun()


def pagina_local() -> None:
    encabezado(
        "Modo local",
        "Procesa un expediente en tu equipo con el LLM simulado: nada sale de tu máquina y no necesitas AWS.",
    )
    cfg = config()
    izq, der = st.columns([3, 2], gap="large")
    with izq:
        st.subheader("Expediente", anchor=False)
        eleccion = selector_expediente("local")
        valido = eleccion is not None and vista_archivos(eleccion, cfg)
    with der:
        with st.container(border=True):
            st.markdown("**Qué hace**")
            st.markdown(
                "- Extrae, concilia y calcula con el mismo código que la nube.\n"
                "- Redacta con el LLM simulado y pasa el verificador.\n"
                "- Escribe el memo y el paquete de auditoría en `salidas/`."
            )
        if st.button(
            "Procesar en local", type="primary", icon=":material/play_arrow:", disabled=not valido, width="stretch"
        ):
            assert eleccion is not None
            with st.spinner("Procesando…"):
                st.session_state["resultado_local"] = procesar_local(eleccion)
    resultado: srv.ResultadoLocal | None = st.session_state.get("resultado_local")
    if resultado is None:
        return
    st.divider()
    st.subheader(f"Resultado · {resultado.folio}", anchor=False)
    resumen_memo(resultado.memo)
    t_memo, t_evidencia, t_auditoria = st.tabs(["Memo", "Evidencia de cifras", "Auditoría"])
    with t_memo:
        vista_memo(resultado.memo)
    with t_evidencia:
        vista_evidencia(resultado.indicadores)
    with t_auditoria:
        if resultado.auditoria:
            st.error("El paquete de auditoría no pasó la verificación: " + "; ".join(resultado.auditoria))
        else:
            st.markdown(insignia("Paquete íntegro", "green", ":material/verified:"))
            st.caption(
                f"Ejecución {resultado.ejecucion}: los hashes de cada parte y la cadena coinciden. "
                f"Está en `salidas/{resultado.folio}/auditoria/{resultado.ejecucion}/`."
            )


def procesar_local(eleccion: Eleccion) -> srv.ResultadoLocal | None:
    try:
        if eleccion.carpeta is not None:
            return srv.procesar_local(eleccion.carpeta)
        with srv.carpeta_temporal(eleccion.contenido or {}) as carpeta:
            return srv.procesar_local(carpeta)
    except Exception as exc:  # el pipeline rechaza expedientes inválidos con excepciones de dominio
        st.error(f"No se pudo procesar el expediente ({type(exc).__name__}): {exc}", icon=":material/error:")
        return None


def barra_lateral(con: srv.Conexion | None, error: str) -> None:
    with st.sidebar:
        st.markdown("**memo-crédito** · consola del analista")
        if con is not None:
            st.markdown(insignia(f"Conectado a {con.entorno}", "green", ":material/cloud_done:"))
            st.caption(f"Revisas como **{fmt.escapar_md(fmt.nombre_corto(con.identidad.arn))}**")
            perfil = f" · perfil {con.perfil}" if con.perfil else ""
            st.caption(f"Cuenta {fmt.enmascarar_cuenta(con.identidad.cuenta)} · {con.region}{perfil}")
        else:
            st.markdown(insignia("Sin conexión a AWS", "red", ":material/cloud_off:"))
            st.caption(error)
            if st.button("Reintentar", icon=":material/refresh:", width="stretch"):
                _conexion.clear()
                st.rerun()
        st.caption("El sistema prepara el borrador; el comité decide.")


# === Arranque ==================================================================================================

PAGINAS.update(
    enviar=st.Page(pagina_enviar, title="Enviar expediente", icon=":material/upload_file:", default=True),
    seguimiento=st.Page(
        pagina_seguimiento, title="Seguimiento", icon=":material/account_tree:", url_path="seguimiento"
    ),
    bandeja=st.Page(pagina_bandeja, title="Bandeja de revisión", icon=":material/inbox:", url_path="bandeja"),
    revisar=st.Page(pagina_revisar, title="Revisar memo", icon=":material/rate_review:", url_path="revisar"),
    local=st.Page(pagina_local, title="Modo local", icon=":material/computer:", url_path="local"),
)
navegacion = st.navigation(
    {
        f"En AWS · {ENTORNO}": [PAGINAS[p] for p in ("enviar", "seguimiento", "bandeja", "revisar")],
        "Sin AWS": [PAGINAS["local"]],
    }
)
barra_lateral(*obtener_conexion())
navegacion.run()
