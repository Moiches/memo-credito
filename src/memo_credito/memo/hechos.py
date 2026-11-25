"""Construye el JSON de hechos (hechos/v1) desde el análisis. Es lo único que verá el LLM.

- Toda cifra va como texto ya formateado en `cifras`; el LLM la cita con {{id}} y el código la sustituye.
- Sin PII (contacto, representante legal, domicilio, cuentas) ni texto crudo (conceptos, líneas de documentos).
- Los textos cortos permitidos (razón social, actividad, destino) se escanean; si parecen instrucciones se omiten.
"""

from __future__ import annotations

from decimal import Decimal

from memo_credito.config import Config
from memo_credito.dominio.formato import (
    formato_millones,
    formato_monto,
    formato_pct,
    formato_razon,
    nombre_mes,
    redondear,
)
from memo_credito.dominio.modelos import Analisis, Expediente
from memo_credito.extraccion.expediente import detector_de
from memo_credito.memo.modelos import AlertaHecho, HechosParaLLM, IndicadorHecho
from memo_credito.validacion.periodos import periodos_esperados

OMITIDO = "[omitido por seguridad]"


class HechosDemasiadoGrandes(ValueError):
    pass


def _periodo_revisado(analisis: Analisis) -> str:
    esperados = analisis.periodos_esperados
    presentes = set(analisis.periodos_presentes)
    con = [p for p in esperados if p.clave in presentes]
    sin = [p for p in esperados if p.clave not in presentes]
    anio = esperados[-1].anio
    if not sin:
        return f"{nombre_mes(esperados[0].mes)} a {nombre_mes(esperados[-1].mes)} de {anio}"
    nombres = [nombre_mes(p.mes) for p in con]
    texto = " y ".join(nombres) if nombres else "ningún mes"
    return f"{texto} de {anio} (falta {', '.join(nombre_mes(p.mes) for p in sin)})"


def _limpio(texto: str, cfg: Config) -> str:
    deteccion = detector_de(cfg).evaluar(texto)
    return OMITIDO if deteccion.dispara or len(texto) > cfg.limites.texto_libre_max else texto


def construir_hechos(exp: Expediente, analisis: Analisis, cfg: Config) -> HechosParaLLM:
    sol, cons, ef, d = exp.solicitud, exp.constancia, exp.estados_financieros, analisis.derivados
    pol = cfg.politica
    cifras: dict[str, str] = {
        "monto_solicitado": formato_monto(sol.credito.monto_solicitado),
        "plazo_meses": str(sol.credito.plazo_meses),
        "n_meses_revisados": str(len(periodos_esperados(sol.fecha_solicitud, pol.meses_flujo))),
        "tasa_referencia_anual": f"{redondear(pol.tasa_anual_referencia * 100, 0)}%",
        "umbral_capacidad_minima": formato_razon(pol.umbral_capacidad_minima),
        "umbral_capacidad_holgura": formato_razon(pol.umbral_capacidad_holgura),
        "umbral_apalancamiento": formato_razon(pol.umbral_apalancamiento),
        "n_alertas": str(len(analisis.alertas)),
    }
    indicadores: dict[str, IndicadorHecho] = {}
    for ind in analisis.indicadores:
        indicadores[ind.id] = IndicadorHecho(estado=ind.estado, motivo=ind.motivo)
        if ind.valor is not None:
            cifras[ind.id] = ind.valor_texto
    if d.antiguedad_anios is not None:
        cifras["antiguedad_anios"] = str(d.antiguedad_anios)
    if d.ventas_netas is not None:
        cifras["ventas_netas"] = formato_monto(d.ventas_netas)
        cifras["ventas_netas_mm"] = formato_millones(d.ventas_netas)
    if d.utilidad_neta is not None:
        cifras["utilidad_neta"] = formato_monto(d.utilidad_neta)
        cifras["utilidad_neta_mm"] = formato_millones(d.utilidad_neta)
    if d.monto_pct_ventas is not None:
        cifras["monto_pct_ventas"] = formato_pct(d.monto_pct_ventas)
        cifras["monto_pct_ventas_entero"] = f"{redondear(d.monto_pct_ventas, 0)}%"
    if d.pct_abonos_efectivo is not None:
        cifras["pct_abonos_efectivo"] = formato_pct(d.pct_abonos_efectivo)
    if d.depositos_atipicos is not None:
        cifras["n_depositos_atipicos"] = str(len(d.depositos_atipicos))
    if ef is not None:
        cifras["ejercicio_eeff"] = str(ef.ejercicio)

    actividad = cons.actividad_economica.valor if cons else ""
    actividad = actividad[:1].lower() + actividad[1:] if actividad else ""
    textos: dict[str, str] = {"periodo_revisado": _periodo_revisado(analisis)}
    if actividad:
        textos["actividad_economica"] = _limpio(actividad, cfg)
    textos["destino"] = _limpio(sol.credito.destino, cfg)
    textos["razon_social"] = _limpio(sol.solicitante.razon_social, cfg)
    if d.mes_flujo_mas_alto is not None:
        textos["mes_flujo_mas_alto"] = nombre_mes(d.mes_flujo_mas_alto.mes)
    if d.mes_flujo_mas_bajo is not None:
        textos["mes_flujo_mas_bajo"] = nombre_mes(d.mes_flujo_mas_bajo.mes)
    motivo_flujo = analisis.indicador("flujo_promedio").motivo
    if motivo_flujo:
        textos["motivo_flujo_promedio"] = motivo_flujo

    hechos = HechosParaLLM(
        folio=sol.folio,
        empresa={"razon_social": textos["razon_social"], "actividad_economica": textos.get("actividad_economica", "")},
        credito={"destino": textos["destino"]},
        cifras=cifras,
        textos=textos,
        indicadores=indicadores,
        hechos={
            "eeff_dictaminados": d.eeff_dictaminados,
            "constancia_estatus": d.constancia_estatus,
            "flujos_positivos": d.flujos_positivos,
            "hay_depositos_atipicos": bool(d.depositos_atipicos) if d.depositos_atipicos is not None else None,
            "documentos_completos": all(i.estado == "calculado" for i in analisis.indicadores),
        },
        alertas=tuple(
            AlertaHecho(codigo=a.codigo, categoria=a.categoria, severidad=a.severidad, titulo=a.titulo)
            for a in analisis.alertas
        ),
    )
    if len(hechos.model_dump_json().encode("utf-8")) > cfg.limites.hechos_max_bytes:
        raise HechosDemasiadoGrandes("los hechos exceden el tamaño máximo")
    return hechos


def valores_numericos_permitidos(hechos: HechosParaLLM) -> set[str]:
    """Todos los tokens numéricos que pueden aparecer en las secciones redactadas."""
    from memo_credito.memo.verificador import tokens_numericos

    permitidos: set[str] = set()
    for valor in (*hechos.cifras.values(), *hechos.textos.values()):
        permitidos |= tokens_numericos(valor)
    return permitidos


__all__ = ["OMITIDO", "Decimal", "HechosDemasiadoGrandes", "construir_hechos", "valores_numericos_permitidos"]
