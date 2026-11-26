"""Consola del analista: validación de entrada, lectura del memo, línea de tiempo y operaciones con moto.

Las credenciales son falsas y AWS_PROFILE se borra: ninguna llamada puede llegar a la cuenta real.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from memo_credito.cli import nube
from memo_credito.config import cargar_config
from memo_credito.dominio.modelos import Solicitud
from memo_credito.ui import formato as fmt
from memo_credito.ui import servicios as srv
from tests.conftest import DATOS

REGION = "us-east-1"
TABLA, BUCKET_DATOS, BUCKET_AUDITORIA = "ui-solicitudes-test", "ui-datos-test", "ui-auditoria-test"
REVISOR = "AIDAEJEMPLOREVISOR"
RAIZ = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------- patrones sincronizados


def test_folio_igual_al_del_dominio():
    patrones = [m.pattern for m in Solicitud.model_fields["folio"].metadata if hasattr(m, "pattern")]
    assert patrones == [fmt.RE_FOLIO.pattern]


def test_nombre_igual_al_de_la_ingesta():
    fuente = (RAIZ / "src" / "memo_credito" / "handlers" / "ingesta.py").read_text(encoding="utf-8")
    m = re.search(r'^RE_NOMBRE = re\.compile\(r"(.+?)"\)', fuente, re.MULTILINE)
    assert m is not None
    assert m.group(1) == fmt.RE_NOMBRE.pattern


# ---------------------------------------------------------------------------- entrada


@pytest.mark.parametrize("folio", ["UI-20261125-153012", "EXI-0102", "SOL-0001", "DEMO-E01"])
def test_folio_valido(folio):
    assert fmt.validar_folio(folio) is None


@pytest.mark.parametrize("folio", ["", "ui-0001", "ABCDE-1", "UI 0001", "U-1", "UI-"])
def test_folio_invalido(folio):
    assert fmt.validar_folio(folio)


def test_folio_sugerido_es_valido():
    assert fmt.validar_folio(fmt.sugerir_folio(datetime(2026, 11, 25, 15, 30, 12, tzinfo=UTC))) is None


def _archivos(carpeta: Path) -> dict[str, int]:
    return {p.name: p.stat().st_size for p in carpeta.iterdir() if p.is_file()}


def test_expediente_completo_sin_problemas():
    limites = cargar_config("dev").limites
    assert fmt.validar_archivos(_archivos(DATOS / "SOL-0001"), limites) == []


def test_problemas_bloqueantes_y_avisos():
    limites = cargar_config("dev").limites
    archivos = {
        "Estado de cuenta agosto.pdf": 100,  # la ingesta rechazaría el manifiesto completo
        "agosto.pdf": 100,  # se rechaza solo el documento (S02)
        "estado_cuenta_2026-07.pdf": limites.pdf_max_bytes + 1,  # S02
    }
    problemas = {p.archivo: p for p in fmt.validar_archivos(archivos, limites)}
    assert problemas["solicitud.json"].bloquea
    assert problemas["Estado de cuenta agosto.pdf"].bloquea
    assert not problemas["agosto.pdf"].bloquea
    assert not problemas["estado_cuenta_2026-07.pdf"].bloquea


def test_solicitud_demasiado_grande_bloquea():
    limites = cargar_config("dev").limites
    problemas = fmt.validar_archivos({"solicitud.json": limites.json_max_bytes + 1}, limites)
    assert [p.bloquea for p in problemas] == [True]


def test_requisitos_con_un_mes_faltante():
    nombres = [n for n in _archivos(DATOS / "SOL-0001") if n != "estado_cuenta_2026-08.pdf"]
    req = {r.nombre: r for r in fmt.requisitos(nombres, meses=3)}
    assert not req["Estados de cuenta"].cumple
    assert req["Estados de cuenta"].detalle == "2 de 3 (2026-07, 2026-09)"
    assert (
        req["Solicitud"].cumple and req["Constancia de situación fiscal"].cumple and req["Estados financieros"].cumple
    )


def test_folios_de_prueba():
    assert fmt.es_folio_de_prueba("IT-75C3F8-0007") and fmt.es_folio_de_prueba("LT-0001")
    assert not fmt.es_folio_de_prueba("EXI-0102")


# ---------------------------------------------------------------------------- memo real (pipeline local)


@pytest.fixture(scope="module")
def local_sol6(tmp_path_factory: pytest.TempPathFactory) -> srv.ResultadoLocal:
    return srv.procesar_local(DATOS / "SOL-0006", salida=tmp_path_factory.mktemp("salidas"))


def test_procesar_local_deja_paquete_integro(local_sol6):
    assert local_sol6.folio == "SOL-0006"
    assert local_sol6.auditoria == []
    assert local_sol6.ejecucion.startswith("local-")
    capacidad = next(i for i in local_sol6.indicadores if i["id"] == "capacidad_pago")
    assert capacidad["valor_texto"] == "1.30"
    assert capacidad["entradas"] == {"flujo_promedio": "86712.73", "pago_mensual": "66702.10"}


def test_lee_alertas_indicadores_y_metadatos_del_memo(local_sol6):
    alertas = fmt.alertas_del_memo(local_sol6.memo)
    assert [(a.codigo, a.severidad) for a in alertas] == [("S01", "alta")]
    assert "renglón 16" in alertas[0].detalle
    filas = fmt.indicadores_del_memo(local_sol6.memo)
    assert fmt.valor_indicador(filas, "Capacidad de pago") == "1.30"
    assert fmt.valor_indicador(filas, "Apalancamiento") == "1.24"
    assert fmt.valor_indicador(filas, "No existe") == "—"
    meta = fmt.metadatos_del_memo(local_sol6.memo)
    assert meta["folio"] == "SOL-0006" and meta["origen"] == "llm" and meta["modo"] == "simulado"
    cuerpo = fmt.cuerpo_del_memo(local_sol6.memo)
    assert "<!--" not in cuerpo and cuerpo.startswith("# Memo")


def test_memo_para_mostrar(local_sol6):
    texto = fmt.para_mostrar(local_sol6.memo)
    assert texto.startswith("## Memo") and "\n### Resumen" in texto and "<!--" not in texto
    assert "\n**Solicitante:** AGROINSUMOS DEL VALLE SA DE CV (RFC AVA160229LC1)  \n" in texto
    assert "\n- **Alerta S01" in texto  # las viñetas no cambian


def test_escapar_md_neutraliza_directivas():
    escapado = fmt.escapar_md("[clic](http://x) :red[hola] $x$")
    assert "[clic](" not in escapado and ":red[" not in escapado and "$x$" not in escapado
    assert fmt.moneda(1200000) == "$1,200,000.00" and fmt.moneda("n/a") == "n/a"


def test_memo_sin_metadatos():
    assert fmt.metadatos_del_memo("# Memo\n") == {}
    assert fmt.alertas_del_memo("# Memo\n") == []


# ---------------------------------------------------------------------------- línea de tiempo


def _ev(tipo: str, nombre: str, segundo: int) -> dict[str, Any]:
    clave = "stateEnteredEventDetails" if tipo.endswith("Entered") else "stateExitedEventDetails"
    return {
        "type": tipo,
        "timestamp": datetime(2026, 11, 25, tzinfo=UTC) + timedelta(seconds=segundo),
        clave: {"name": nombre},
    }


def test_pasos_de_una_ejecucion_esperando_revision():
    eventos = [
        _ev("TaskStateEntered", "RegistrarInicio", 0),
        _ev("TaskStateExited", "RegistrarInicio", 0),
        _ev("MapStateEntered", "ExtraerDocumentos", 1),
        _ev("TaskStateEntered", "ExtraerDocumento", 1),
        _ev("TaskStateEntered", "ExtraerDocumento", 1),
        _ev("TaskStateExited", "ExtraerDocumento", 3),
        _ev("TaskStateExited", "ExtraerDocumento", 4),
        _ev("MapStateExited", "ExtraerDocumentos", 4),
        _ev("TaskStateEntered", "ValidarYCalcular", 4),
        _ev("TaskStateExited", "ValidarYCalcular", 5),
        _ev("TaskStateEntered", "RedactarMemo", 5),
        _ev("TaskStateExited", "RedactarMemo", 17),
        _ev("TaskStateEntered", "EsperarRevision", 17),
    ]
    pasos = {p.clave: p for p in fmt.pasos_del_flujo(fmt.resumir_historial(eventos), "RUNNING")}
    assert pasos["ExtraerDocumentos"].estado == "completado"
    assert pasos["ExtraerDocumentos"].detalle == "2 documentos · 3.0 s"
    assert pasos["RedactarMemo"].detalle == "12.0 s"
    assert pasos["EsperarRevision"].estado == "en_curso"
    assert pasos["EsperarRevision"].detalle == "esperando al analista"
    assert pasos["RegistrarRevision"].estado == "pendiente"


def test_rama_de_plantilla_y_error():
    eventos = [
        _ev("TaskStateEntered", "RedactarMemo", 0),
        _ev("TaskStateEntered", "RedactarConPlantilla", 60),
        _ev("TaskStateExited", "RedactarConPlantilla", 61),
        _ev("TaskStateEntered", "MarcarError", 62),
    ]
    pasos = fmt.pasos_del_flujo(fmt.resumir_historial(eventos), "FAILED")
    claves = [p.clave for p in pasos]
    assert claves.index("RedactarConPlantilla") == claves.index("RedactarMemo") + 1
    estados = {p.clave: p.estado for p in pasos}
    assert estados["RedactarMemo"] == "fallido"
    assert estados["RegistrarRevision"] == "omitido"
    assert pasos[-1].clave == "MarcarError" and pasos[-1].estado == "fallido"


# ---------------------------------------------------------------------------- formato breve


def test_formato_breve():
    ahora = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    assert fmt.enmascarar_cuenta("123456789012") == "••••••••9012"
    assert fmt.nombre_corto("arn:aws:iam::123456789012:user/ana") == "user/ana"
    assert fmt.nombre_corto("arn:aws:sts::123456789012:assumed-role/Analista/ana") == "assumed-role/Analista/ana"
    assert fmt.duracion(3.04) == "3.0 s" and fmt.duracion(125) == "2 min 5 s"
    assert fmt.hace("2026-11-25T11:55:00+00:00", ahora) == "hace 5 min"
    assert fmt.hace("no es fecha", ahora) == "no es fecha"
    assert fmt.tamano(512) == "512 B" and fmt.tamano(4594) == "4.5 KB"
    url = fmt.url_ejecucion("us-east-1", "arn:aws:states:us-east-1:1:execution:flujo:EXI-0102-abc")
    assert url.startswith("https://us-east-1.console.aws.amazon.com/states/") and url.endswith("EXI-0102-abc")


# ---------------------------------------------------------------------------- operaciones con moto


@pytest.fixture
def con(monkeypatch: pytest.MonkeyPatch) -> Iterator[srv.Conexion]:
    for var in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_BEARER_TOKEN_BEDROCK", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    for var, valor in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "POWERTOOLS_METRICS_NAMESPACE": "MemoCredito/test",
        "POWERTOOLS_TRACE_DISABLED": "true",
    }.items():
        monkeypatch.setenv(var, valor)
    with mock_aws():
        sesion = boto3.session.Session(region_name=REGION)
        sesion.client("dynamodb").create_table(
            TableName=TABLA,
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "folio", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": a, "AttributeType": "S"} for a in ("folio", "sk", "estado", "actualizado")
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "por-estado",
                    "KeySchema": [
                        {"AttributeName": "estado", "KeyType": "HASH"},
                        {"AttributeName": "actualizado", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        for bucket in (BUCKET_DATOS, BUCKET_AUDITORIA):
            sesion.client("s3").create_bucket(Bucket=bucket)
        yield srv.Conexion(
            entorno="dev",
            region=REGION,
            perfil=None,
            identidad=srv.Identidad("arn:aws:iam::123456789012:user/ana", REVISOR, "123456789012"),
            recursos={
                "tabla-solicitudes": TABLA,
                "bucket-datos": BUCKET_DATOS,
                "bucket-auditoria": BUCKET_AUDITORIA,
                "maquina-estados-arn": f"arn:aws:states:{REGION}:123456789012:stateMachine:flujo",
            },
            sesion=sesion,
        )


def _poner_estado(con: srv.Conexion, folio: str, estado: str, actualizado: str, **extra: str) -> None:
    item = {"folio": folio, "sk": "ESTADO", "estado": estado, "actualizado": actualizado, **extra}
    con.sesion.resource("dynamodb").Table(TABLA).put_item(Item=item)


def test_pendientes_y_estado(con):
    _poner_estado(con, "EXI-0102", "PENDIENTE_REVISION", "2026-11-25T10:00:00+00:00", memo_sha256="a" * 64)
    _poner_estado(con, "IT-1-0006", "PENDIENTE_REVISION", "2026-11-25T09:00:00+00:00", memo_sha256="b" * 64)
    _poner_estado(con, "INC-0102", "LISTO_PARA_COMITE", "2026-11-25T11:00:00+00:00")
    assert [p.folio for p in srv.pendientes(con)] == ["IT-1-0006", "EXI-0102"]
    assert srv.estado(con, "INC-0102")["estado"] == "LISTO_PARA_COMITE"
    assert srv.estado(con, "NO-EXISTE") == {}
    assert srv.esperar_estado(con, "INC-0102", {"LISTO_PARA_COMITE"}, timeout_s=0)["estado"] == "LISTO_PARA_COMITE"


def test_borrador_y_revision_que_acepta_la_lambda(con, monkeypatch):
    texto = "# Memo de análisis de crédito — EXI-0102\n".encode()
    sha = hashlib.sha256(texto).hexdigest()
    clave_memo = "trabajo/EXI-0102/EXI-0102-abc/memo.md"
    con.sesion.client("s3").put_object(Bucket=BUCKET_DATOS, Key=clave_memo, Body=texto)
    _poner_estado(con, "EXI-0102", "PENDIENTE_REVISION", "2026-11-25T10:00:00+00:00", memo_sha256=sha)

    leido = srv.borrador(con, "EXI-0102", clave_memo)
    assert leido.sha256 == sha and leido.texto.startswith("# Memo")

    clave = srv.registrar_revision(
        con, "EXI-0102", "listo_para_comite", {"resumen": 5, "puntos": 4}, "ninguna", "Claro y completo."
    )
    assert clave == f"revisiones/{REVISOR}/EXI-0102/{sha}.json"
    cuerpo = json.loads(con.sesion.client("s3").get_object(Bucket=BUCKET_DATOS, Key=clave)["Body"].read())
    modelo = importlib.import_module("memo_credito.handlers.aplicar_revision").RevisionEntrada
    revision = modelo.model_validate(cuerpo)
    assert revision.memo_final is None and revision.memo_final_sha256 == sha

    _poner_estado(con, "EXI-0102", "LISTO_PARA_COMITE", "2026-11-25T10:05:00+00:00", memo_sha256=sha)
    with pytest.raises(srv.EstadoNoPendiente):
        srv.registrar_revision(con, "EXI-0102", "requiere_cambios", {}, "ninguna", "")


def test_revision_con_memo_editado(con):
    texto = b"# Memo\n"
    sha = hashlib.sha256(texto).hexdigest()
    _poner_estado(con, "EXI-0103", "PENDIENTE_REVISION", "2026-11-25T10:00:00+00:00", memo_sha256=sha)
    clave = srv.registrar_revision(
        con, "EXI-0103", "requiere_cambios", {}, "redaccion", "", memo_final="# Memo editado\n"
    )
    cuerpo = json.loads(con.sesion.client("s3").get_object(Bucket=BUCKET_DATOS, Key=clave)["Body"].read())
    assert cuerpo["memo_final"] == "# Memo editado\n"
    assert cuerpo["memo_final_sha256"] == hashlib.sha256(b"# Memo editado\n").hexdigest()


def test_cli_y_consola_arman_la_misma_revision():
    args = type(
        "Args",
        (),
        {
            "calificacion": ["resumen=5"],
            "memo_final": None,
            "decision": "listo_para_comite",
            "categoria_error": "ninguna",
            "comentario": None,
        },
    )()
    assert nube.construir_revision("EXI-0102", "c" * 64, args) == nube.revision(
        "EXI-0102", "c" * 64, "listo_para_comite", {"resumen": 5}
    )


def test_carpeta_temporal_se_borra():
    with srv.carpeta_temporal({"solicitud.json": b"{}", "../fuera.pdf": b"%PDF"}) as carpeta:
        assert carpeta.name == "subida"
        assert sorted(p.name for p in carpeta.iterdir()) == ["fuera.pdf", "solicitud.json"]
    assert not carpeta.exists()


def test_listar_expedientes_del_reto():
    expedientes = {e.nombre: e for e in srv.listar_expedientes(DATOS)}
    assert expedientes["SOL-0001"].razon_social == "ABARROTES Y SURTIDOS LA PALMA SA DE CV"
    assert expedientes["SOL-0001"].credito["plazo_meses"] == 24
    assert "solicitud.json" in expedientes["SOL-0001"].archivos
    assert srv.listar_expedientes(RAIZ / "no-existe") == []
