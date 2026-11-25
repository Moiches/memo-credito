"""Comandos de una línea del proyecto (multiplataforma). El Makefile solo delega aquí.

Uso:  uv run python tareas.py <objetivo> [--entorno dev] [--perfil <perfil>] [--n 5]
En Windows sin make, este es el punto de entrada.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent


def correr(*cmd: str, env: dict[str, str] | None = None) -> None:
    exe = shutil.which(cmd[0]) or cmd[0]
    print(f"$ {' '.join(cmd)}", flush=True)
    resultado = subprocess.run([exe, *cmd[1:]], cwd=RAIZ, env={**os.environ, **(env or {})}, check=False)
    if resultado.returncode != 0:
        sys.exit(resultado.returncode)


def commit_actual() -> str:
    git = shutil.which("git")
    if not git:
        return "desconocido"
    r = subprocess.run([git, "rev-parse", "--short=12", "HEAD"], cwd=RAIZ, capture_output=True, text=True, check=False)
    return r.stdout.strip() or "desconocido"


def cdk(args: argparse.Namespace, *resto: str) -> None:
    perfil = ["--profile", args.perfil] if args.perfil else []
    correr(
        "npx",
        "cdk",
        *resto,
        "-c",
        f"entorno={args.entorno}",
        *perfil,
        env={"ENTORNO": args.entorno, "GIT_COMMIT": commit_actual()},
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("objetivo")
    p.add_argument("--entorno", default=os.environ.get("ENV", "dev"))
    p.add_argument("--perfil", default=os.environ.get("PROFILE") or os.environ.get("AWS_PROFILE"))
    p.add_argument("--n", type=int, default=int(os.environ.get("N", "5")))
    p.add_argument("--modo", default=os.environ.get("MODO", "simulado"))
    p.add_argument("--folio", default=os.environ.get("FOLIO"))
    args, extra = p.parse_known_args()
    o = args.objetivo
    perfil_env = {"AWS_PROFILE": args.perfil} if args.perfil else {}

    if o == "setup":
        correr("uv", "sync", "--frozen")
        correr("npm", "ci")
        correr("uv", "run", "pre-commit", "install")
    elif o == "lint":
        correr("uv", "run", "ruff", "check", ".")
        correr("uv", "run", "ruff", "format", "--check", ".")
        correr("uv", "run", "mypy")
    elif o == "test":
        correr("uv", "run", "pytest", *extra)
    elif o == "check":
        correr("uv", "run", "ruff", "check", ".")
        correr("uv", "run", "ruff", "format", "--check", ".")
        correr("uv", "run", "mypy")
        correr("uv", "run", "pytest")
    elif o == "evals":
        correr("uv", "run", "memo", "evals", "--modo", "simulado")
    elif o == "evals-real":
        correr("uv", "run", "memo", "evals", "--modo", "bedrock", "--corridas", str(args.n), env=perfil_env)
    elif o == "procesar":
        correr("uv", "run", "memo", "procesar", "--todas", "--modo", args.modo, env=perfil_env)
    elif o == "diagrama":
        correr("uv", "run", "memo", "diagrama")
    elif o == "build-lambda":
        correr("uv", "run", "python", "scripts/build_lambda.py")
    elif o == "synth":
        correr("uv", "run", "python", "scripts/build_lambda.py")
        cdk(args, "synth", "--quiet")
    elif o == "diff":
        correr("uv", "run", "python", "scripts/build_lambda.py")
        cdk(args, "diff", f"MemoCredito-{args.entorno}/*")
    elif o == "deploy":
        correr("uv", "run", "python", "scripts/build_lambda.py")
        cdk(args, "deploy", f"MemoCredito-{args.entorno}/*", "--require-approval", "never")
    elif o == "deploy-cicd":
        # Requiere el repositorio de GitHub: uv run python tareas.py deploy-cicd -c repo=<owner>/<repo>
        cdk(args, "deploy", "MemoCredito-Cicd", "--require-approval", "never", *extra)
    elif o == "smoke":
        correr(
            "uv",
            "run",
            "memo",
            "--entorno",
            args.entorno,
            "enviar",
            args.folio or "SOL-0001",
            "--esperar",
            env=perfil_env,
        )
    elif o == "smoke-bedrock":
        correr("uv", "run", "python", "scripts/smoke_bedrock.py", env=perfil_env)
    elif o == "probar-guardrail":
        correr("uv", "run", "python", "scripts/probar_guardrail.py", "--entorno", args.entorno, env=perfil_env)
    elif o == "integracion":
        correr(
            "uv",
            "run",
            "pytest",
            "-m",
            "integracion",
            "tests/integracion",
            "-o",
            "addopts=-q",
            env={**perfil_env, "ENTORNO": args.entorno},
        )
    elif o == "carga":
        correr(
            "uv",
            "run",
            "python",
            "scripts/carga.py",
            "--entorno",
            args.entorno,
            "--n",
            str(args.n),
            "--modo",
            args.modo,
            *extra,
            env=perfil_env,
        )
    elif o == "revisar":
        correr("uv", "run", "memo", "--entorno", args.entorno, "revisar", *extra, env=perfil_env)
    elif o == "reprocesar":
        correr("uv", "run", "memo", "--entorno", args.entorno, "reprocesar", args.folio or "", *extra, env=perfil_env)
    elif o == "activar-etiquetas-costo":
        correr("uv", "run", "python", "scripts/activar_etiquetas_costo.py", env=perfil_env)
    elif o == "destroy":
        if args.entorno == "prod":
            sys.exit("destroy no está permitido en prod desde tareas.py")
        correr(
            "uv",
            "run",
            "python",
            "scripts/purgar_auditoria_dev.py",
            "--entorno",
            args.entorno,
            "--confirmar",
            env=perfil_env,
        )
        cdk(args, "destroy", f"MemoCredito-{args.entorno}/*", "--force")
    else:
        sys.exit(f"objetivo desconocido: {o}")


if __name__ == "__main__":
    main()
