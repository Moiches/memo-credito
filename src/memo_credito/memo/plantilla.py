"""Plantilla determinista: misma interfaz que el LLM (SalidaLLM con marcadores {{id}}).

Es a la vez el "LLM simulado" (decisión 3) y el respaldo cuando la redacción no pasa la verificación.
Nunca escribe dígitos ni lenguaje de recomendación: pasa el verificador por construcción.
"""

from __future__ import annotations

from memo_credito.memo.modelos import HechosParaLLM, SalidaLLM, Vineta

MAX_OBSERVACIONES = 5
MAX_PUNTOS = 4

OBSERVACION_POR_ALERTA = {
    "A01": "El RFC de un documento no coincide con el del resto; se conserva tal como viene para su aclaración.",
    "A02": "La razón social no coincide entre documentos.",
    "A03": "Falta un estado de cuenta del periodo revisado ({{periodo_revisado}}), por lo que el flujo promedio "
    "y la capacidad de pago no son calculables.",
    "A04": "Se recibió más de un estado de cuenta para el mismo mes.",
    "A05": "Se recibió un estado de cuenta fuera del periodo revisado; no se usó en los cálculos.",
    "A06": "Un estado de cuenta no concilia con sus totales; ese mes no se usa en los cálculos.",
    "A07": "Los saldos de meses consecutivos no encadenan.",
    "A08": "Los estados de cuenta corresponden a cuentas o titulares distintos.",
    "A09": "La constancia no tiene estatus activo.",
    "A10": "La constancia está fuera del plazo de vigencia de referencia.",
    "A11": "Los estados financieros no corresponden al último ejercicio esperado.",
    "A12": "Los estados financieros presentan diferencias aritméticas.",
    "A13": "El capital contable no es positivo, por lo que el apalancamiento no es calculable.",
    "A14": "Un RFC no tiene un formato válido.",
    "A15": "Los documentos recibidos no coinciden con los listados en la solicitud.",
    "A16": "El monto, el plazo o la moneda de la solicitud no son válidos.",
    "R01": "La capacidad de pago de {{capacidad_pago}} es menor a {{umbral_capacidad_minima}}: el flujo promedio "
    "no cubre la cuota estimada.",
    "R02": "La capacidad de pago de {{capacidad_pago}} está por debajo de la holgura de referencia de "
    "{{umbral_capacidad_holgura}}.",
    "R03": "El apalancamiento de {{apalancamiento}} es igual o mayor a la referencia de {{umbral_apalancamiento}}.",
    "R04": "Se observan depósitos atípicos respecto de la mediana de los abonos del periodo.",
    "S01": "Un documento contiene texto dirigido a un sistema automatizado; no se procesó y se reporta como señal de "
    "riesgo documental.",
    "S02": "Un archivo no se aceptó por no cumplir los requisitos de seguridad.",
    "S03": "Un documento contiene texto no visible que no se procesó.",
    "D01": "La solicitud incluyó información personal no requerida, que se descartó sin procesarse.",
}

PREGUNTAS = [
    ({"A01", "A02", "A14"}, "Aclarar con el solicitante la diferencia de identificación fiscal entre documentos."),
    ({"A03"}, "Solicitar el estado de cuenta faltante antes de evaluar la capacidad de pago."),
    (
        {"A04", "A05", "A06", "A07", "A08"},
        "Verificar con el solicitante los estados de cuenta observados en Validaciones.",
    ),
    ({"A09", "A10"}, "Solicitar una constancia de situación fiscal actualizada."),
    ({"A11", "A12", "A13"}, "Revisar los estados financieros observados en Validaciones."),
    ({"A15", "A16"}, "Confirmar con el solicitante los datos y documentos de la solicitud."),
    ({"R01", "R02"}, "Analizar la suficiencia del flujo frente a la cuota estimada y las fuentes adicionales de pago."),
    ({"R03"}, "Considerar el nivel de apalancamiento frente a la política de la institución."),
    ({"R04"}, "Verificar el origen de los depósitos atípicos."),
    (
        {"S01", "S03"},
        "Valorar el texto detectado en los documentos como señal de riesgo documental y confirmar su "
        "origen con el solicitante.",
    ),
    ({"S02"}, "Solicitar nuevamente los archivos que no se aceptaron."),
    ({"D01"}, "Confirmar que la información personal no requerida se descartó conforme a la política de datos."),
]


