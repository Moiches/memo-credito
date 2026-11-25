"""Verificador determinista del memo (el evaluador del ciclo evaluador-optimizador).

Reglas (§10.6 del plan). Los errores NUNCA incluyen el valor detectado, solo la regla y la ubicación, para no
reinyectar PII en la petición de corrección.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from memo_credito.dominio.alertas import CATALOGO
from memo_credito.memo.ensamblador import FRASE_FINAL, RE_MARCADOR, TITULOS, renderizar, secciones_redactadas
from memo_credito.memo.modelos import ErrorVerificacion, HechosParaLLM, SalidaLLM

RE_MARCADOR_CUALQUIERA = re.compile(r"\{\{(.*?)\}\}")
# Los códigos del catálogo (A03, S01…) son identificadores, no cifras: pueden citarse en la prosa si existen en el caso
RE_CODIGO_ALERTA = re.compile(r"\b(?:" + "|".join(sorted(CATALOGO)) + r")\b")
RE_DIGITO = re.compile(r"\d")
RE_PALABRAS_NUMERO = re.compile(
    r"\b(dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|veinte|treinta|cuarenta|cincuenta|sesenta|"
    r"setenta|ochenta|noventa|cien|ciento|cientos|mil|millon|millones|mitad|doble|triple|cuadruple)\b"
)
RE_RECOMENDACION = re.compile(
    r"\b(aprob\w*|aprueb\w*|rechaz\w*|autoriz\w*|otorg\w*|deneg\w*|negar|recomend\w*|recomiend\w*|sugier\w*|"
    r"sugerimos|sugerir|aconsej\w*|viable|viables|inviable|favorable|favorables|desfavorable|procedente|improcedente)\b"
)
RE_CURP = re.compile(r"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z\d]\d\b")
RE_RFC = re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z\d]{3}\b")
RE_TELEFONO = re.compile(r"(?<!\d)(?:\+?52[\s-]?)?(?:\d{2}[\s-]?\d{4}[\s-]?\d{4}|\d{3}[\s-]?\d{3}[\s-]?\d{4})(?!\d)")
RE_CORREO = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
RE_DOMICILIO = re.compile(r"\b(CALLE|AV\.|AVENIDA|COL\.|COLONIA|C\.P\.|CP \d{5}|INT \d+|NUM\. \d+)", re.IGNORECASE)
RE_INSEGURO = re.compile(r"(https?://|www\.|\]\(|!\[|<[a-zA-Z/!]|```|^\s*#|^\s*\|)", re.MULTILINE)
RE_TOKEN_NUMERICO = re.compile(r"\d[\d,]*(?:\.\d+)?")
RE_SECUENCIA_DIGITOS = re.compile(r"\d[\d\s-]{6,}\d")

LIMITES = {"resumen": 900, "vineta": 400, "obs_min": 2, "obs_max": 5, "puntos_min": 1, "puntos_max": 4}


def normalizar(texto: str) -> str:
    sin_acentos = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return " ".join(sin_acentos.casefold().split())


def tokens_numericos(texto: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in RE_TOKEN_NUMERICO.finditer(texto)}


def _textos(salida: SalidaLLM) -> Iterable[tuple[str, str]]:
    yield "resumen", salida.resumen
    for i, v in enumerate(salida.observaciones):
        yield f"observaciones[{i}]", v.texto
    for i, v in enumerate(salida.puntos_comite):
        yield f"puntos_comite[{i}]", v.texto


def verificar_salida(salida: SalidaLLM, hechos: HechosParaLLM) -> list[ErrorVerificacion]:
    errores: list[ErrorVerificacion] = []
    referencias = hechos.referencias

    def err(regla: str, detalle: str, ubicacion: str) -> None:
        errores.append(ErrorVerificacion(regla=regla, detalle=detalle, ubicacion=ubicacion))

    for ubicacion, texto in _textos(salida):
        for m in RE_MARCADOR_CUALQUIERA.finditer(texto):
            contenido = m.group(1).strip()
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", contenido):
                err(
                    "marcador_invalido",
                    "un marcador no es un id válido de hechos.cifras; usa {{id}} sin valores",
                    ubicacion,
                )
            elif contenido not in referencias:
                err(
                    "cifra_desconocida",
                    f"el marcador {{{{{contenido}}}}} no existe en hechos.cifras ni hechos.textos",
                    ubicacion,
                )
        sin_marcadores = RE_MARCADOR_CUALQUIERA.sub(" ", texto)
        for citado in sorted(set(RE_CODIGO_ALERTA.findall(sin_marcadores)) - {a.codigo for a in hechos.alertas}):
            err("alerta_inexistente", f"la alerta {citado} citada en el texto no existe en hechos.alertas", ubicacion)
        sin_marcadores = RE_CODIGO_ALERTA.sub(" ", sin_marcadores)
        if RE_DIGITO.search(sin_marcadores):
            err("cifra_literal", "hay dígitos fuera de un marcador {{id}}", ubicacion)
        if RE_PALABRAS_NUMERO.search(normalizar(sin_marcadores)):
            err("cifra_literal", "hay un número escrito con palabras", ubicacion)
        if RE_RECOMENDACION.search(normalizar(sin_marcadores)):
            err("lenguaje_recomendacion", "hay lenguaje de recomendación o dictamen sobre el crédito", ubicacion)
        if RE_INSEGURO.search(texto):
            err("formato_inseguro", "hay enlaces, imágenes, HTML, encabezados, tablas o bloques de código", ubicacion)
        if len(texto) > (LIMITES["resumen"] if ubicacion == "resumen" else LIMITES["vineta"]):
            err("estructura", "el texto excede la longitud máxima", ubicacion)

    codigos = {a.codigo for a in hechos.alertas}
    referidas = {c for v in (*salida.observaciones, *salida.puntos_comite) for c in v.alertas_referidas}
    for faltante in sorted(codigos - referidas):
        err("alerta_omitida", f"la alerta {faltante} no está referida en ninguna viñeta", "alertas_referidas")
    for sobrante in sorted(referidas - codigos):
        err("alerta_inexistente", f"la alerta {sobrante} no existe en hechos.alertas", "alertas_referidas")

    n_obs, n_pts = len(salida.observaciones), len(salida.puntos_comite)
    if not LIMITES["obs_min"] <= n_obs <= LIMITES["obs_max"]:
        err(
            "estructura",
            f"observaciones debe tener de {LIMITES['obs_min']} a {LIMITES['obs_max']} viñetas",
            "observaciones",
        )
    if not LIMITES["puntos_min"] <= n_pts <= LIMITES["puntos_max"]:
        err(
            "estructura",
            f"puntos_comite debe tener de {LIMITES['puntos_min']} a {LIMITES['puntos_max']} viñetas",
            "puntos_comite",
        )
    if not salida.resumen.strip():
        err("estructura", "el resumen está vacío", "resumen")
    return errores


def verificar_memo(memo: str, hechos: HechosParaLLM, canarios: tuple[str, ...] = ()) -> list[ErrorVerificacion]:
    """Reglas sobre el memo ya ensamblado: estructura, cifras, PII y canarios."""
    errores: list[ErrorVerificacion] = []

    def err(regla: str, detalle: str, ubicacion: str) -> None:
        errores.append(ErrorVerificacion(regla=regla, detalle=detalle, ubicacion=ubicacion))

    posiciones = [memo.find(t) for t in TITULOS]
    if any(p < 0 for p in posiciones) or posiciones != sorted(posiciones):
        err("estructura", "faltan secciones o están fuera de orden", "memo")
    if FRASE_FINAL not in memo:
        err("estructura", "falta la frase final del memo", "memo")

    redactado = secciones_redactadas(memo)
    redactado_sin_codigos = RE_CODIGO_ALERTA.sub(" ", redactado)
    permitidos: set[str] = set()
    for valor in (*hechos.cifras.values(), *hechos.textos.values()):
        permitidos |= tokens_numericos(valor)
    for token in tokens_numericos(redactado_sin_codigos):
        if token not in permitidos:
            err(
                "cifras_memo", "una cifra de las secciones redactadas no proviene de los hechos", "secciones redactadas"
            )
            break
    if RE_CURP.search(redactado) or RE_RFC.search(redactado):
        err("pii", "patrón de CURP o RFC en las secciones redactadas", "secciones redactadas")
    if RE_CORREO.search(redactado) or RE_DOMICILIO.search(redactado):
        err("pii", "patrón de correo o domicilio en las secciones redactadas", "secciones redactadas")
    if RE_TELEFONO.search(redactado):
        err("pii", "patrón de teléfono en las secciones redactadas", "secciones redactadas")
    if RE_CURP.search(memo):
        err("pii", "patrón de CURP en el memo", "memo")
    memo_normal = normalizar(memo)
    secuencias = {re.sub(r"[\s-]", "", s) for s in RE_SECUENCIA_DIGITOS.findall(memo)}
    for canario in canarios:
        if canario.isdigit():
            if any(canario in s for s in secuencias):
                err("pii", "un dato personal de la solicitud aparece en el memo", "memo")
                break
        elif canario in memo_normal:
            err("pii", "un dato personal de la solicitud aparece en el memo", "memo")
            break
    return errores


def verificar(
    salida: SalidaLLM | None, memo: str | None, hechos: HechosParaLLM, canarios: tuple[str, ...] = ()
) -> list[ErrorVerificacion]:
    if salida is None or memo is None:
        return [
            ErrorVerificacion(
                regla="salida_invalida", detalle="la respuesta no es un JSON conforme al esquema", ubicacion="respuesta"
            )
        ]
    return verificar_salida(salida, hechos) + verificar_memo(memo, hechos, canarios)


__all__ = [
    "RE_MARCADOR",
    "normalizar",
    "renderizar",
    "tokens_numericos",
    "verificar",
    "verificar_memo",
    "verificar_salida",
]
