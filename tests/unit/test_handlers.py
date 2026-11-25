"""Handlers de Lambda contra AWS simulado (moto): el recorrido que hace Step Functions y los negativos de seguridad.

Las credenciales son falsas y AWS_PROFILE se borra: ninguna llamada puede llegar a la cuenta real.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import boto3
import pytest
from moto import mock_aws

from tests.conftest import DATOS

REGION = "us-east-1"
DATOS_B, AUDITORIA_B = "memo-datos-test", "memo-auditoria-test"
TABLA, IDEM = "memo-solicitudes-test", "memo-idempotencia-test"
REVISOR = "AROAEJEMPLO:analista"


@dataclass
class Contexto:
    function_name: str = "prueba"
    memory_limit_in_mb: int = 1024
    invoked_function_arn: str = f"arn:aws:lambda:{REGION}:123456789012:function:prueba"
    aws_request_id: str = "req-1"

    def get_remaining_time_in_millis(self) -> int:
        return 60_000


class SfnFalso:
    """Registra start/stop/send_task_success sin depender del soporte de moto para task tokens."""

    def __init__(self) -> None:
        self.inicios: list[dict[str, Any]] = []
        self.tokens: list[dict[str, Any]] = []

    def start_execution(self, **kw: Any) -> dict[str, Any]:
        self.inicios.append(kw)
        return {"executionArn": f"arn:aws:states:{REGION}:123456789012:execution:flujo:{kw['name']}"}

    def stop_execution(self, **kw: Any) -> dict[str, Any]:
        return {}

    def send_task_success(self, **kw: Any) -> dict[str, Any]:
        self.tokens.append(kw)
        return {}


@dataclass
class Entorno:
    s3: Any
    sfn: SfnFalso
    h: dict[str, ModuleType]


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[Entorno]:
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_BEARER_TOKEN_BEDROCK", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    for var, valor in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "ENTORNO": "dev",
        "BUCKET_DATOS": DATOS_B,
        "BUCKET_AUDITORIA": AUDITORIA_B,
        "TABLA_SOLICITUDES": TABLA,
        "TABLA_IDEMPOTENCIA": IDEM,
        "MAQUINA_ESTADOS_ARN": f"arn:aws:states:{REGION}:123456789012:stateMachine:flujo",
        "MODO_LLM": "simulado",
        "PERMITIR_OVERRIDE_MODO": "false",
        "POWERTOOLS_METRICS_NAMESPACE": "MemoCredito/test",
        "POWERTOOLS_TRACE_DISABLED": "true",
        "LANGSMITH_TRACING": "false",
    }.items():
        monkeypatch.setenv(var, valor)
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        for b in (DATOS_B, AUDITORIA_B):
            s3.create_bucket(Bucket=b)
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=TABLA,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "folio", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "folio", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
        )
        ddb.create_table(
            TableName=IDEM,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        )
        comun = importlib.import_module("memo_credito.handlers._comun")
        for cache in (comun.s3, comun.sfn, comun.tabla, comun.config):
            cache.cache_clear()
        sfn = SfnFalso()
        monkeypatch.setattr(comun, "sfn", lambda: sfn)
        nombres = ("ingesta", "extraer", "validar_calcular", "redactar", "esperar_revision", "aplicar_revision")
        modulos = {n: importlib.reload(importlib.import_module(f"memo_credito.handlers.{n}")) for n in nombres}
        modulos["registrar_revision"] = importlib.reload(
            importlib.import_module("memo_credito.handlers.registrar_revision")
        )
        for m in modulos.values():
            if hasattr(m, "sfn"):
                monkeypatch.setattr(m, "sfn", lambda: sfn)
        yield Entorno(s3=s3, sfn=sfn, h=modulos)


def _subir_expediente(e: Entorno, folio: str, origen: str = "SOL-0001", alterar: str | None = None) -> None:
    documentos = []
    for archivo in sorted((DATOS / origen).iterdir()):
        datos = archivo.read_bytes()
        if archivo.name == "solicitud.json" and folio != origen:
            datos = datos.replace(origen.encode(), folio.encode())
        sha = hashlib.sha256(datos).hexdigest()
        e.s3.put_object(
            Bucket=DATOS_B,
            Key=f"entrada/{folio}/{archivo.name}",
            Body=datos,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=base64.b64encode(bytes.fromhex(sha)).decode(),
        )
        documentos.append({"nombre": archivo.name, "sha256": "0" * 64 if archivo.name == alterar else sha})
    manifiesto = {"folio": folio, "documentos": documentos, "reproceso": 0}
    e.s3.put_object(Bucket=DATOS_B, Key=f"entrada/{folio}/_manifest.json", Body=json.dumps(manifiesto).encode())


def _evento_s3(clave: str) -> dict[str, Any]:
    return {"detail": {"bucket": {"name": DATOS_B}, "object": {"key": clave}}}


def _leer(e: Entorno, bucket: str, clave: str) -> bytes:
    return bytes(e.s3.get_object(Bucket=bucket, Key=clave)["Body"].read())


def _estado(e: Entorno, folio: str) -> dict[str, Any]:
    return dict(e.h["ingesta"].leer_estado(folio))


def test_flujo_completo_con_revision_y_negativos(aws: Entorno) -> None:
    e, h, ctx = aws, aws.h, Contexto()
    folio = "SOL-0001"
    _subir_expediente(e, folio)

    r = h["ingesta"].handler(_evento_s3(f"entrada/{folio}/_manifest.json"), ctx)
    assert r["duplicada"] is False and len(e.sfn.inicios) == 1
    entrada = json.loads(e.sfn.inicios[0]["input"])
    assert _estado(e, folio)["estado"] == "RECIBIDA"
    # El mismo evento otra vez (EventBridge entrega al menos una vez): no se duplica ni regresa el estado
    assert h["ingesta"].handler(_evento_s3(f"entrada/{folio}/_manifest.json"), ctx)["duplicada"] is True
    assert len(e.sfn.inicios) == 1

    ejecucion = r["ejecucion"]
    base = {"folio": folio, "ejecucion": ejecucion}
    extracciones = [h["extraer"].handler({**base, "doc": d}, ctx) for d in entrada["documentos"]]
    assert not any(x["rechazado"] for x in extracciones)
    analisis = h["validar_calcular"].handler({**base, **entrada, "extracciones": extracciones}, ctx)
    assert analisis["alertas"] == []
    carga = {**base, "clave_contexto": analisis["clave_contexto"], "modo_llm": "simulado", "forzar_plantilla": None}
    redaccion = h["redactar"].handler(carga, ctx)
    assert redaccion["origen"] == "llm" and redaccion["llamadas"] == 1
    # Idempotencia: el reintento de Step Functions devuelve lo mismo sin volver a redactar
    assert h["redactar"].handler(carga, ctx) == redaccion
    memo = _leer(e, DATOS_B, redaccion["memo_s3_key"]).decode("utf-8")
    assert hashlib.sha256(memo.encode("utf-8")).hexdigest() == redaccion["memo_sha256"]

    h["esperar_revision"].handler(
        {
            **base,
            "token": "token-de-prueba",
            "memo_sha256": redaccion["memo_sha256"],
            "memo_s3_key": redaccion["memo_s3_key"],
            "escalamientos": 0,
        },
        ctx,
    )
    assert _estado(e, folio)["estado"] == "PENDIENTE_REVISION"

    def revisar(sha: str, **extra: Any) -> dict[str, Any]:
        cuerpo = {
            "folio": folio,
            "memo_sha256": sha,
            "decision": "listo_para_comite",
            "calificaciones": {"resumen": 5},
            "categoria_error": "ninguna",
            "comentario": "",
            "memo_final_sha256": redaccion["memo_sha256"],
            **extra,
        }
        clave = f"revisiones/{REVISOR}/{folio}/{sha}.json"
        e.s3.put_object(Bucket=DATOS_B, Key=clave, Body=json.dumps(cuerpo).encode())
        return dict(h["aplicar_revision"].handler(_evento_s3(clave), ctx))

    # Negativos: hash de un memo que no es el vigente y campos fuera del esquema
    assert revisar("a" * 64)["aplicada"] is False
    assert revisar(redaccion["memo_sha256"], aprobado=True)["aplicada"] is False
    assert e.sfn.tokens == [] and _estado(e, folio)["estado"] == "PENDIENTE_REVISION"

    assert revisar(redaccion["memo_sha256"])["aplicada"] is True
    assert len(e.sfn.tokens) == 1 and e.sfn.tokens[0]["taskToken"] == "token-de-prueba"
    salida = json.loads(e.sfn.tokens[0]["output"])
    assert salida["revisor_id"] == REVISOR  # la identidad sale de la clave impuesta por IAM, no del cuerpo
    # Doble aplicación: rechazada por la escritura condicional
    assert revisar(redaccion["memo_sha256"])["aplicada"] is False and len(e.sfn.tokens) == 1

    final = h["registrar_revision"].handler({**base, "revision": salida, "redaccion": redaccion}, ctx)
    assert final["estado_final"] == "LISTO_PARA_COMITE"
    item = _estado(e, folio)
    assert item["estado"] == "LISTO_PARA_COMITE" and item["token"] == "-"

    from memo_credito.auditoria.paquete import verificar_paquete

    almacen = h["validar_calcular"].AlmacenS3(AUDITORIA_B)
    prefijo = f"{folio}/{ejecucion}"
    assert verificar_paquete(almacen, prefijo) == []
    partes = e.s3.list_objects_v2(Bucket=AUDITORIA_B, Prefix=prefijo)["Contents"]
    nombres = sorted(o["Key"].rsplit("/", 1)[1] for o in partes)
    assert nombres == [
        "00_entrada.json",
        "01_extraccion.json",
        "02_analisis.json",
        "03_redaccion.json",
        "04_revision.json",
        "99_manifiesto.json",
    ]
    # Sin PII en la auditoría: los valores de contacto de la solicitud no aparecen en ninguna parte
    from memo_credito.extraccion.solicitud import canarios

    textos = [_leer(e, AUDITORIA_B, o["Key"]).decode("utf-8") for o in partes]
    for canario in canarios((DATOS / folio / "solicitud.json").read_bytes()):
        assert not any(canario in t for t in textos)
    # Write-once: una parte existente no se puede sobrescribir con otro contenido
    with pytest.raises(FileExistsError):
        almacen.escribir(f"{prefijo}/00_entrada.json", b"{}")


def test_ingesta_rechaza_manifiesto_que_no_corresponde(aws: Entorno) -> None:
    _subir_expediente(aws, "SOL-0101", alterar="constancia_situacion_fiscal.pdf")
    with pytest.raises(ValueError, match="sha256"):
        aws.h["ingesta"].handler(_evento_s3("entrada/SOL-0101/_manifest.json"), Contexto())
    assert aws.sfn.inicios == []


def test_ingesta_rechaza_nombres_con_ruta(aws: Entorno) -> None:
    manifiesto = {"folio": "SOL-0102", "documentos": [{"nombre": "../revisiones/x.json", "sha256": "0" * 64}]}
    aws.s3.put_object(Bucket=DATOS_B, Key="entrada/SOL-0102/_manifest.json", Body=json.dumps(manifiesto).encode())
    with pytest.raises(ValueError, match="nombre"):
        aws.h["ingesta"].handler(_evento_s3("entrada/SOL-0102/_manifest.json"), Contexto())


def test_aplicar_revision_rechaza_clave_mal_formada(aws: Entorno) -> None:
    clave = "revisiones/SOL-0001/x.json"
    aws.s3.put_object(Bucket=DATOS_B, Key=clave, Body=b"{}")
    assert aws.h["aplicar_revision"].handler(_evento_s3(clave), Contexto())["aplicada"] is False


def test_distancia_de_edicion_y_pii() -> None:
    from memo_credito.handlers.registrar_revision import contiene_pii, distancia_edicion

    assert distancia_edicion("a b c", "a b c") == 0.0
    assert distancia_edicion("a b c d", "a x c d") == 0.25
    assert contiene_pii("llamar al 55 1234 5678") and contiene_pii("correo x@y.mx")
    assert not contiene_pii("Capacidad de pago de 1.85x con RFC CRE120315AB1")
