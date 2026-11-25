"""Construye el paquete de las Lambdas (arm64, Python 3.12) sin Docker y de forma reproducible.

1. Exporta las dependencias de ejecución fijadas en uv.lock (sin grupos dev ni infra, con hashes).
2. Las instala para aarch64-manylinux_2_28 solo con wheels binarios (--only-binary :all:).
3. Copia el paquete memo_credito, config/ y prompts/ (la Lambda los lee de /var/task).
4. Verifica que toda biblioteca nativa sea ELF aarch64 y que el paquete quepa en el límite de 250 MB.

Uso: uv run python scripts/build_lambda.py [--salida build/lambda]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
EM_AARCH64 = 183
LIMITE_BYTES = 250 * 1024 * 1024


def correr(*cmd: str) -> None:
    exe = shutil.which(cmd[0]) or cmd[0]
    subprocess.run([exe, *cmd[1:]], cwd=RAIZ, check=True)


def maquina_elf(ruta: Path) -> int | None:
    with ruta.open("rb") as f:
        cabecera = f.read(20)
    if cabecera[:4] != b"\x7fELF":
        return None
    return int.from_bytes(cabecera[18:20], "little" if cabecera[5] == 1 else "big")


def verificar(salida: Path) -> None:
    ajenos = [
        str(p.relative_to(salida))
        for p in salida.rglob("*.so*")
        if p.is_file() and maquina_elf(p) not in (None, EM_AARCH64)
    ]
    if ajenos:
        sys.exit(f"bibliotecas que no son aarch64: {ajenos[:5]}")
    total = sum(p.stat().st_size for p in salida.rglob("*") if p.is_file())
    if total > LIMITE_BYTES:
        sys.exit(f"el paquete mide {total / 1e6:.1f} MB, más que el límite de Lambda")
    print(f"paquete listo: {total / 1e6:.1f} MB sin comprimir en {salida}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salida", default=str(RAIZ / "build" / "lambda"))
    args = ap.parse_args()
    salida = Path(args.salida).resolve()
    if salida.exists():
        shutil.rmtree(salida)
    salida.mkdir(parents=True)
    requisitos = salida.parent / "requirements-lambda.txt"
    correr(
        "uv",
        "export",
        "--frozen",
        "--no-default-groups",
        "--no-emit-project",
        "--format",
        "requirements-txt",
        "--output-file",
        str(requisitos),
        "--quiet",
    )
    correr(
        "uv",
        "pip",
        "install",
        "--quiet",
        "--target",
        str(salida),
        "--python-platform",
        "aarch64-manylinux_2_28",
        "--python-version",
        "3.12",
        "--only-binary",
        ":all:",
        "--no-deps",
        "--require-hashes",
        "-r",
        str(requisitos),
    )
    shutil.copytree(
        RAIZ / "src" / "memo_credito",
        salida / "memo_credito",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "cli"),
    )
    for carpeta in ("config", "prompts"):
        shutil.copytree(RAIZ / carpeta, salida / carpeta)
    for cache in list(salida.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    verificar(salida)


if __name__ == "__main__":
    main()
