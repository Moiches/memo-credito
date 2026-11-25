"""Configuración de infraestructura por entorno: config/base.yaml + config/{entorno}.yaml (sección `infra`)."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from memo_credito.config import Config, cargar_config

RAIZ = Path(__file__).resolve().parents[1]


class Costos(BaseModel):
    model_config = ConfigDict(frozen=True)
    umbral_memo_usd: Decimal
    umbral_hora_usd: Decimal
    presupuesto_mes_usd: Decimal


class InfraConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    vpc: bool
    concurrencia_reservada_redaccion: int | None
    map_max_concurrency: int
    revision_sla_s: int
    max_escalamientos: int
    retencion_logs_dias: int
    auditoria_modo: Literal["GOVERNANCE", "COMPLIANCE"]
    auditoria_dias: int
    ciclo_vida_entrada_dias: int
    ciclo_vida_trabajo_dias: int
    destruir_al_borrar: bool
    crear_perfil_aplicacion: bool
    data_protection_logs: bool
    costos: Costos
    suscriptores_alarmas: list[str] = []


class Entorno(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    nombre: str
    app: Config
    infra: InfraConfig

    @property
    def prefijo(self) -> str:
        return f"memo-credito-{self.nombre}"


def cargar_entorno(nombre: str) -> Entorno:
    app = cargar_config(nombre)
    return Entorno(nombre=nombre, app=app, infra=InfraConfig.model_validate(app.infra))


def directorio_asset_lambda() -> str:
    return os.environ.get("MEMO_LAMBDA_ASSET", str(RAIZ / "build" / "lambda"))
