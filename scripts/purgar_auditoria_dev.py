"""Vacía los buckets de DEV (todas las versiones) para que `cdk destroy` pueda borrarlos.

La auditoría de dev usa Object Lock en modo GOVERNANCE (1 día): borrar exige s3:BypassGovernanceRetention.
En prod (COMPLIANCE) esto es imposible por diseño, y el script se niega a correr fuera de dev.

Uso: uv run python tareas.py destroy --entorno dev --perfil <perfil>   (llama a este script con --confirmar)
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError


def vaciar(s3: object, bucket: str, gobernanza: bool) -> int:
    borrados = 0
    paginador = s3.get_paginator("list_object_versions")  # type: ignore[attr-defined]
    for pagina in paginador.paginate(Bucket=bucket):
        objetos = [
            {"Key": v["Key"], "VersionId": v["VersionId"]}
            for v in pagina.get("Versions", []) + pagina.get("DeleteMarkers", [])
        ]
        for i in range(0, len(objetos), 1000):
            lote = objetos[i : i + 1000]
            kwargs = {"Bucket": bucket, "Delete": {"Objects": lote, "Quiet": True}}
            if gobernanza:
                kwargs["BypassGovernanceRetention"] = True
            s3.delete_objects(**kwargs)  # type: ignore[attr-defined]
            borrados += len(lote)
    return borrados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entorno", required=True)
    ap.add_argument("--confirmar", action="store_true")
    args = ap.parse_args()
    if args.entorno != "dev" or not args.confirmar:
        sys.exit("solo dev y con --confirmar")
    cuenta = boto3.client("sts").get_caller_identity()["Account"]
    s3 = boto3.client("s3")
    for sufijo, gobernanza in (("datos", False), ("auditoria", True), ("accesos", False)):
        bucket = f"memo-credito-dev-{sufijo}-{cuenta}"
        try:
            print(f"{bucket}: {vaciar(s3, bucket, gobernanza)} versiones borradas")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "NoSuchBucket":
                raise
            print(f"{bucket}: no existe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
