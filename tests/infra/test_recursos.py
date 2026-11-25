"""Propiedades de seguridad y operación de los recursos sintetizados (dev y prod)."""

from __future__ import annotations

import json
import re

import pytest

from infra.stacks.workflow import CURP, RFC
from tests.infra.conftest import ENTORNOS, Sintesis, texto_plano

NOMBRES_SECRETOS = re.compile(r"SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|BEARER|CREDENTIAL", re.IGNORECASE)


def _uno(s: Sintesis, tipo: str, contiene: str = "") -> dict:
    recursos = [r for k, r in s.recursos(tipo).items() if contiene in k]
    assert len(recursos) == 1, (tipo, contiene, len(recursos))
    return recursos[0]


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_sin_secretos_ni_llave_de_bedrock_en_plantillas(sintesis: dict[str, Sintesis], entorno: str) -> None:
    s = sintesis[entorno]
    assert "AWS_BEARER_TOKEN_BEDROCK" not in s.json()
    for clave, fn in s.recursos("AWS::Lambda::Function").items():
        variables = fn["Properties"].get("Environment", {}).get("Variables", {})
        assert not [v for v in variables if NOMBRES_SECRETOS.search(v)], clave
        assert variables["LANGSMITH_TRACING"] == "false" and variables["LANGCHAIN_TRACING_V2"] == "false"


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_lambdas_arm64_con_trazas_y_handler_del_paquete(sintesis: dict[str, Sintesis], entorno: str) -> None:
    funciones = sintesis[entorno].recursos("AWS::Lambda::Function")
    assert len(funciones) == 7
    for fn in funciones.values():
        p = fn["Properties"]
        assert p["Architectures"] == ["arm64"] and p["Runtime"] == "python3.12"
        assert p["TracingConfig"]["Mode"] == "Active"
        assert p["Handler"].startswith("memo_credito.handlers.") and p["Handler"].endswith(".handler")
        assert ("VpcConfig" in p) == (entorno == "prod")


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_redactar_recibe_guardrail_y_perfil(sintesis: dict[str, Sintesis], entorno: str) -> None:
    fn = _uno(sintesis[entorno], "AWS::Lambda::Function", "FnRedactar")
    variables = fn["Properties"]["Environment"]["Variables"]
    assert {"GUARDRAIL_ARN", "GUARDRAIL_VERSION", "PERFIL_INFERENCIA_ARN"} <= set(variables)
    assert variables["MODO_LLM"] == ("bedrock" if entorno == "prod" else "simulado")


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_maquina_de_estados(sintesis: dict[str, Sintesis], entorno: str) -> None:
    s = sintesis[entorno]
    sm = _uno(s, "AWS::StepFunctions::StateMachine")["Properties"]
    assert sm["LoggingConfiguration"]["Level"] == "ALL"
    assert sm["LoggingConfiguration"]["IncludeExecutionData"] is False
    assert sm["TracingConfiguration"]["Enabled"] is True
    definicion = texto_plano(sm["DefinitionString"])
    assert "lambda:invoke.waitForTaskToken" in definicion
    assert f'"TimeoutSeconds":{s.entorno.infra.revision_sla_s}' in definicion
    assert '"ErrorEquals":["States.Timeout"]' in definicion
    assert f'"MaxConcurrency":{s.entorno.infra.map_max_concurrency}' in definicion
    assert '"forzar_plantilla":"falla del paso de redacción"' in definicion


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_auditoria_inmutable(sintesis: dict[str, Sintesis], entorno: str) -> None:
    s = sintesis[entorno]
    bucket = _uno(s, "AWS::S3::Bucket", "Auditoria")
    regla = bucket["Properties"]["ObjectLockConfiguration"]["Rule"]["DefaultRetention"]
    assert bucket["Properties"]["ObjectLockEnabled"] is True
    assert regla == {"Mode": s.entorno.infra.auditoria_modo, "Days": s.entorno.infra.auditoria_dias}
    if entorno == "prod":
        assert regla["Mode"] == "COMPLIANCE" and bucket["DeletionPolicy"] == "Retain"


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_buckets_privados_cifrados_y_solo_tls(sintesis: dict[str, Sintesis], entorno: str) -> None:
    s = sintesis[entorno]
    buckets = s.recursos("AWS::S3::Bucket")
    assert len(buckets) == 3
    for b in buckets.values():
        bloqueo = b["Properties"]["PublicAccessBlockConfiguration"]
        assert all(
            bloqueo[k] for k in ("BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets")
        )
    politicas = json.dumps(s.recursos("AWS::S3::BucketPolicy"))
    assert politicas.count('"aws:SecureTransport": "false"') == 3
    datos = _uno(s, "AWS::S3::Bucket", "Datos")["Properties"]
    cifrado = datos["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]["ServerSideEncryptionByDefault"]
    assert cifrado["SSEAlgorithm"] == "aws:kms"
    assert datos["NotificationConfiguration"]["EventBridgeConfiguration"]["EventBridgeEnabled"] is True
    assert not s.recursos("Custom::S3BucketNotifications"), "EventBridge nativo, sin custom resource"


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_tablas_y_llave(sintesis: dict[str, Sintesis], entorno: str) -> None:
    s = sintesis[entorno]
    for t in s.recursos("AWS::DynamoDB::Table").values():
        p = t["Properties"]
        assert p["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"] is True
        assert p["SSESpecification"]["SSEType"] == "KMS"
    assert _uno(s, "AWS::KMS::Key")["Properties"]["EnableKeyRotation"] is True


def test_vpc_solo_en_prod_con_endpoints_y_egreso_443(sintesis: dict[str, Sintesis]) -> None:
    assert not sintesis["dev"].recursos("AWS::EC2::VPC")
    prod = sintesis["prod"]
    endpoints = [texto_plano(e["Properties"]["ServiceName"]) for e in prod.recursos("AWS::EC2::VPCEndpoint").values()]
    assert len(endpoints) == 5
    assert sum(e.endswith(sufijo) for e in endpoints for sufijo in (".bedrock-runtime", ".states", ".xray")) == 3
    sg = _uno(prod, "AWS::EC2::SecurityGroup", "SgLambdas")["Properties"]
    assert [(r["IpProtocol"], r["FromPort"], r["ToPort"]) for r in sg["SecurityGroupEgress"]] == [("tcp", 443, 443)]
    assert not prod.recursos("AWS::EC2::NatGateway") and not prod.recursos("AWS::EC2::InternetGateway")


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_guardrail(sintesis: dict[str, Sintesis], entorno: str) -> None:
    g = _uno(sintesis[entorno], "AWS::Bedrock::Guardrail")["Properties"]
    filtros = g["ContentPolicyConfig"]["FiltersConfig"]
    assert {"Type": "PROMPT_ATTACK", "InputStrength": "HIGH", "OutputStrength": "NONE"} in filtros
    tema = g["TopicPolicyConfig"]["TopicsConfig"][0]
    assert tema["Type"] == "DENY" and tema["OutputAction"] == "BLOCK"
    regex = {r["Name"]: r for r in g["SensitiveInformationPolicyConfig"]["RegexesConfig"]}
    assert set(regex) == {"CURP", "RFC"} and all(r["OutputAction"] == "BLOCK" for r in regex.values())
    assert g["ContentPolicyConfig"]["ContentFiltersTierConfig"]["TierName"] == "STANDARD"
    assert texto_plano(g["CrossRegionConfig"]["GuardrailProfileArn"]).endswith("guardrail-profile/us.guardrail.v1:0")


def test_regex_del_guardrail() -> None:
    assert re.search(CURP, "dato BADD110313HCMLNS09 dato")
    assert re.search(RFC, "RFC: CRE120315AB1")
    assert not re.search(CURP, "{{flujo_promedio}} y {{capacidad_pago}}")
    assert not re.search(RFC, "Capacidad de pago 1.85x con apalancamiento 0.79x")


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_alarmas_notifican_y_presupuesto(sintesis: dict[str, Sintesis], entorno: str) -> None:
    s = sintesis[entorno]
    alarmas = s.recursos("AWS::CloudWatch::Alarm")
    assert len(alarmas) >= 15
    assert all(a["Properties"].get("AlarmActions") for a in alarmas.values())
    nombres = {texto_plano(a["Properties"]["AlarmName"]) for a in alarmas.values()}
    p = s.entorno.prefijo
    assert {
        f"{p}-flujo-fallido",
        f"{p}-memo-plantilla",
        f"{p}-guardrail",
        f"{p}-costo-memo",
        f"{p}-dlq-eventos",
    } <= nombres
    presupuesto = _uno(s, "AWS::Budgets::Budget")["Properties"]
    assert len(presupuesto["NotificationsWithSubscribers"]) == 2


@pytest.mark.parametrize("entorno", ENTORNOS)
def test_reconocimientos_de_cdk_nag_acotados(sintesis: dict[str, Sintesis], entorno: str) -> None:
    from aws_cdk import Validations

    permitidas = {"AwsSolutions-S1", "AwsSolutions-IAM5", "AwsSolutions-L1", "AwsSolutions-SQS3", "AwsSolutions-IAM4"}
    stage = sintesis[entorno].stage
    encontrados = []
    for construct in stage.node.find_all():
        for m in construct.node.metadata:
            if m.type == Validations.ACKNOWLEDGED_RULES_METADATA_KEY:
                encontrados.append((construct.node.path, m.data))
    assert encontrados, "no se encontraron reconocimientos: ¿cambió la API de cdk-nag?"
    texto = json.dumps([d for _, d in encontrados], ensure_ascii=False, default=str)
    reglas = set(re.findall(r"AwsSolutions-[A-Z]+\d+", texto))
    assert reglas <= permitidas, reglas - permitidas
    assert all(path != stage.node.path for path, _ in encontrados), "sin reconocimientos globales en el stage"
