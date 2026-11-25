"""Utilidades comunes de las Lambdas: configuración por entorno, clientes, almacén S3 write-once, estado en
DynamoDB, logger sin PII y métricas.

Reglas: los logs solo llevan ids, pasos, conteos y códigos; nunca contenido de documentos ni PII.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Metrics, Tracer
from botocore.exceptions import ClientError

from memo_credito.config import Config, cargar_config

SERVICIO = "memo-credito"
_PATRONES_PII = [
    re.compile(r"\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z\d]\d\b"),  # CURP
    re.compile(r"\b[A-ZÑ&]{4}\d{6}[A-Z\d]{3}\b"),  # RFC persona física
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # correo
    re.compile(r"(?<!\d)(?:\d{2}[\s-]?\d{4}[\s-]?\d{4})(?!\d)"),  # teléfono MX
]


class FiltroPii(logging.Filter):
    """Defensa adicional: enmascara patrones de PII si alguno llegara a un mensaje de log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for patron in _PATRONES_PII:
                record.msg = patron.sub("[PII]", record.msg)
        return True


logger = Logger(service=SERVICIO)
logger.addFilter(FiltroPii())
tracer = Tracer(service=SERVICIO)
metrics = Metrics(service=SERVICIO)


def env(nombre: str, defecto: str | None = None) -> str:
    valor = os.environ.get(nombre, defecto)
    if valor is None:
        raise RuntimeError(f"falta la variable de entorno {nombre}")
    return valor


@lru_cache(maxsize=1)
def config() -> Config:
    return cargar_config(env("ENTORNO", "dev"))


@lru_cache(maxsize=1)
def s3() -> Any:
    return boto3.client("s3")


@lru_cache(maxsize=1)
def sfn() -> Any:
    return boto3.client("stepfunctions")


@lru_cache(maxsize=1)
def tabla() -> Any:
    return boto3.resource("dynamodb").Table(env("TABLA_SOLICITUDES"))


def ahora_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def leer_s3(bucket: str, clave: str) -> bytes:
    return bytes(s3().get_object(Bucket=bucket, Key=clave)["Body"].read())


def escribir_s3(bucket: str, clave: str, contenido: bytes, tipo: str = "application/json") -> None:
    s3().put_object(Bucket=bucket, Key=clave, Body=contenido, ContentType=tipo, ChecksumAlgorithm="SHA256")


class AlmacenS3:
    """Almacén write-once sobre S3 (If-None-Match: *). El bucket de auditoría además tiene Object Lock."""

    def __init__(self, bucket: str, prefijo_base: str = "") -> None:
        self.bucket = bucket
        self.base = prefijo_base

    def _clave(self, clave: str) -> str:
        return f"{self.base}{clave}"

    def escribir(self, clave: str, contenido: bytes) -> None:
        try:
            s3().put_object(
                Bucket=self.bucket,
                Key=self._clave(clave),
                Body=contenido,
                IfNoneMatch="*",
                ContentType="application/json",
                ChecksumAlgorithm="SHA256",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise
            if self.leer(clave) != contenido:
                raise FileExistsError(f"la parte {clave} ya existe y es inmutable") from None

    def leer(self, clave: str) -> bytes:
        return leer_s3(self.bucket, self._clave(clave))

    def existe(self, clave: str) -> bool:
        try:
            s3().head_object(Bucket=self.bucket, Key=self._clave(clave))
            return True
        except ClientError as exc:
            # Solo "no existe" es False: un 403 (permiso faltante) debe fallar, no omitir partes del manifiesto.
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise


def actualizar_estado(
    folio: str, condicion: str | None = None, valores_condicion: dict[str, Any] | None = None, **campos: Any
) -> None:
    campos["actualizado"] = ahora_iso()
    nombres = {f"#{k}": k for k in campos}
    valores = {f":{k}": v for k, v in campos.items()}
    expresion = "SET " + ", ".join(f"#{k} = :{k}" for k in campos)
    kwargs: dict[str, Any] = {
        "Key": {"folio": folio, "sk": "ESTADO"},
        "UpdateExpression": expresion,
        "ExpressionAttributeNames": nombres,
        "ExpressionAttributeValues": {**valores, **(valores_condicion or {})},
    }
    if condicion:
        kwargs["ConditionExpression"] = condicion
    tabla().update_item(**kwargs)


def leer_estado(folio: str) -> dict[str, Any]:
    return dict(tabla().get_item(Key={"folio": folio, "sk": "ESTADO"}).get("Item") or {})


def correlacionar(folio: str, ejecucion: str) -> None:
    logger.append_keys(correlation_id=f"{folio}#{ejecucion}", folio=folio)