def _resumen(h: HechosParaLLM) -> str:
    c, t, hx = h.cifras, h.textos, h.hechos
    if "antiguedad_anios" in c and "actividad_economica" in t:
        inicio = "Empresa con {{antiguedad_anios}} años de operación en {{actividad_economica}}"
    else:
        inicio = "La empresa"
    if "flujo_promedio" in c:
        flujo = (
            "con flujo neto positivo en los {{n_meses_revisados}} meses revisados"
            if hx.get("flujos_positivos")
            else "con al menos un mes de flujo neto negativo en el periodo revisado"
        )
    else:
        flujo = "sin flujo promedio calculable para el periodo revisado"
    apal = " y apalancamiento de {{apalancamiento}}" if "apalancamiento" in c else ""
    texto = f"{inicio}, {flujo}{apal}."
    if "capacidad_pago" in c:
        texto += " La capacidad de pago estimada cubre {{capacidad_pago}} veces la cuota mensual."
    elif "motivo_flujo_promedio" in t:
        texto += " La capacidad de pago no es calculable porque el flujo promedio {{motivo_flujo_promedio}}."
    if not h.alertas:
        texto += " No se encontraron inconsistencias entre documentos."
    else:
        texto += " Se identificaron alertas que se detallan en Validaciones."
    return texto


def plantilla(h: HechosParaLLM) -> SalidaLLM:
    c, t, hx = h.cifras, h.textos, h.hechos
    codigos = [a.codigo for a in h.alertas]

    observaciones: list[Vineta] = []
    if {"ventas_netas_mm", "utilidad_neta_mm", "monto_pct_ventas_entero", "ejercicio_eeff"} <= set(c):
        observaciones.append(
            Vineta(
                texto=(
                    "Ventas {{ejercicio_eeff}} de {{ventas_netas_mm}} y utilidad neta de {{utilidad_neta_mm}}; el monto "
                    "solicitado equivale a cerca de {{monto_pct_ventas_entero}} de las ventas anuales."
                )
            )
        )
    if hx.get("eeff_dictaminados") is False:
        observaciones.append(Vineta(texto="Los estados financieros son internos y no están dictaminados."))
    elif hx.get("eeff_dictaminados") is True:
        observaciones.append(Vineta(texto="Los estados financieros están dictaminados."))
    if "mes_flujo_mas_alto" in t:
        atipicos = (
            "se observan depósitos atípicos (ver Validaciones)."
            if hx.get("hay_depositos_atipicos")
            else "no se observan depósitos atípicos."
        )
        observaciones.append(Vineta(texto="El flujo de {{mes_flujo_mas_alto}} es el más alto del periodo; " + atipicos))
    for codigo in codigos:
        if len(observaciones) >= MAX_OBSERVACIONES:
            break
        observaciones.append(Vineta(texto=OBSERVACION_POR_ALERTA[codigo], alertas_referidas=(codigo,)))
    if len(observaciones) < 2:
        observaciones.append(Vineta(texto="El periodo revisado comprende {{periodo_revisado}}."))

    puntos: list[Vineta] = []
    cubiertos: set[str] = set()
    for grupo, pregunta in PREGUNTAS:
        refs = tuple(cod for cod in codigos if cod in grupo)
        if refs and len(puntos) < MAX_PUNTOS - 1:
            puntos.append(Vineta(texto=pregunta, alertas_referidas=refs))
            cubiertos |= set(refs)
    pendientes = tuple(
        cod for cod in codigos if cod not in cubiertos and not any(cod in v.alertas_referidas for v in observaciones)
    )
    if pendientes:
        puntos.append(
            Vineta(texto="Revisar las demás alertas detalladas en Validaciones.", alertas_referidas=pendientes)
        )
    if len(puntos) < MAX_PUNTOS and "destino" in t:
        puntos.insert(
            0, Vineta(texto="Confirmar que el destino del crédito ({{destino}}) es consistente con el flujo observado.")
        )
    if len(puntos) < MAX_PUNTOS and hx.get("eeff_dictaminados") is False:
        puntos.append(Vineta(texto="Valorar si se requieren estados financieros dictaminados dado el monto."))
    return SalidaLLM(resumen=_resumen(h), observaciones=tuple(observaciones), puntos_comite=tuple(puntos))
