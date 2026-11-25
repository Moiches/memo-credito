from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "datos" / "reto_ki_datos" / "solicitudes"
FOLIOS = [f"SOL-000{i}" for i in range(1, 9)]


@pytest.fixture(scope="session")
def casos() -> dict[str, Any]:
    return yaml.safe_load((RAIZ / "evals" / "casos.yaml").read_text(encoding="utf-8"))["casos"]


@pytest.fixture(scope="session")
def config():
    from memo_credito.config import cargar_config

    return cargar_config("dev")


@pytest.fixture(scope="session")
def expedientes(config):
    from memo_credito.extraccion.expediente import extraer_archivos, leer_carpeta

    return {folio: extraer_archivos(leer_carpeta(DATOS / folio), config)[0] for folio in FOLIOS}


@pytest.fixture(scope="session")
def analisis(expedientes, config):
    from memo_credito.calculos.analisis import analizar

    return {folio: analizar(exp, config) for folio, exp in expedientes.items()}
