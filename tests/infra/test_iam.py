"""Regla 6 de AGENTS.md: ningún permiso de IAM nuevo sin su prueba.

`permisos_esperados.json` es el inventario aprobado de acciones por rol y política. Si un cambio agrega o quita una
acción, esta prueba falla hasta que se regenere con ACTUALIZAR_PERMISOS=1 y el diff se revise en el PR.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from tests.infra.conftest import ENTORNOS, Sintesis, sin_hash, texto_plano

GOLDEN = Path(__file__).with_name("permisos_esperados.json")
PROHIBIDAS_EN_FLUJO = {
    "s3:DeleteObject",
    "s3:DeleteObjectVersion",
    "s3:PutObjectRetention",
    "s3:PutObjectLegalHold",
    "s3:BypassGovernanceRetention",
    "s3:PutBucketObjectLockConfiguration",
    "s3:PutBucketPolicy",
    "kms:ScheduleKeyDeletion",
    "kms:PutKeyPolicy",
}


def _lista(valor: Any) -> list[Any]:
    return valor if isinstance(valor, list) else [valor]


def sentencias_por_principal(s: Sintesis) -> dict[str, list[dict[str, Any]]]:
    """Sentencias de identidad agrupadas por rol (políticas en línea y AWS::IAM::Policy) o por política administrada."""
    por_rol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for clave, rol in s.recursos("AWS::IAM::Role").items():
        pila, lid = clave.split("/", 1)
        for politica in rol["Properties"].get("Policies", []):
            por_rol[f"{pila}/{sin_hash(lid)}"].extend(_lista(politica["PolicyDocument"]["Statement"]))
        for arn in rol["Properties"].get("ManagedPolicyArns", []):
            por_rol[f"{pila}/{sin_hash(lid)}"].append(
                {"Effect": "Managed", "Action": texto_plano(arn).rsplit("/", 1)[-1]}
            )
    for clave, politica in s.recursos("AWS::IAM::Policy").items():
        pila = clave.split("/", 1)[0]
        for ref in politica["Properties"].get("Roles", []):
            por_rol[f"{pila}/{sin_hash(ref['Ref'])}"].extend(
                _lista(politica["Properties"]["PolicyDocument"]["Statement"])
            )
    for clave, politica in s.recursos("AWS::IAM::ManagedPolicy").items():
        pila, lid = clave.split("/", 1)
        por_rol[f"{pila}/{sin_hash(lid)}"].extend(_lista(politica["Properties"]["PolicyDocument"]["Statement"]))
    return por_rol


def inventario(s: Sintesis) -> dict[str, list[str]]:
    return {
        principal: sorted({f"{st['Effect']} {accion}" for st in sentencias for accion in _lista(st["Action"])})
        for principal, sentencias in sorted(sentencias_por_principal(s).items())
    }


def test_inventario_de_permisos_aprobado(sintesis: dict[str, Sintesis]) -> None:
    actual = {nombre: inventario(sintesis[nombre]) for nombre in ENTORNOS}
    if os.environ.get("ACTUALIZAR_PERMISOS") == "1":
        GOLDEN.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        pytest.skip("inventario de permisos regenerado: revisa el diff antes de hacer commit")
    esperado = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for nombre in ENTORNOS:
        for principal in sorted(set(actual[nombre]) | set(esperado.get(nombre, {}))):
            nuevos = sorted(set(actual[nombre].get(principal, [])) - set(esperado.get(nombre, {}).get(principal, [])))
            quitados = sorted(set(esperado.get(nombre, {}).get(principal, [])) - set(actual[nombre].get(principal, [])))
            assert not nuevos and not quitados, (
                f"[{nombre}] {principal}: permisos nuevos={nuevos} quitados={quitados}. "
                "Agrega su prueba y regenera con ACTUALIZAR_PERMISOS=1."
            )


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_sin_comodines_de_servicio(sintesis: dict[str, Sintesis], entorno: str) -> None:
    for principal, acciones in inventario(sintesis[entorno]).items():
        malas = [a for a in acciones if a.split(" ", 1)[1] == "*" or a.endswith(":*")]
        assert not malas, f"{principal} tiene comodines de servicio: {malas}"


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_solo_redactar_invoca_bedrock_y_con_guardrail_obligatorio(sintesis: dict[str, Sintesis], entorno: str) -> None:
    por_rol = sentencias_por_principal(sintesis[entorno])
    con_bedrock = [p for p, sts in por_rol.items() if any("bedrock:InvokeModel" in _lista(st["Action"]) for st in sts)]
    assert len(con_bedrock) == 1 and "RolRedactar" in con_bedrock[0], con_bedrock
    denegaciones = [
        st for st in por_rol[con_bedrock[0]] if st["Effect"] == "Deny" and "bedrock:InvokeModel" in _lista(st["Action"])
    ]
    assert len(denegaciones) == 1
    condicion = denegaciones[0]["Condition"]["StringNotLike"]["bedrock:GuardrailIdentifier"]
    assert texto_plano(condicion).endswith("*"), "el Deny debe aceptar solo versiones del guardrail propio"


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_flujo_no_puede_borrar_ni_acortar_retencion(sintesis: dict[str, Sintesis], entorno: str) -> None:
    for principal, acciones in inventario(sintesis[entorno]).items():
        if "-flujo/" not in principal:
            continue
        prohibidas = {a.split(" ", 1)[1] for a in acciones if a.startswith("Allow ")} & PROHIBIDAS_EN_FLUJO
        assert not prohibidas, f"{principal}: {prohibidas}"


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_analista_escribe_solo_en_su_prefijo_de_revisiones(sintesis: dict[str, Sintesis], entorno: str) -> None:
    por_rol = sentencias_por_principal(sintesis[entorno])
    analista = next(sts for p, sts in por_rol.items() if "PoliticaAnalista" in p)
    escrituras = [st for st in analista if "s3:PutObject" in _lista(st["Action"])]
    assert len(escrituras) == 1
    recursos = [texto_plano(r) for r in _lista(escrituras[0]["Resource"])]
    assert recursos and all(r.endswith("/revisiones/${aws:userid}/*") for r in recursos), recursos


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_comite_solo_lee_memos_finales(sintesis: dict[str, Sintesis], entorno: str) -> None:
    por_rol = sentencias_por_principal(sintesis[entorno])
    comite = next(sts for p, sts in por_rol.items() if "PoliticaComite" in p)
    s3 = [st for st in comite if any(str(a).startswith("s3:") for a in _lista(st["Action"]))]
    assert all(texto_plano(r).endswith("/memo_final.md") for st in s3 for r in _lista(st["Resource"]))
