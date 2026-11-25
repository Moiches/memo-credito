"""Carga tipada de config/base.yaml + config/{entorno}.yaml."""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict


class _Cfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Politica(_Cfg):
    meses_flujo: int = 3
    tasa_anual_referencia: Decimal
    umbral_capacidad_minima: Decimal
    umbral_capacidad_holgura: Decimal
    umbral_apalancamiento: Decimal
    factor_deposito_atipico: Decimal
    vigencia_constancia_dias: int


class Limites(_Cfg):
    pdf_max_bytes: int
    pdf_max_paginas: int
    json_max_bytes: int
    max_archivos: int
    texto_libre_max: int
    hechos_max_bytes: int
    max_caracteres_concepto: int


class Seguridad(_Cfg):
    patrones_instruccion: tuple[str, ...]


class ConfigLLM(_Cfg):
    modo: Literal["simulado", "bedrock", "plantilla"]
    permitir_override_modo: bool = False
    region: str
    modelo_id: str
    modelo_objetivo: str | None = None
    max_tokens: int
    effort: str | None
    max_correcciones: int
    prompt: str
    esquema: str


class PrecioModelo(_Cfg):
    entrada_mtok: Decimal
    salida_mtok: Decimal
    cache_lectura_mtok: Decimal
    cache_escritura_mtok: Decimal


class Precios(_Cfg):
    fuente: str
    modelos: dict[str, PrecioModelo]
    guardrail_por_1k_unidades: dict[str, Decimal]


class Config(_Cfg):
    entorno: str
    politica: Politica
    limites: Limites
    seguridad: Seguridad
    llm: ConfigLLM
    precios: Precios
    infra: dict[str, Any] = {}


def directorio_config() -> Path:
    if env := os.environ.get("MEMO_CONFIG_DIR"):
        return Path(env)
    repo = Path(__file__).resolve().parents[2] / "config"
    return repo if repo.exists() else Path.cwd() / "config"


def directorio_prompts() -> Path:
    if env := os.environ.get("MEMO_PROMPTS_DIR"):
        return Path(env)
    repo = Path(__file__).resolve().parents[2] / "prompts"
    return repo if repo.exists() else Path.cwd() / "prompts"


def _fusionar(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    salida = dict(base)
    for clave, valor in extra.items():
        if isinstance(valor, dict) and isinstance(salida.get(clave), dict):
            salida[clave] = _fusionar(salida[clave], valor)
        else:
            salida[clave] = valor
    return salida


def cargar_dict(entorno: str | None = None) -> dict[str, Any]:
    entorno = entorno or os.environ.get("ENTORNO", "dev")
    d = directorio_config()
    base = yaml.safe_load((d / "base.yaml").read_text(encoding="utf-8")) or {}
    ruta_env = d / f"{entorno}.yaml"
    extra = yaml.safe_load(ruta_env.read_text(encoding="utf-8")) if ruta_env.exists() else {}
    datos = _fusionar(base, extra or {})
    datos.setdefault("entorno", entorno)
    return datos


@lru_cache(maxsize=8)
def cargar_config(entorno: str | None = None) -> Config:
    return Config.model_validate(cargar_dict(entorno))
