"""Catálogo de alertas (§8.2 del plan). Los umbrales viven en config/base.yaml."""

from __future__ import annotations

from typing import Literal

Categoria = Literal["integridad", "riesgo", "seguridad", "datos"]
Severidad = Literal["alta", "media", "baja", "info"]
CodigoAlerta = Literal[
    "A01",
    "A02",
    "A03",
    "A04",
    "A05",
    "A06",
    "A07",
    "A08",
    "A09",
    "A10",
    "A11",
    "A12",
    "A13",
    "A14",
    "A15",
    "A16",
    "R01",
    "R02",
    "R03",
    "R04",
    "S01",
    "S02",
    "S03",
    "D01",
]

# código -> (nombre estable, categoría, severidad por defecto, título corto para hechos/memo)
CATALOGO: dict[str, tuple[str, Categoria, Severidad, str]] = {
    "A01": ("RFC_INCONSISTENTE", "integridad", "alta", "RFC inconsistente entre documentos"),
    "A02": ("RAZON_SOCIAL_INCONSISTENTE", "integridad", "alta", "Razón social inconsistente"),
    "A03": ("PERIODO_FALTANTE", "integridad", "alta", "Estado de cuenta faltante"),
    "A04": ("PERIODO_DUPLICADO", "integridad", "alta", "Estado de cuenta duplicado"),
    "A05": ("PERIODO_NO_ESPERADO", "integridad", "media", "Estado de cuenta fuera del periodo"),
    "A06": ("CONCILIACION_FALLIDA", "integridad", "alta", "Estado de cuenta que no concilia"),
    "A07": ("SALDOS_NO_ENCADENAN", "integridad", "media", "Saldos que no encadenan"),
    "A08": ("CUENTA_O_TITULAR_DISTINTO", "integridad", "alta", "Cuentas o titulares distintos"),
    "A09": ("CONSTANCIA_NO_ACTIVA", "integridad", "alta", "Constancia sin estatus activo"),
    "A10": ("CONSTANCIA_FUERA_DE_VIGENCIA", "integridad", "media", "Constancia fuera de vigencia"),
    "A11": ("EJERCICIO_NO_ESPERADO", "integridad", "media", "Ejercicio de estados financieros no esperado"),
    "A12": ("ESTADOS_FINANCIEROS_NO_CUADRAN", "integridad", "alta", "Estados financieros que no cuadran"),
    "A13": ("CAPITAL_CONTABLE_NO_POSITIVO", "riesgo", "alta", "Capital contable no positivo"),
    "A14": ("FORMATO_RFC_INVALIDO", "integridad", "media", "RFC con formato inválido"),
    "A15": ("DOCUMENTOS_NO_COINCIDEN", "integridad", "media", "Documentos recibidos distintos a los listados"),
    "A16": ("MONTO_O_PLAZO_INVALIDO", "integridad", "alta", "Monto, plazo o moneda inválidos"),
    "R01": ("CAPACIDAD_INSUFICIENTE", "riesgo", "alta", "Capacidad de pago insuficiente"),
    "R02": ("CAPACIDAD_AJUSTADA", "riesgo", "media", "Capacidad de pago ajustada"),
    "R03": ("APALANCAMIENTO_ELEVADO", "riesgo", "media", "Apalancamiento elevado"),
    "R04": ("DEPOSITOS_ATIPICOS", "riesgo", "media", "Depósitos atípicos"),
    "S01": ("TEXTO_CON_FORMA_DE_INSTRUCCION", "seguridad", "alta", "Texto con forma de instrucción"),
    "S02": ("DOCUMENTO_RECHAZADO", "seguridad", "alta", "Documento rechazado"),
    "S03": ("TEXTO_OCULTO", "seguridad", "alta", "Texto oculto en un documento"),
    "D01": ("CAMPOS_NO_ESPERADOS", "datos", "info", "Campos no requeridos descartados"),
}

ORDEN = list(CATALOGO)
