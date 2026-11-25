"""Adaptador Bedrock Converse con botocore Stubber: forma exacta de la petición y parseo de la respuesta."""

from __future__ import annotations

import json
from decimal import Decimal

import boto3
import pytest
from botocore.stub import Stubber

from memo_credito.costos import costo_llamada
from memo_credito.dominio.errores import LLMErrorConfiguracion, LLMNoDisponible
from memo_credito.llm.bedrock import ClienteBedrock, parsear_guardrail
from memo_credito.memo.hechos import construir_hechos
from memo_credito.memo.modelos import PeticionRedaccion, ResultadoGuardrail, UsoTokens
from memo_credito.memo.plantilla import plantilla


@pytest.fixture
def stub_cliente():
    cliente = boto3.client("bedrock-runtime", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")
    with Stubber(cliente) as stubber:
        yield cliente, stubber


@pytest.fixture(scope="module")
def peticion(expedientes, analisis, config):
    return PeticionRedaccion(hechos=construir_hechos(expedientes["SOL-0001"], analisis["SOL-0001"], config))


def test_peticion_exacta_sin_parametros_de_muestreo(stub_cliente, peticion, config):
    cliente, stubber = stub_cliente
    bedrock = ClienteBedrock(config, guardrail_id="gr-123", guardrail_version="1", cliente=cliente)
    req = bedrock.construir_peticion(peticion)
    assert req["modelId"] == config.llm.modelo_id
    assert req["inferenceConfig"] == {"maxTokens": config.llm.max_tokens}
    assert req["outputConfig"]["effort"] == "low"
    assert req["outputConfig"]["textFormat"]["type"] == "json_schema"
    assert req["guardrailConfig"] == {"guardrailIdentifier": "gr-123", "guardrailVersion": "1", "trace": "enabled"}
    plano = json.dumps(req, ensure_ascii=False)
    for prohibido in ("temperature", "topP", "top_p", "top_k"):
        assert prohibido not in plano
    salida = plantilla(peticion.hechos)
    stubber.add_response(
        "converse",
        {
            "output": {"message": {"role": "assistant", "content": [{"text": salida.model_dump_json()}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 2239, "outputTokens": 812, "totalTokens": 3051},
            "metrics": {"latencyMs": 900},
        },
        req,
    )
    resp = bedrock.redactar(peticion)
    assert resp.salida == salida and resp.uso == UsoTokens(entrada=2239, salida=812)
    assert resp.peticion == req and resp.stop_reason == "end_turn"


@pytest.mark.parametrize(
    ("codigo", "excepcion"),
    [
        ("ThrottlingException", LLMNoDisponible),
        ("AccessDeniedException", LLMErrorConfiguracion),
        ("ValidationException", LLMErrorConfiguracion),
    ],
)
def test_errores_reintentables_y_no_reintentables(stub_cliente, peticion, config, codigo, excepcion):
    cliente, stubber = stub_cliente
    stubber.add_client_error("converse", service_error_code=codigo, http_status_code=400)
    with pytest.raises(excepcion):
        ClienteBedrock(config, cliente=cliente).redactar(peticion)


def test_parseo_de_traza_del_guardrail():
    traza = {
        "inputAssessment": {
            "gr-123": {
                "sensitiveInformationPolicy": {"regexes": [{"name": "CURP", "action": "BLOCKED", "detected": True}]},
                "invocationMetrics": {
                    "usage": {"contentPolicyUnits": 6, "topicPolicyUnits": 0, "sensitiveInformationPolicyUnits": 6}
                },
            }
        },
        "outputAssessments": {"gr-123": [{"invocationMetrics": {"usage": {"topicPolicyUnits": 3}}}]},
    }
    g = parsear_guardrail(traza, "guardrail_intervened")
    assert g.intervino and g.origen == "entrada" and g.politicas == ("sensitiveInformationPolicy:CURP",)
    assert g.unidades == {"contentPolicyUnits": 6, "topicPolicyUnits": 3, "sensitiveInformationPolicyUnits": 6}
    assert not parsear_guardrail(None, "end_turn").intervino


def test_costo_por_llamada_con_precios_de_config(config):
    uso = UsoTokens(entrada=2239, salida=812)
    assert costo_llamada(uso, ResultadoGuardrail(), "us.anthropic.claude-sonnet-4-6", config.precios) == Decimal(
        "0.020787"
    )
    g = ResultadoGuardrail(
        unidades={"contentPolicyUnits": 6, "topicPolicyUnits": 3, "sensitiveInformationPolicyUnits": 9}
    )
    # 6*0.15/1000 + 3*0.15/1000 + 9*0.10/1000 = 0.00225
    assert costo_llamada(UsoTokens(), g, "simulado", config.precios) == Decimal("0.002250")
