"""Comandos contra AWS: enviar, pendientes, descargar, revisar y reprocesar.

Los nombres de recursos se descubren en SSM (/memo-credito/{entorno}/...), publicados por CDK.
La identidad del revisor NO la declara el cliente: la clave revisiones/<aws:userid>/... la impone IAM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[3]
DATOS = RAIZ / "datos" / "reto_ki_datos" / "solicitudes"
ESTADOS_FINALES = {"PENDIENTE_REVISION", "LISTO_PARA_COMITE", "REQUIERE_CAMBIOS", "ERROR", "REVISION_VENCIDA"}


def _sesion() -> Any:
    import boto3

    return boto3.session.Session()


def recursos(entorno: str, sesion: Any | None = None) -> dict[str, str]:
    ssm = (sesion or _sesion()).client("ssm")
    prefijo = f"/memo-credito/{entorno}/"
    salida: dict[str, str] = {}
    paginador = ssm.get_paginator("get_parameters_by_path")
    for pagina in paginador.paginate(Path=prefijo, Recursive=True):
        for p in pagina["Parameters"]:
            salida[p["Name"].removeprefix(prefijo)] = p["Value"]
    if not salida:
        raise SystemExit(f"no hay parámetros en SSM bajo {prefijo}: ¿está desplegado el entorno {entorno}?")
    return salida


def _sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def enviar(
    folio_origen: str,
    entorno: str,
    folio_destino: str | None = None,
    modo_llm: str | None = None,
    datos: Path = DATOS,
    sesion: Any | None = None,
    reproceso: int = 0,
) -> str:
    """Sube documentos + _manifest.json (al final) a entrada/{folio}/; EventBridge dispara la ingesta."""
    sesion = sesion or _sesion()
    r = recursos(entorno, sesion)
    s3 = sesion.client("s3")
    folio = folio_destino or folio_origen
    archivos = {p.name: p.read_bytes() for p in sorted((datos / folio_origen).iterdir()) if p.is_file()}
    if folio != folio_origen and "solicitud.json" in archivos:
        sol = json.loads(archivos["solicitud.json"].decode("utf-8"))
        sol["folio"] = folio
        archivos["solicitud.json"] = json.dumps(sol, ensure_ascii=False, indent=2).encode("utf-8")
    documentos = []
    for nombre, contenido in archivos.items():
        s3.put_object(
            Bucket=r["bucket-datos"], Key=f"entrada/{folio}/{nombre}", Body=contenido, ChecksumAlgorithm="SHA256"
        )
        documentos.append({"nombre": nombre, "sha256": _sha256(contenido), "bytes": len(contenido)})
    manifiesto: dict[str, Any] = {"folio": folio, "documentos": documentos, "reproceso": reproceso}
    if modo_llm:
        manifiesto["modo_llm"] = modo_llm
    s3.put_object(
        Bucket=r["bucket-datos"],
        Key=f"entrada/{folio}/_manifest.json",
        Body=json.dumps(manifiesto).encode("utf-8"),
        ChecksumAlgorithm="SHA256",
    )
    return folio


def estado(folio: str, entorno: str, sesion: Any | None = None) -> dict[str, Any]:
    sesion = sesion or _sesion()
    r = recursos(entorno, sesion)
    tabla = sesion.resource("dynamodb").Table(r["tabla-solicitudes"])
    return dict(tabla.get_item(Key={"folio": folio, "sk": "ESTADO"}).get("Item") or {})


def esperar(
    folio: str, entorno: str, timeout_s: int = 300, objetivo: set[str] | None = None, sesion: Any | None = None
) -> dict[str, Any]:
    objetivo = objetivo or ESTADOS_FINALES
    limite = time.time() + timeout_s
    item: dict[str, Any] = {}
    while time.time() < limite:
        item = estado(folio, entorno, sesion)
        if item.get("estado") in objetivo:
            return item
        time.sleep(3)
    raise TimeoutError(f"{folio} no llegó a {sorted(objetivo)} en {timeout_s}s (estado={item.get('estado')})")


def cmd_enviar(args: argparse.Namespace) -> int:
    folio = enviar(args.folio, args.entorno, args.folio_destino, args.modo_llm, Path(args.datos))
    print(f"Enviado {folio} a {args.entorno}.")
    if args.esperar:
        item = esperar(folio, args.entorno, args.timeout)
        print(
            f"{folio}: estado={item.get('estado')} motivo={item.get('motivo', '-')} memo={item.get('memo_s3_key', '-')}"
        )
        return 0 if item.get("estado") == "PENDIENTE_REVISION" else 1
    return 0


def cmd_pendientes(args: argparse.Namespace) -> int:
    from boto3.dynamodb.conditions import Key

    sesion = _sesion()
    r = recursos(args.entorno, sesion)
    tabla = sesion.resource("dynamodb").Table(r["tabla-solicitudes"])
    items = tabla.query(IndexName="por-estado", KeyConditionExpression=Key("estado").eq("PENDIENTE_REVISION"))["Items"]
    for it in sorted(items, key=lambda i: str(i.get("actualizado", ""))):
        print(f"{it['folio']}\tdesde {it.get('actualizado')}\tmemo_sha256={str(it.get('memo_sha256', ''))[:12]}")
    print(f"{len(items)} pendientes de revisión")
    return 0


def cmd_descargar(args: argparse.Namespace) -> int:
    sesion = _sesion()
    r = recursos(args.entorno, sesion)
    item = estado(args.folio, args.entorno, sesion)
    if not item.get("memo_s3_key"):
        print(f"{args.folio} no tiene memo (estado={item.get('estado')})")
        return 1
    destino = Path(args.dir)
    destino.mkdir(parents=True, exist_ok=True)
    cuerpo = sesion.client("s3").get_object(Bucket=r["bucket-datos"], Key=item["memo_s3_key"])["Body"].read()
    ruta = destino / f"{args.folio}.md"
    ruta.write_bytes(cuerpo)
    print(f"Memo en {ruta} (sha256 {_sha256(cuerpo)}); edítalo y registra la revisión con `memo revisar`.")
    return 0


def construir_revision(folio: str, memo_sha256: str, args: argparse.Namespace) -> dict[str, Any]:
    calificaciones = {}
    for par in args.calificacion or []:
        seccion, _, valor = par.partition("=")
        calificaciones[seccion.strip()] = int(valor)
    memo_final = Path(args.memo_final).read_bytes() if args.memo_final else None
    return {
        "folio": folio,
        "memo_sha256": memo_sha256,
        "decision": args.decision,
        "calificaciones": calificaciones,
        "categoria_error": args.categoria_error,
        "comentario": args.comentario or "",
        "memo_final_sha256": _sha256(memo_final) if memo_final else memo_sha256,
        "memo_final": memo_final.decode("utf-8") if memo_final else None,
    }


def cmd_revisar(args: argparse.Namespace) -> int:
    sesion = _sesion()
    r = recursos(args.entorno, sesion)
    item = estado(args.folio, args.entorno, sesion)
    if item.get("estado") != "PENDIENTE_REVISION":
        print(f"{args.folio} no está pendiente de revisión (estado={item.get('estado')})")
        return 1
    revision = construir_revision(args.folio, str(item["memo_sha256"]), args)
    user_id = sesion.client("sts").get_caller_identity()["UserId"]  # IAM obliga a que coincida con aws:userid
    clave = f"revisiones/{user_id}/{args.folio}/{revision['memo_sha256']}.json"
    sesion.client("s3").put_object(
        Bucket=r["bucket-datos"],
        Key=clave,
        Body=json.dumps(revision, ensure_ascii=False).encode("utf-8"),
        ChecksumAlgorithm="SHA256",
    )
    print(f"Revisión registrada: s3://{r['bucket-datos']}/{clave}")
    return 0


def cmd_reprocesar(args: argparse.Namespace) -> int:
    item = estado(args.folio, args.entorno)
    n = int(item.get("reprocesos", 0)) + 1
    folio = enviar(args.folio, args.entorno, None, args.modo_llm, Path(args.datos), reproceso=n)
    print(f"Reproceso {n} de {folio} enviado (motivo: {args.motivo}).")
    return 0


def registrar(sub: Any) -> None:
    s = sub.add_parser("enviar", help="sube una solicitud a entrada/ del entorno")
    s.add_argument("folio")
    s.add_argument("--folio-destino")
    s.add_argument("--modo-llm", choices=["simulado", "bedrock"])
    s.add_argument("--datos", default=str(DATOS))
    s.add_argument("--esperar", action="store_true")
    s.add_argument("--timeout", type=int, default=300)
    s.set_defaults(fn=cmd_enviar)

    s = sub.add_parser("pendientes", help="lista folios pendientes de revisión")
    s.set_defaults(fn=cmd_pendientes)

    s = sub.add_parser("descargar", help="descarga el memo borrador para revisarlo")
    s.add_argument("folio")
    s.add_argument("--dir", default="rev")
    s.set_defaults(fn=cmd_descargar)

    s = sub.add_parser("revisar", help="registra la revisión del analista (identidad por IAM)")
    s.add_argument("folio")
    s.add_argument("--decision", required=True, choices=["listo_para_comite", "requiere_cambios"])
    s.add_argument(
        "--calificacion",
        action="append",
        help="seccion=1..5 (resumen, observaciones, puntos, indicadores, validaciones)",
    )
    s.add_argument(
        "--categoria-error",
        default="ninguna",
        choices=["ninguna", "cifra", "alerta_omitida", "alerta_falsa", "redaccion", "tono", "dato_faltante", "otro"],
    )
    s.add_argument("--comentario", default="")
    s.add_argument("--memo-final")
    s.set_defaults(fn=cmd_revisar)

    s = sub.add_parser("reprocesar", help="reprocesa una solicitud con los documentos actuales")
    s.add_argument("folio")
    s.add_argument("--motivo", required=True)
    s.add_argument("--modo-llm", choices=["simulado", "bedrock"])
    s.add_argument("--datos", default=str(DATOS))
    s.set_defaults(fn=cmd_reprocesar)
