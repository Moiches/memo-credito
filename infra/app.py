"""Punto de entrada de CDK: `npx cdk <comando> -c entorno=dev|prod [-c repo=<owner>/<repo>]`."""

from __future__ import annotations

import os

from aws_cdk import App, Environment, Tags, Validations
from cdk_nag import AwsSolutionsChecks

from infra.config import cargar_entorno
from infra.stacks.cicd import CicdStack
from infra.stage import ETIQUETAS, MemoCreditoStage


def main() -> None:
    app = App()
    nombre = str(app.node.try_get_context("entorno") or os.environ.get("ENTORNO", "dev"))
    if nombre not in ("dev", "prod"):
        raise SystemExit(f"entorno desconocido: {nombre}")
    entorno = cargar_entorno(nombre)
    cuenta = os.environ.get("CDK_DEFAULT_ACCOUNT")
    region = os.environ.get("CDK_DEFAULT_REGION") or entorno.app.llm.region
    env = Environment(account=cuenta, region=region) if cuenta else None
    MemoCreditoStage(app, f"MemoCredito-{nombre}", entorno=entorno, env=env)

    repo = app.node.try_get_context("repo")
    if repo:
        cicd = CicdStack(
            app,
            "MemoCredito-Cicd",
            repo=str(repo),
            dev=cargar_entorno("dev"),
            env=env,
            stack_name="memo-credito-cicd",
            description="memo-credito: OIDC de GitHub y roles de CI/CD",
        )
        for clave, valor in ETIQUETAS.items():
            Tags.of(cicd).add(clave, valor)
        Validations.of(app).add_plugins(AwsSolutionsChecks(app, verbose=True))
    app.synth()


if __name__ == "__main__":
    main()
