"""Confianza OIDC del stack de CI/CD: cada rol acepta un único sujeto, con los IDs inmutables del repositorio."""

from __future__ import annotations

from typing import Any

import pytest

SUJETO = "duena@11/repo-x@22"


@pytest.fixture(scope="module")
def roles(sintesis: Any) -> dict[str, dict[str, Any]]:  # sintesis fija el asset mínimo de Lambda
    from aws_cdk import App
    from aws_cdk.assertions import Template

    from infra.config import cargar_entorno
    from infra.stacks.cicd import CicdStack

    app = App()
    pila = CicdStack(app, "Cicd", repo="duena/repo-x", sujeto_repo=SUJETO, dev=cargar_entorno("dev"))
    recursos = Template.from_stack(pila).to_json()["Resources"]
    return {r["Properties"]["RoleName"]: r for r in recursos.values() if r["Type"] == "AWS::IAM::Role"}


def test_cada_rol_confia_en_un_solo_sujeto_con_ids(roles: dict[str, dict[str, Any]]) -> None:
    esperados = {
        "memo-credito-gh-pr-lectura": f"repo:{SUJETO}:pull_request",
        "memo-credito-gh-deploy-dev": f"repo:{SUJETO}:environment:dev",
        "memo-credito-gh-deploy-prod": f"repo:{SUJETO}:environment:prod",
        "memo-credito-gh-pruebas-dev": f"repo:{SUJETO}:environment:dev",
    }
    assert set(roles) == set(esperados)
    for nombre, sujeto in esperados.items():
        (sentencia,) = roles[nombre]["Properties"]["AssumeRolePolicyDocument"]["Statement"]
        condicion = sentencia["Condition"]["StringEquals"]
        assert condicion["token.actions.githubusercontent.com:sub"] == sujeto
        assert condicion["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
        assert "StringLike" not in sentencia["Condition"], "sin comodines en el sujeto"


def test_roles_de_despliegue_solo_asumen_roles_de_bootstrap(roles: dict[str, dict[str, Any]]) -> None:
    from infra.stacks.cicd import CALIFICADOR_CDK

    for nombre in ("memo-credito-gh-deploy-dev", "memo-credito-gh-deploy-prod"):
        assert roles[nombre]["Properties"].get("MaxSessionDuration") == 3600
    assert CALIFICADOR_CDK == "memocred"
