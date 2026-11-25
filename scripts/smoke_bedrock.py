"""Smoke de Bedrock desde la máquina local: una llamada Converse mínima con el modelo de config/base.yaml.

Credenciales: el perfil de AWS o, solo en local, una API key de corto plazo en AWS_BEARER_TOKEN_BEDROCK
(nunca en el repo, el pipeline ni Lambda). Si existen MEMO_GUARDRAIL_ID/VERSION se aplica el guardrail.
Texto sintético; no envía datos de clientes.
"""

from __future__ import annotations

import os
import sys
import time

import boto3

from memo_credito.config import cargar_config


def main() -> int:
    cfg = cargar_config(os.environ.get("ENTORNO", "dev"))
    cliente = boto3.client("bedrock-runtime", region_name=cfg.llm.region)
    peticion = {
        "modelId": cfg.llm.modelo_id,
        "messages": [{"role": "user", "content": [{"text": "Responde solo con la palabra: listo"}]}],
        "inferenceConfig": {"maxTokens": 16},
    }
    if os.environ.get("MEMO_GUARDRAIL_ID"):
        peticion["guardrailConfig"] = {
            "guardrailIdentifier": os.environ["MEMO_GUARDRAIL_ID"],
            "guardrailVersion": os.environ.get("MEMO_GUARDRAIL_VERSION", "DRAFT"),
        }
    inicio = time.perf_counter()
    r = cliente.converse(**peticion)
    ms = (time.perf_counter() - inicio) * 1000
    uso = r.get("usage", {})
    print(
        f"modelo={cfg.llm.modelo_id} stopReason={r['stopReason']} entrada={uso.get('inputTokens')} "
        f"salida={uso.get('outputTokens')} latencia={ms:.0f} ms"
    )
    return 0 if r["stopReason"] in ("end_turn", "max_tokens") else 1


if __name__ == "__main__":
    sys.exit(main())
