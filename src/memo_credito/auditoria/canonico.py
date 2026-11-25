"""JSON canónico (claves ordenadas, sin espacios, Decimal como texto) y hashes."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, SecretStr


def a_json(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return a_json(obj.model_dump(mode="json"))
    if isinstance(obj, dict):
        return {str(k): a_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [a_json(v) for v in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, SecretStr):
        return "**********"
    return obj


def json_canonico(obj: Any) -> str:
    return json.dumps(a_json(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def sha256_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()
