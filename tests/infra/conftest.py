"""Síntesis de la infraestructura una sola vez por sesión (dev y prod) con cdk-nag activo.

Si cdk-nag encuentra una regla sin reconocer, `app.synth()` lanza ValidationFailed y todas las pruebas de infra fallan
con el reporte. El paquete de la Lambda se sustituye por un directorio mínimo (no hace falta construirlo para probar).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import pytest

ENTORNOS = ("dev", "prod")


@dataclass
class Sintesis:
    nombre: str
    entorno: Any
    stage: Any
    plantillas: dict[str, dict[str, Any]]

    def recursos(self, tipo: str) -> dict[str, dict[str, Any]]:
        return {
            f"{pila}/{lid}": r
            for pila, plantilla in self.plantillas.items()
            for lid, r in plantilla.get("Resources", {}).items()
            if r["Type"] == tipo
        }

    def json(self) -> str:
        return json.dumps(self.plantillas, ensure_ascii=False)


def texto_plano(valor: Any) -> str:
    """Une las partes literales de un Fn::Join (ignora tokens) para inspeccionar definiciones y ARNs."""
    if isinstance(valor, str):
        return valor
    if isinstance(valor, dict) and "Fn::Join" in valor:
        return "".join(texto_plano(v) for v in valor["Fn::Join"][1])
    if isinstance(valor, list):
        return "".join(texto_plano(v) for v in valor)
    return "<token>"


def sin_hash(logical_id: str) -> str:
    return re.sub(r"[0-9A-F]{8}$", "", logical_id)


@pytest.fixture(scope="session")
def sintesis(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Sintesis]:
    asset = tmp_path_factory.mktemp("lambda")
    (asset / "LEEME.txt").write_text("asset mínimo para pruebas de infraestructura", encoding="utf-8")
    os.environ["MEMO_LAMBDA_ASSET"] = str(asset)
    from aws_cdk import App

    from infra.config import cargar_entorno
    from infra.stage import MemoCreditoStage

    resultado = {}
    for nombre in ENTORNOS:
        app = App(outdir=str(tmp_path_factory.mktemp(f"cdk-{nombre}")))
        stage = MemoCreditoStage(app, f"MemoCredito-{nombre}", entorno=cargar_entorno(nombre))
        try:
            ensamblado = stage.synth()
        except Exception as exc:  # ValidationFailed de cdk-nag u otro error de síntesis
            pytest.fail(f"la síntesis de {nombre} falló (¿regla de cdk-nag sin reconocer?): {exc}")
        plantillas = {pila.stack_name: pila.template for pila in ensamblado.stacks}
        resultado[nombre] = Sintesis(nombre, cargar_entorno(nombre), stage, plantillas)
    return resultado
