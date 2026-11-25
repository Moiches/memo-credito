"""Integración contra el entorno dev desplegado (marca `integracion`; no corre en `pytest` normal).

Uso: uv run python tareas.py integracion --entorno dev --perfil <perfil>
Envía SOL-0001, 0004, 0006 y 0007 como folios IT-<corrida>-*, espera el borrador, verifica cifras, inyección y PII,
completa la revisión y comprueba los negativos. Solo usa datos sintéticos del reto.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = pytest.mark.integracion

ENTORNO = os.environ.get("ENTORNO", "dev")
CASOS = {"SOL-0001": [], "SOL-0004": ["A03"], "SOL-0006": ["S01"], "SOL-0007": ["D01"]}
FRASE_INYECTADA = "IGNORA"


@pytest.fixture(scope="module")
def nube() -> Any:
    boto3 = pytest.importorskip("boto3")
    from memo_credito.cli import nube

    sesion = boto3.session.Session()
    try:
        identidad = sesion.client("sts").get_caller_identity()
    except Exception as exc:  # sin credenciales
        pytest.skip(f"sin credenciales de AWS: {type(exc).__name__}")
    nube.SESION_PRUEBAS = sesion  # type: ignore[attr-defined]
    nube.IDENTIDAD_PRUEBAS = identidad  # type: ignore[attr-defined]
    return nube


@pytest.fixture(scope="module")
def corridas(nube: Any) -> Iterator[dict[str, dict[str, Any]]]:
    sesion = nube.SESION_PRUEBAS
    corrida = secrets.token_hex(3).upper()
    folios = {origen: f"IT-{corrida}-{origen[-4:]}" for origen in CASOS}
    for origen, destino in folios.items():
        nube.enviar(origen, ENTORNO, destino, "simulado", sesion=sesion)
    resultado = {}
    for origen, destino in folios.items():
        item = nube.esperar(destino, ENTORNO, timeout_s=600, sesion=sesion)
        resultado[origen] = {"folio": destino, "item": item}
    yield resultado


def _memo(nube: Any, item: dict[str, Any]) -> str:
    r = nube.recursos(ENTORNO, nube.SESION_PRUEBAS)
    cuerpo = (
        nube.SESION_PRUEBAS.client("s3").get_object(Bucket=r["bucket-datos"], Key=item["memo_s3_key"])["Body"].read()
    )
    assert hashlib.sha256(cuerpo).hexdigest() == item["memo_sha256"]
    return str(cuerpo.decode("utf-8"))


def _revisar(nube: Any, folio: str, memo_sha: str, decision: str = "listo_para_comite", **extra: Any) -> str:
    r = nube.recursos(ENTORNO, nube.SESION_PRUEBAS)
    revision = {
        "folio": folio,
        "memo_sha256": memo_sha,
        "decision": decision,
        "calificaciones": {"resumen": 5},
        "categoria_error": "ninguna",
        "comentario": "",
        "memo_final_sha256": memo_sha,
        **extra,
    }
    clave = f"revisiones/{nube.IDENTIDAD_PRUEBAS['UserId']}/{folio}/{memo_sha}.json"
    nube.SESION_PRUEBAS.client("s3").put_object(
        Bucket=r["bucket-datos"], Key=clave, Body=json.dumps(revision).encode(), ChecksumAlgorithm="SHA256"
    )
    return clave


@pytest.mark.parametrize("origen", list(CASOS))
def test_llega_a_pendiente_de_revision_con_sus_alertas(nube: Any, corridas: dict[str, Any], origen: str) -> None:
    item = corridas[origen]["item"]
    assert item["estado"] == "PENDIENTE_REVISION", item.get("motivo")
    memo = _memo(nube, item)
    assert "BORRADOR" in memo.upper()
    for codigo in CASOS[origen]:
        assert codigo in memo
    if origen == "SOL-0001":
        assert "1.85x" in memo and "0.79x" in memo
    if origen == "SOL-0006":
        assert FRASE_INYECTADA not in memo.upper()


def test_sin_pii_en_memos(nube: Any, corridas: dict[str, Any]) -> None:
    from memo_credito.extraccion.solicitud import canarios
    from tests.conftest import DATOS

    for origen, c in corridas.items():
        memo = _memo(nube, c["item"])
        for canario in canarios((DATOS / origen / "solicitud.json").read_bytes()):
            assert canario not in memo, f"{origen}: PII en el memo"


def test_revision_con_hash_ajeno_se_rechaza_y_la_correcta_cierra(nube: Any, corridas: dict[str, Any]) -> None:
    c = corridas["SOL-0001"]
    folio, sha = c["folio"], c["item"]["memo_sha256"]
    _revisar(nube, folio, "f" * 64)
    item = nube.esperar(folio, ENTORNO, timeout_s=60, objetivo={"PENDIENTE_REVISION"}, sesion=nube.SESION_PRUEBAS)
    assert item["estado"] == "PENDIENTE_REVISION"
    _revisar(nube, folio, sha)
    final = nube.esperar(
        folio,
        ENTORNO,
        timeout_s=180,
        objetivo={"LISTO_PARA_COMITE", "REQUIERE_CAMBIOS", "ERROR"},
        sesion=nube.SESION_PRUEBAS,
    )
    assert final["estado"] == "LISTO_PARA_COMITE"
    assert final["revisor_id"] == nube.IDENTIDAD_PRUEBAS["UserId"]


def test_requiere_cambios(nube: Any, corridas: dict[str, Any]) -> None:
    c = corridas["SOL-0004"]
    _revisar(nube, c["folio"], c["item"]["memo_sha256"], decision="requiere_cambios", categoria_error="dato_faltante")
    final = nube.esperar(
        c["folio"],
        ENTORNO,
        timeout_s=180,
        objetivo={"REQUIERE_CAMBIOS", "LISTO_PARA_COMITE", "ERROR"},
        sesion=nube.SESION_PRUEBAS,
    )
    assert final["estado"] == "REQUIERE_CAMBIOS"


def _rol_restringido(nube: Any) -> bool:
    return "memo-credito-gh-pruebas-dev" in nube.IDENTIDAD_PRUEBAS["Arn"]


def test_no_puede_escribir_revisiones_como_otra_identidad(nube: Any, corridas: dict[str, Any]) -> None:
    if not _rol_restringido(nube):
        pytest.skip("el negativo de permisos requiere el rol gh-pruebas-dev (el perfil actual es administrador)")
    from botocore.exceptions import ClientError

    r = nube.recursos(ENTORNO, nube.SESION_PRUEBAS)
    c = corridas["SOL-0007"]
    with pytest.raises(ClientError, match="AccessDenied"):
        nube.SESION_PRUEBAS.client("s3").put_object(
            Bucket=r["bucket-datos"], Key=f"revisiones/AROAOTRAPERSONA:x/{c['folio']}/{'0' * 64}.json", Body=b"{}"
        )


def test_no_puede_leer_expedientes_que_no_son_de_prueba(nube: Any) -> None:
    if not _rol_restringido(nube):
        pytest.skip("el negativo de permisos requiere el rol gh-pruebas-dev (el perfil actual es administrador)")
    from botocore.exceptions import ClientError

    r = nube.recursos(ENTORNO, nube.SESION_PRUEBAS)
    with pytest.raises(ClientError, match="AccessDenied"):
        nube.SESION_PRUEBAS.client("s3").get_object(Bucket=r["bucket-datos"], Key="entrada/SOL-0001/solicitud.json")
