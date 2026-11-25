"""CLI `memo`: procesamiento local, evals, diagrama, evidencia, auditoría y operaciones contra AWS."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from memo_credito.config import cargar_config

RAIZ = Path(__file__).resolve().parents[3]
DATOS = RAIZ / "datos" / "reto_ki_datos" / "solicitudes"


def _cliente(modo: str, cfg):  # type: ignore[no-untyped-def]
    if modo == "simulado":
        from memo_credito.llm.simulado import ClienteSimulado

        return ClienteSimulado()
    from memo_credito.llm.bedrock import ClienteBedrock

    return ClienteBedrock(
        cfg,
        guardrail_id=os.environ.get("MEMO_GUARDRAIL_ID"),
        guardrail_version=os.environ.get("MEMO_GUARDRAIL_VERSION"),
    )


def cmd_procesar(args: argparse.Namespace) -> int:
    from memo_credito.pipeline import procesar_carpeta

    cfg = cargar_config(args.entorno)
    folios = sorted(p.name for p in Path(args.datos).iterdir() if p.is_dir()) if args.todas else args.folios
    salida = Path(args.salida)
    codigo = 0
    for folio in folios:
        res = procesar_carpeta(
            Path(args.datos) / folio, cfg, _cliente(args.modo, cfg), salida_base=salida, fecha=args.fecha
        )
        red = res.redaccion
        estado = "OK" if not red.errores_finales else f"ERRORES {[e.regla for e in red.errores_finales]}"
        print(
            f"{folio}: alertas={list(res.analisis.codigos_alerta)} origen={red.estado.origen} llamadas={len(red.estado.llamadas)} "
            f"costo=${red.costo_usd:.4f} memo={res.ruta_memo} sha256={res.memo_sha256[:12]} {estado}"
        )
        codigo |= 1 if red.errores_finales else 0
    return codigo


def cmd_evals(args: argparse.Namespace) -> int:
    from memo_credito import evals
    from memo_credito.pipeline import ahora_mx

    cfg = cargar_config(args.entorno)
    if args.verificar_candado:
        if evals.candado_vigente(cfg):
            print("Candado de evals vigente: el prompt y el modelo actuales están aprobados por evals reales.")
            return 0
        print("El prompt o el modelo cambiaron y no hay una aprobación vigente: corre `tareas.py evals-real`.")
        return 1
    rpm = args.rpm if args.modo == "bedrock" else None
    resultados = evals.correr(args.modo, args.corridas, cfg, Path(args.datos), RAIZ / "evals" / "casos.yaml", rpm)
    tabla = evals.scorecard(resultados, args.modo, cfg)
    print(tabla)
    sufijo = "local_simulado" if args.modo == "simulado" else f"bedrock_{args.corridas}x"
    ruta = RAIZ / "evals" / "scorecards" / f"{ahora_mx():%Y%m%d_%H%M}_{sufijo}.md"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(tabla, encoding="utf-8", newline="\n")
    todos = all(r.ok for r in resultados)
    if args.modo == "bedrock" and todos:
        evals.registrar_aprobacion(resultados, cfg, ruta)
        print(f"Aprobación registrada en {evals.ruta_aprobaciones()}")
    return 0 if todos else 1


def cmd_diagrama(args: argparse.Namespace) -> int:
    from memo_credito.config import cargar_config as cc
    from memo_credito.llm.simulado import ClienteSimulado
    from memo_credito.memo.grafo import construir_grafo, diagrama_mermaid
    from memo_credito.memo.plantilla import plantilla

    cfg = cc(args.entorno)
    grafo = construir_grafo(ClienteSimulado(), lambda _s: (), plantilla, lambda _r: 0, cfg.llm.max_correcciones)  # type: ignore[arg-type,return-value]
    destino = Path(args.salida)
    texto = diagrama_mermaid(grafo)
    if args.verificar:
        actual = destino.read_text(encoding="utf-8") if destino.exists() else ""
        if actual != texto:
            print(f"{destino} está desactualizado: corre `tareas.py diagrama`.")
            return 1
        print("Diagrama del grafo actualizado.")
        return 0
    destino.write_text(texto, encoding="utf-8", newline="\n")
    print(f"Diagrama escrito en {destino}")
    return 0


def cmd_evidencia(args: argparse.Namespace) -> int:
    import json

    carpeta = Path(args.salida) / args.folio / "auditoria"
    ejecuciones = sorted(p for p in carpeta.iterdir() if p.is_dir())
    analisis = json.loads((ejecuciones[-1] / "02_analisis.json").read_text(encoding="utf-8"))["analisis"]
    for ind in analisis["indicadores"]:
        if ind["id"] == args.indicador:
            print(f"{ind['nombre']}: {ind['valor_texto']} ({ind['estado']})")
            print(f"  fórmula: {ind['formula']}")
            print(f"  fuente:  {ind['fuente_memo']}")
            for k, v in ind["entradas"].items():
                print(f"  entrada {k} = {v}")
            return 0
    print(f"indicador no encontrado; disponibles: {[i['id'] for i in analisis['indicadores']]}")
    return 1


def cmd_auditoria(args: argparse.Namespace) -> int:
    from memo_credito.auditoria.paquete import AlmacenLocal, verificar_paquete

    carpeta = Path(args.salida) / args.folio / "auditoria"
    codigo = 0
    for ejecucion in sorted(p.name for p in carpeta.iterdir() if p.is_dir()):
        problemas = verificar_paquete(AlmacenLocal(carpeta), ejecucion)
        print(f"{args.folio}/{ejecucion}: {problemas if problemas else 'íntegro'}")
        codigo |= 1 if problemas else 0
    return codigo


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="memo", description="Memo de crédito asistido")
    p.add_argument("--entorno", default=os.environ.get("ENTORNO", "dev"))
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("procesar", help="procesa solicitudes en local y escribe memo + auditoría")
    s.add_argument("folios", nargs="*")
    s.add_argument("--todas", action="store_true")
    s.add_argument("--datos", default=str(DATOS))
    s.add_argument("--salida", default=str(RAIZ / "salidas"))
    s.add_argument("--modo", choices=["simulado", "bedrock"], default="simulado")
    s.add_argument("--fecha", default=None)
    s.set_defaults(fn=cmd_procesar)

    s = sub.add_parser("evals", help="evals por caso con scorecard")
    s.add_argument("--modo", choices=["simulado", "bedrock"], default="simulado")
    s.add_argument("--corridas", type=int, default=1)
    s.add_argument("--datos", default=str(DATOS))
    s.add_argument("--verificar-candado", action="store_true")
    s.add_argument(
        "--rpm", type=float, default=6.0, help="máximo de llamadas por minuto al LLM real (cuota de la cuenta)"
    )
    s.set_defaults(fn=cmd_evals)

    s = sub.add_parser("diagrama", help="genera el diagrama Mermaid del grafo desde el código")
    s.add_argument("--salida", default=str(RAIZ / "docs" / "grafo_redaccion.mmd"))
    s.add_argument("--verificar", action="store_true")
    s.set_defaults(fn=cmd_diagrama)

    s = sub.add_parser("evidencia", help="muestra la cadena de evidencia de un indicador")
    s.add_argument("folio")
    s.add_argument("indicador")
    s.add_argument("--salida", default=str(RAIZ / "salidas"))
    s.set_defaults(fn=cmd_evidencia)

    s = sub.add_parser("auditoria", help="verifica hashes y cadena del paquete de auditoría local")
    s.add_argument("folio")
    s.add_argument("--salida", default=str(RAIZ / "salidas"))
    s.set_defaults(fn=cmd_auditoria)

    s = sub.add_parser("invocar", help="invoca un handler de Lambda en proceso con un evento JSON (sin Docker)")
    s.add_argument("handler", choices=HANDLERS)
    s.add_argument("evento", help="ruta a un JSON con el evento")
    s.set_defaults(fn=cmd_invocar)

    try:
        from memo_credito.cli import nube

        nube.registrar(sub)
    except ImportError:  # pragma: no cover
        pass
    return p


HANDLERS = [
    "ingesta",
    "extraer",
    "validar_calcular",
    "redactar",
    "esperar_revision",
    "aplicar_revision",
    "registrar_revision",
]


def cmd_invocar(args: argparse.Namespace) -> int:
    """Equivalente local de `sam local invoke` sin Docker; usa las variables de entorno y credenciales actuales."""
    import importlib
    from types import SimpleNamespace

    modulo = importlib.import_module(f"memo_credito.handlers.{args.handler}")
    evento = json.loads(Path(args.evento).read_text(encoding="utf-8"))
    contexto = SimpleNamespace(
        function_name=f"local-{args.handler}",
        memory_limit_in_mb=1024,
        invoked_function_arn="arn:aws:lambda:local:000000000000:function:local",
        aws_request_id="local",
        get_remaining_time_in_millis=lambda: 300_000,
    )
    print(json.dumps(modulo.handler(evento, contexto), ensure_ascii=False, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = construir_parser().parse_args(argv)
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
