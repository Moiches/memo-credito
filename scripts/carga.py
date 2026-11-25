"""Prueba de carga en dev: envía N expedientes LT-<corrida>-<n> (clones de SOL-0001..0008) y mide el tiempo hasta
PENDIENTE_REVISION. Reporta p50/p95, errores y memos con plantilla. Solo datos sintéticos del reto.

Uso: uv run python tareas.py carga --entorno dev --perfil <perfil> --n 20 [--modo simulado|bedrock]
"""

from __future__ import annotations

import argparse
import secrets
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import boto3

from memo_credito.cli import nube

ORIGENES = [f"SOL-000{i}" for i in range(1, 9)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entorno", default="dev")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--modo", default="simulado", choices=["simulado", "bedrock"])
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    if args.entorno != "dev":
        sys.exit("la carga solo corre en dev")
    sesion = boto3.session.Session()
    corrida = secrets.token_hex(3).upper()
    folios = {f"LT-{corrida}-{i:03d}": ORIGENES[i % len(ORIGENES)] for i in range(args.n)}
    inicio: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for folio, origen in folios.items():
            inicio[folio] = time.time()
            pool.submit(nube.enviar, origen, args.entorno, folio, args.modo, sesion=sesion)
    duraciones, estados = [], {}
    for folio in folios:
        try:
            item = nube.esperar(folio, args.entorno, timeout_s=args.timeout, sesion=sesion)
            estados[folio] = item.get("estado")
            # Momento real en que la Lambda dejó el borrador listo (no cuando este script lo consultó)
            fin = (
                datetime.fromisoformat(str(item["pendiente_desde"])).timestamp()
                if item.get("pendiente_desde")
                else time.time()
            )
            duraciones.append(fin - inicio[folio])
        except TimeoutError:
            estados[folio] = "TIMEOUT"
    ok = sum(1 for e in estados.values() if e == "PENDIENTE_REVISION")
    print(
        f"corrida {corrida}: {ok}/{args.n} en PENDIENTE_REVISION; otros: "
        f"{ {e: list(estados.values()).count(e) for e in set(estados.values()) if e != 'PENDIENTE_REVISION'} }"
    )
    if duraciones:
        p95 = sorted(duraciones)[max(0, int(len(duraciones) * 0.95) - 1)]
        print(
            f"segundos hasta el borrador: p50={statistics.median(duraciones):.1f} p95={p95:.1f} max={max(duraciones):.1f}"
        )
    return 0 if ok == args.n else 1


if __name__ == "__main__":
    sys.exit(main())
