"""CI/CD (§18.2): proveedor OIDC de GitHub y roles sin llaves de larga duración.

- gh-pr-lectura: pull requests; solo asume el rol de lookup de CDK (cdk diff).
- gh-deploy-dev / gh-deploy-prod: solo desde el environment de GitHub correspondiente (prod exige revisores);
  solo asumen los roles de bootstrap de CDK, que son los que operan CloudFormation.
- gh-pruebas-dev: integración, carga y evals reales en dev, limitado a folios IT-* y LT-*. Nunca despliega.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Aws, Duration, Stack
from aws_cdk import aws_iam as iam
from constructs import Construct

from infra.config import Entorno
from infra.nag import suprimir
from infra.stacks.workflow import REGIONES_PERFIL_US

EMISOR = "token.actions.githubusercontent.com"
CALIFICADOR_CDK = "memocred"  # igual a @aws-cdk/core:bootstrapQualifier en cdk.json
FOLIOS_PRUEBA = ("IT-*", "LT-*")


class CicdStack(Stack):
    def __init__(
        self, scope: Construct, id_: str, repo: str, dev: Entorno, sujeto_repo: str | None = None, **kwargs: Any
    ) -> None:
        """`sujeto_repo` es la parte del claim `sub` que identifica al repositorio. GitHub emite el formato con IDs
        inmutables (`<owner>@<owner_id>/<repo>@<repo_id>`), que además impide que un repositorio recreado con el
        mismo nombre herede la confianza. Sin él se usa el formato heredado `<owner>/<repo>`."""
        super().__init__(scope, id_, **kwargs)
        if repo.count("/") != 1:
            raise ValueError("repo debe tener la forma <owner>/<repo>")
        sujeto_repo = sujeto_repo or repo
        if sujeto_repo.count("/") != 1:
            raise ValueError("sujeto_repo debe tener la forma <owner>@<id>/<repo>@<id>")
        self.proveedor = iam.OidcProviderNative(
            self, "GitHubOidc", url=f"https://{EMISOR}", client_ids=["sts.amazonaws.com"]
        )
        cuenta, region, part = Aws.ACCOUNT_ID, Aws.REGION, Aws.PARTITION
        rol_cdk = f"arn:{part}:iam::{cuenta}:role/cdk-{CALIFICADOR_CDK}-%s-role-{cuenta}-{region}"

        def rol(nombre: str, sujeto: str, sentencias: list[iam.PolicyStatement]) -> iam.Role:
            r = iam.Role(
                self,
                f"Rol{nombre.title().replace('-', '')}",
                role_name=f"memo-credito-{nombre}",
                max_session_duration=Duration.hours(1),
                description=f"GitHub Actions ({sujeto})",
                assumed_by=iam.WebIdentityPrincipal(
                    self.proveedor.open_id_connect_provider_arn,
                    conditions={
                        "StringEquals": {
                            f"{EMISOR}:aud": "sts.amazonaws.com",
                            f"{EMISOR}:sub": f"repo:{sujeto_repo}:{sujeto}",
                        }
                    },
                ),
            )
            for s in sentencias:
                r.add_to_policy(s)
            return r

        rol(
            "gh-pr-lectura",
            "pull_request",
            [iam.PolicyStatement(actions=["sts:AssumeRole"], resources=[rol_cdk % "lookup"])],
        )
        for entorno in ("dev", "prod"):
            rol(
                f"gh-deploy-{entorno}",
                f"environment:{entorno}",
                [
                    iam.PolicyStatement(
                        actions=["sts:AssumeRole", "sts:TagSession"],
                        # lookup: cdk.context.json no se versiona (contiene la cuenta), el synth consulta las AZs
                        resources=[rol_cdk % r for r in ("deploy", "file-publishing", "image-publishing", "lookup")],
                    )
                ],
            )

        p = dev.prefijo
        bucket = f"arn:{part}:s3:::{p}-datos-{cuenta}"
        tabla = f"arn:{part}:dynamodb:{region}:{cuenta}:table/{p}-solicitudes"
        modelo = dev.app.llm.modelo_id
        rol(
            "gh-pruebas-dev",
            "environment:dev",
            [
                iam.PolicyStatement(
                    actions=["s3:PutObject"], resources=[f"{bucket}/entrada/{f}" for f in FOLIOS_PRUEBA]
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject"], resources=[f"{bucket}/trabajo/{f}" for f in FOLIOS_PRUEBA]
                ),
                iam.PolicyStatement(
                    actions=["s3:PutObject"],
                    resources=[f"{bucket}/revisiones/${{aws:userid}}/{f}" for f in FOLIOS_PRUEBA],
                ),
                iam.PolicyStatement(
                    actions=["dynamodb:GetItem", "dynamodb:Query"],
                    resources=[tabla],
                    conditions={"ForAllValues:StringLike": {"dynamodb:LeadingKeys": list(FOLIOS_PRUEBA)}},
                ),
                iam.PolicyStatement(
                    actions=["kms:Decrypt", "kms:GenerateDataKey"],
                    resources=[f"arn:{part}:kms:{region}:{cuenta}:key/*"],
                    conditions={
                        "ForAnyValue:StringEquals": {"kms:ResourceAliases": [f"alias/{p}"]},
                        "StringLike": {
                            "kms:ViaService": [f"s3.{region}.amazonaws.com", f"dynamodb.{region}.amazonaws.com"]
                        },
                    },
                ),
                iam.PolicyStatement(
                    actions=["ssm:GetParameter", "ssm:GetParametersByPath"],
                    resources=[f"arn:{part}:ssm:{region}:{cuenta}:parameter/memo-credito/{dev.nombre}*"],
                ),
                iam.PolicyStatement(
                    actions=["states:DescribeExecution", "states:GetExecutionHistory", "states:ListExecutions"],
                    resources=[
                        f"arn:{part}:states:{region}:{cuenta}:stateMachine:{p}-flujo",
                        f"arn:{part}:states:{region}:{cuenta}:execution:{p}-flujo:*",
                    ],
                ),
                iam.PolicyStatement(actions=["cloudwatch:GetMetricData"], resources=["*"]),
                iam.PolicyStatement(
                    actions=["logs:StartQuery", "logs:GetQueryResults"],
                    resources=[f"arn:{part}:logs:{region}:{cuenta}:log-group:/aws/lambda/{p}-*"],
                ),
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[
                        f"arn:{part}:bedrock:{region}:{cuenta}:inference-profile/{modelo}",
                        f"arn:{part}:bedrock:{region}:{cuenta}:application-inference-profile/*",
                        *[
                            f"arn:{part}:bedrock:{r}::foundation-model/{modelo.split('.', 1)[1]}"
                            for r in REGIONES_PERFIL_US
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
                        f"arn:{part}:bedrock:{region}:{cuenta}:guardrail/*",
                        *[
                            f"arn:{part}:bedrock:{r}:{cuenta}:guardrail-profile/us.guardrail.v1:0"
                            for r in REGIONES_PERFIL_US
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
                    "Roles de bootstrap de CDK por patrón de nombre; prefijos de folios de prueba (IT-*, LT-*); "
                    "llave KMS acotada por alias; métricas sin permisos a nivel de recurso; guardrails y perfiles "
                    "de aplicación cuyo id nace en el despliegue; el Deny exige guardrail en toda invocación.",
                )
            ],
        )
