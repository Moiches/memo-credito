"""Puerto del modelo: el grafo es el mismo en modo simulado y Bedrock; solo cambia el cliente."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from memo_credito.auditoria.canonico import sha256_texto
from memo_credito.config import Config, directorio_prompts
from memo_credito.memo.modelos import PeticionRedaccion, RespuestaLLM


class ClienteLLM(Protocol):
    modelo: str
    modo: str

    def redactar(self, peticion: PeticionRedaccion) -> RespuestaLLM: ...


def cargar_prompt(cfg: Config) -> str:
    return (directorio_prompts() / cfg.llm.prompt).read_text(encoding="utf-8")


def cargar_esquema_texto(cfg: Config) -> str:
    return (directorio_prompts() / cfg.llm.esquema).read_text(encoding="utf-8")


def prompt_id(cfg: Config) -> str:
    """ "redaccion_v1@<sha256[:12]>": se registra en cada memo y en el candado de evals."""
    nombre = Path(cfg.llm.prompt).stem
    return f"{nombre}@{sha256_texto(cargar_prompt(cfg))[:12]}"


def huella_modelo(cfg: Config) -> dict[str, Any]:
    return {
        "prompt_sha256": sha256_texto(cargar_prompt(cfg)),
        "esquema_sha256": sha256_texto(cargar_esquema_texto(cfg)),
        "modelo_id": cfg.llm.modelo_id,
        "effort": cfg.llm.effort,
        "max_tokens": cfg.llm.max_tokens,
        "max_correcciones": cfg.llm.max_correcciones,
    }
