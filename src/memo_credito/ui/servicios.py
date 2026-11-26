"""Operaciones de la consola contra AWS y en local.

Usan la sesión de boto3 del propio analista (su perfil), no un rol compartido: al registrar una revisión, IAM sigue
imponiendo su identidad en revisiones/<aws:userid>/. Reutilizan la CLI (cli/nube.py) para no duplicar contratos.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memo_credito.cli import nube

RAIZ = Path(__file__).resolve().parents[3]
DATOS_RETO = RAIZ / "datos" / "reto_ki_datos" / "solicitudes"
DATOS_DEMO = RAIZ / "demo" / "casos"
SALIDAS = RAIZ / "salidas"


class EstadoNoPendiente(Exception):
    """El folio ya no está en PENDIENTE_REVISION (otro analista lo revisó o cambió el borrador)."""


@dataclass(frozen=True)
class Identidad:
    arn: str
    user_id: str
    cuenta: str


@dataclass(frozen=True)
class Conexion:
    entorno: str
    region: str
    perfil: str | None
    identidad: Identidad
    recursos: dict[str, str]
    sesion: Any


def conectar(entorno: str, sesion: Any | None = None) -> Conexion:
    import boto3

    s = sesion or boto3.session.Session()
    yo = s.client("sts").get_caller_identity()
    return Conexion(
        entorno=entorno,
        region=s.region_name or "us-east-1",
        perfil=s.profile_name,
        identidad=Identidad(arn=yo["Arn"], user_id=yo["UserId"], cuenta=yo["Account"]),
        recursos=nube.recursos(entorno, s),
        sesion=s,
    )


# ---------------------------------------------------------------------------- expedientes locales


@dataclass(frozen=True)
class Expediente:
    nombre: str
    ruta: Path
    archivos: dict[str, int]
    razon_social: str
    credito: dict[str, Any]


def _resumen_solicitud(carpeta: Path) -> tuple[str, dict[str, Any]]:
    """Solo la razón social (persona moral) y el crédito; los datos de contacto nunca se leen aquí."""
    try:
        solicitud = json.loads((carpeta / "solicitud.json").read_text(encoding="utf-8"))
        return str(solicitud["solicitante"]["razon_social"]), dict(solicitud["credito"])
    except (OSError, ValueError, KeyError, TypeError):
        return "", {}


def listar_expedientes(base: Path) -> list[Expediente]:
    if not base.is_dir():
        return []
    expedientes = []
    for carpeta in sorted(p for p in base.iterdir() if p.is_dir()):
        archivos = {p.name: p.stat().st_size for p in sorted(carpeta.iterdir()) if p.is_file()}
        razon_social, credito = _resumen_solicitud(carpeta)
        expedientes.append(Expediente(carpeta.name, carpeta, archivos, razon_social, credito))
    return expedientes


@contextmanager
def carpeta_temporal(archivos: Mapping[str, bytes]) -> Iterator[Path]:
    """Escribe archivos subidos en una carpeta temporal que se borra al salir (pueden traer datos sensibles).

    La carpeta se llama `subida`, distinta de cualquier folio, para que `enviar` reescriba el folio de la solicitud.
    """
    with tempfile.TemporaryDirectory(prefix="memo-ui-") as raiz:
        carpeta = Path(raiz) / "subida"
        carpeta.mkdir()
        for nombre, contenido in archivos.items():
            (carpeta / Path(nombre).name).write_bytes(contenido)
        yield carpeta


# ---------------------------------------------------------------------------- operaciones en AWS


def enviar(con: Conexion, carpeta: Path, folio: str, modo_llm: str) -> str:
    """Sube los documentos y, al final, _manifest.json: el manifiesto es lo que dispara la ingesta."""
    return nube.enviar(
        carpeta.name, con.entorno, folio_destino=folio, modo_llm=modo_llm, datos=carpeta.parent, sesion=con.sesion
    )


def _tabla(con: Conexion) -> Any:
    return con.sesion.resource("dynamodb").Table(con.recursos["tabla-solicitudes"])


def estado(con: Conexion, folio: str) -> dict[str, Any]:
    return dict(_tabla(con).get_item(Key={"folio": folio, "sk": "ESTADO"}).get("Item") or {})


def esperar_estado(
    con: Conexion, folio: str, objetivos: set[str], timeout_s: float = 60, intervalo_s: float = 2
) -> dict[str, Any]:
    limite = time.monotonic() + timeout_s
    item = estado(con, folio)
    while item.get("estado") not in objetivos and time.monotonic() < limite:
        time.sleep(intervalo_s)
        item = estado(con, folio)
    return item


@dataclass(frozen=True)
class Pendiente:
    folio: str
    desde: str
    memo_sha256: str


def pendientes(con: Conexion) -> list[Pendiente]:
    from boto3.dynamodb.conditions import Key

    filas: list[Pendiente] = []
    consulta: dict[str, Any] = {
        "IndexName": "por-estado",
        "KeyConditionExpression": Key("estado").eq("PENDIENTE_REVISION"),
    }
    while True:
        pagina = _tabla(con).query(**consulta)
        for it in pagina["Items"]:
            desde = str(it.get("pendiente_desde") or it.get("actualizado") or "")
            filas.append(Pendiente(str(it["folio"]), desde, str(it.get("memo_sha256", ""))))
        if "LastEvaluatedKey" not in pagina:
            return sorted(filas, key=lambda f: f.desde)
        consulta["ExclusiveStartKey"] = pagina["LastEvaluatedKey"]


@dataclass(frozen=True)
class Borrador:
    folio: str
    texto: str
    sha256: str
    clave: str


def borrador(con: Conexion, folio: str, clave: str) -> Borrador:
    cuerpo: bytes = con.sesion.client("s3").get_object(Bucket=con.recursos["bucket-datos"], Key=clave)["Body"].read()
    return Borrador(folio, cuerpo.decode("utf-8"), hashlib.sha256(cuerpo).hexdigest(), clave)


def registrar_revision(
    con: Conexion,
    folio: str,
    decision: str,
    calificaciones: Mapping[str, int],
    categoria_error: str,
    comentario: str,
    memo_final: str | None = None,
) -> str:
    """Escribe la revisión en revisiones/<tu aws:userid>/...; EventBridge la lleva a aplicar_revision."""
    item = estado(con, folio)
    if item.get("estado") != "PENDIENTE_REVISION":
        raise EstadoNoPendiente(str(item.get("estado")))
    memo_sha256 = str(item["memo_sha256"])
    cuerpo = nube.revision(
        folio,
        memo_sha256,
        decision,
        dict(calificaciones),
        categoria_error,
        comentario,
        memo_final.encode("utf-8") if memo_final is not None else None,
    )
    clave = nube.clave_revision(con.identidad.user_id, folio, memo_sha256)
    con.sesion.client("s3").put_object(
        Bucket=con.recursos["bucket-datos"],
        Key=clave,
        Body=json.dumps(cuerpo, ensure_ascii=False).encode("utf-8"),
        ChecksumAlgorithm="SHA256",
    )
    return clave


def arn_ejecucion(con: Conexion, ejecucion: str) -> str:
    return con.recursos["maquina-estados-arn"].replace(":stateMachine:", ":execution:") + ":" + ejecucion


def historial(con: Conexion, ejecucion: str) -> tuple[str, list[dict[str, Any]]]:
    """Estado de la ejecución y su historial completo (estados que entraron y salieron, con hora)."""
    sfn = con.sesion.client("stepfunctions")
    arn = arn_ejecucion(con, ejecucion)
    estado_ejecucion = str(sfn.describe_execution(executionArn=arn)["status"])
    eventos: list[dict[str, Any]] = []
    pedido: dict[str, Any] = {"executionArn": arn, "maxResults": 1000}
    while True:
        pagina = sfn.get_execution_history(**pedido)
        eventos.extend(pagina["events"])
        if not pagina.get("nextToken"):
            return estado_ejecucion, eventos
        pedido["nextToken"] = pagina["nextToken"]


def analisis_auditado(con: Conexion, folio: str, ejecucion: str) -> dict[str, Any]:
    """La parte 02_analisis del paquete de auditoría: cada indicador con su fórmula y sus entradas."""
    cuerpo = (
        con.sesion.client("s3")
        .get_object(Bucket=con.recursos["bucket-auditoria"], Key=f"{folio}/{ejecucion}/02_analisis.json")["Body"]
        .read()
    )
    return dict(json.loads(cuerpo)["analisis"])


# ---------------------------------------------------------------------------- modo local


@dataclass(frozen=True)
class ResultadoLocal:
    folio: str
    memo: str
    memo_sha256: str
    origen: str
    indicadores: list[dict[str, Any]]
    ejecucion: str
    auditoria: list[str]


def procesar_local(carpeta: Path, salida: Path = SALIDAS) -> ResultadoLocal:
    """El mismo pipeline que `memo procesar`, con el LLM simulado: nada sale de este equipo."""
    from memo_credito.auditoria.paquete import AlmacenLocal, verificar_paquete
    from memo_credito.config import cargar_config
    from memo_credito.llm.simulado import ClienteSimulado
    from memo_credito.pipeline import procesar_carpeta

    res = procesar_carpeta(carpeta, cargar_config("dev"), ClienteSimulado(), salida_base=salida)
    memo = res.ruta_memo.read_text(encoding="utf-8") if res.ruta_memo else ""
    carpeta_auditoria = salida / res.folio / "auditoria"
    ejecuciones = (
        sorted(p.name for p in carpeta_auditoria.iterdir() if p.is_dir()) if carpeta_auditoria.is_dir() else []
    )
    ejecucion = ejecuciones[-1] if ejecuciones else ""
    problemas = verificar_paquete(AlmacenLocal(carpeta_auditoria), ejecucion) if ejecucion else ["sin paquete"]
    return ResultadoLocal(
        folio=res.folio,
        memo=memo,
        memo_sha256=res.memo_sha256,
        origen=str(res.redaccion.estado.origen),
        indicadores=[i.model_dump(mode="json") for i in res.analisis.indicadores],
        ejecucion=ejecucion,
        auditoria=problemas,
    )
