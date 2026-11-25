"""CI/CD: proveedor OIDC de GitHub y roles sin llaves de larga duración.

- ci: pull requests; solo puede asumir el rol de lookup de CDK (cdk diff).
- despliegue-dev / despliegue-prod: solo desde el *environment* de GitHub correspondiente (prod exige aprobación);
  solo pueden asumir los roles de bootstrap de CDK, que son los que tocan CloudFormation.
- evals: evals reales contra Bedrock con el guardrail obligatorio; sin acceso a documentos ni a datos.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Aws, Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

from infra.config import Entorno
from infra.nag import suprimir

EMISOR = "token.actions.githubusercontent.com"
CALIFICADOR_CDK = "hnb659fds"


class CicdStack(Stack):
    def __init__(self, scope: Construct, id_: str, repo: str, entorno_evals: Entorno, **kwargs: Any) -> None:
        super().__init__(scope, id_, **kwargs)
        if repo.count("/") != 1:
            raise ValueError("repo debe tener la forma <owner>/<repo>")
        self.proveedor = iam.OidcProviderNative(
            self, "GitHubOidc", url=f"https://{EMISOR}", client_ids=["sts.amazonaws.com"]
        )
        roles_cdk = f"arn:{Aws.PARTITION}:iam::{Aws.ACCOUNT_ID}:role/cdk-{CALIFICADOR_CDK}-%s-role-{Aws.ACCOUNT_ID}-{Aws.REGION}"

        def rol(nombre: str, sujeto: str, sentencias: list[iam.PolicyStatement]) -> iam.Role:
            r = iam.Role(
                self,
                f"Rol{nombre.title().replace('-', '')}",
                role_name=f"memo-credito-gha-{nombre}",
                max_session_duration=Duration.hours(1),
                description=f"GitHub Actions ({sujeto})",
                assumed_by=iam.WebIdentityPrincipal(
                    self.proveedor.open_id_connect_provider_arn,
                    conditions={
                        "StringEquals": {
                            f"{EMISOR}:aud": "sts.amazonaws.com",
                            f"{EMISOR}:sub": f"repo:{repo}:{sujeto}",
                        },
                    },
                ),
            )
            for s in sentencias:
                r.add_to_policy(s)
            return r

        rol("ci", "pull_request", [iam.PolicyStatement(actions=["sts:AssumeRole"], resources=[roles_cdk % "lookup"])])
        for entorno in ("dev", "prod"):
            rol(
                f"despliegue-{entorno}",
                f"environment:{entorno}",
                [
                    iam.PolicyStatement(
                        actions=["sts:AssumeRole", "sts:TagSession"],
                        resources=[roles_cdk % r for r in ("deploy", "file-publishing", "image-publishing", "lookup")],
                    )
                ],
            )
        e = entorno_evals.nombre
        parametros = f"arn:{Aws.PARTITION}:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:parameter/memo-credito/{e}/*"
        modelo = entorno_evals.app.llm.modelo_id
        rol(
            "evals",
            "environment:evals",
            [
                iam.PolicyStatement(actions=["ssm:GetParameter"], resources=[parametros]),
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:inference-profile/{modelo}",
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:application-inference-profile/*",
                        *[
                            f"arn:{Aws.PARTITION}:bedrock:{r}::foundation-model/{modelo.split('.', 1)[1]}"
                            for r in ("us-east-1", "us-east-2", "us-west-2")
                        ],
                    ],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.DENY,
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources=["*"],
                    conditions={"Null": {"bedrock:GuardrailIdentifier": "true"}},
                ),
                iam.PolicyStatement(
                    actions=["bedrock:ApplyGuardrail"],
                    resources=[
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:guardrail/*",
                        *[
                            f"arn:{Aws.PARTITION}:bedrock:{r}:{Aws.ACCOUNT_ID}:guardrail-profile/us.guardrail.v1:0"
                            for r in ("us-east-1", "us-east-2", "us-west-2")
                        ],
                    ],
                ),
            ],
        )
        suprimir(
            self,
            [
                (
                    "AwsSolutions-IAM5",
                    "Roles de bootstrap de CDK por patrón de nombre; perfiles de aplicación y guardrails del "
                    "entorno de evals cuyo id se crea en el despliegue; el Deny exige guardrail en toda invocación.",
                )
            ],
        )
