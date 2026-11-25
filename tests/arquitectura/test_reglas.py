"""Reglas de arquitectura de AGENTS.md verificadas por AST."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "memo_credito"


def _archivos(*paquetes: str) -> list[Path]:
    return [p for paquete in paquetes for p in (SRC / paquete).rglob("*.py")]


def _imports(ruta: Path) -> set[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            mods.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            mods.add(nodo.module)
    return mods


def test_sin_float_en_dominio_calculos_y_memo():
    for ruta in _archivos("dominio", "calculos", "memo"):
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            assert not (isinstance(nodo, ast.Name) and nodo.id == "float"), (
                f"float en {ruta.name}:{getattr(nodo, 'lineno', '?')}"
            )
            assert not (isinstance(nodo, ast.Constant) and isinstance(nodo.value, float)), (
                f"literal float en {ruta.name}:{nodo.lineno}"
            )


def test_dominio_sin_dependencias_de_infraestructura():
    prohibidos = ("boto3", "botocore", "aws_cdk", "langgraph", "langchain", "aws_lambda_powertools")
    for ruta in _archivos("dominio", "calculos", "validacion"):
        for mod in _imports(ruta):
            assert not mod.startswith(prohibidos), f"{ruta.name} importa {mod}"


def test_langgraph_solo_en_el_grafo():
    for ruta in SRC.rglob("*.py"):
        if any(m.startswith(("langgraph", "langchain")) for m in _imports(ruta)):
            assert ruta.relative_to(SRC).as_posix() == "memo/grafo.py", ruta


def test_sin_abstracciones_de_langchain_para_bedrock():
    for ruta in SRC.rglob("*.py"):
        texto = ruta.read_text(encoding="utf-8")
        assert "langchain_aws" not in texto and "ChatBedrock" not in texto, ruta


def test_bedrock_runtime_solo_en_el_adaptador():
    for ruta in SRC.rglob("*.py"):
        if '"bedrock-runtime"' in ruta.read_text(encoding="utf-8"):
            assert ruta.relative_to(SRC).as_posix() == "llm/bedrock.py", ruta
