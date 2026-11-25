"""Evals por caso con scorecard y candado de aprobación de prompt/modelo.

- Modo simulado (CI): debe pasar al 100%.
- Modo bedrock: N corridas por caso; si todas pasan, se registra la aprobación en evals/aprobaciones.json.
  El CI (sin credenciales) verifica que la huella actual de prompt + modelo esté aprobada.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from memo_credito.auditoria.canonico import json_canonico
from memo_credito.config import Config
from memo_credito.llm.puerto import huella_modelo
from memo_credito.memo.verificador import normalizar
from memo_credito.pipeline import ResultadoProceso, ahora_mx, procesar_carpeta

RAIZ = Path(__file__).resolve().parents[2]


@dataclass
class ResultadoCaso:
    folio: str
    corrida: int
    checks: dict[str, bool]
    origen: str
    llamadas: int
    tokens_entrada: int
    tokens_salida: int
    costo_usd: Decimal
    intervenciones_guardrail: int
    latencia_ms: int
    request_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())


def _cliente(modo: str, cfg: Config) -> Any:
    if modo == "simulado":
        from memo_credito.llm.simulado import ClienteSimulado

        return ClienteSimulado()
    from memo_credito.llm.bedrock import ClienteBedrock

    return ClienteBedrock(
        cfg,
        guardrail_id=os.environ.get("MEMO_GUARDRAIL_ID"),
        guardrail_version=os.environ.get("MEMO_GUARDRAIL_VERSION"),
    )


def evaluar_caso(res: ResultadoProceso, caso: dict[str, Any], corrida: int) -> ResultadoCaso:
    a, red = res.analisis, res.redaccion
    ind = {i.id: i.valor for i in a.indicadores}
    checks: dict[str, bool] = {}
    flujos_ok = all(
        (
            str(ind.get(f"flujo_neto_{k.replace('-', '_')}"))
            if ind.get(f"flujo_neto_{k.replace('-', '_')}") is not None
            else None
        )
        == v
        for k, v in caso["flujos"].items()
    )
    principales_ok = all(
        (str(ind[k]) if ind[k] is not None else None) == caso[k]
        for k in ("flujo_promedio", "pago_mensual", "capacidad_pago", "apalancamiento")
    )
    checks["indicadores"] = flujos_ok and principales_ok
    checks["alertas"] = sorted(a.codigos_alerta) == sorted(caso["alertas"])
    checks["verificador"] = not red.errores_finales and red.estado.origen == "llm"
    peticiones = normalizar(" ".join(json_canonico(ll.respuesta.peticion) for ll in red.estado.llamadas))
    memo_normal = normalizar(red.memo)
    canarios = [normalizar(c) for c in caso.get("pii_canarios", [])] + [c for c in res.canarios if not c.isdigit()]
    checks["sin_pii"] = not any(c and (c in memo_normal or c in peticiones) for c in canarios)
    if "inyeccion" in caso:
        frase = normalizar(caso["inyeccion"]["frase"])
        checks["inyeccion_no_llega"] = frase not in peticiones and frase not in memo_normal
    llamadas = red.estado.llamadas
    return ResultadoCaso(
        folio=res.folio,
        corrida=corrida,
        checks=checks,
        origen=red.estado.origen or "?",
        llamadas=len(llamadas),
        tokens_entrada=red.tokens[0],
        tokens_salida=red.tokens[1],
        costo_usd=red.costo_usd,
        intervenciones_guardrail=sum(1 for ll in llamadas if ll.respuesta.guardrail.intervino),
        latencia_ms=sum(ll.respuesta.latencia_ms for ll in llamadas),
        request_ids=[ll.respuesta.request_id for ll in llamadas if ll.respuesta.request_id],
    )


class LimitadorRitmo:
    """Espacia las llamadas al LLM para no rebasar `rpm` solicitudes por minuto en toda la corrida."""

    def __init__(self, rpm: float) -> None:
        self.intervalo = 60.0 / rpm
        self.ultima = float("-inf")

    def esperar(self) -> None:
        pendiente = self.ultima + self.intervalo - time.monotonic()
        if pendiente > 0:
            time.sleep(pendiente)
        self.ultima = time.monotonic()


class ClienteConRitmo:
    """Envuelve un ClienteLLM y aplica el limitador antes de cada llamada; el resto se delega."""

    def __init__(self, cliente: Any, limitador: LimitadorRitmo) -> None:
        self._cliente = cliente
        self._limitador = limitador

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._cliente, nombre)

    def redactar(self, *args: Any, **kwargs: Any) -> Any:
        self._limitador.esperar()
        return self._cliente.redactar(*args, **kwargs)


def correr(
    modo: str, corridas: int, cfg: Config, datos: Path, casos_yaml: Path, rpm: float | None = None
) -> list[ResultadoCaso]:
    casos = yaml.safe_load(casos_yaml.read_text(encoding="utf-8"))["casos"]
    resultados = []
    limitador = LimitadorRitmo(rpm) if rpm else None
    for folio, caso in casos.items():
        for corrida in range(1, corridas + 1):
            cliente = _cliente(modo, cfg)
            if limitador is not None:
                cliente = ClienteConRitmo(cliente, limitador)
            res = procesar_carpeta(
                datos / folio, cfg, cliente, salida_base=None, fecha="2026-11-25", ejecucion=f"eval-{corrida}"
            )
            resultados.append(evaluar_caso(res, caso, corrida))
    return resultados


def scorecard(resultados: list[ResultadoCaso], modo: str, cfg: Config) -> str:
    nombres = sorted({k for r in resultados for k in r.checks})
    lineas = [
        f"# Scorecard de evals — modo {modo}",
        "",
        f"Fecha: {ahora_mx():%Y-%m-%d %H:%M} (UTC-6) · modelo: {cfg.llm.modelo_id if modo != 'simulado' else 'simulado'} "
        f"· effort: {cfg.llm.effort} · corridas por caso: {max(r.corrida for r in resultados)}",
        "",
        "| Caso | "
        + " | ".join(nombres)
        + " | Aprobadas | Origen | Llamadas prom. | Tokens ent./sal. prom. | Costo prom. (USD) | Guardrail | Latencia p50/p95 (ms) |",
        "| --- | " + " | ".join("---" for _ in nombres) + " | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for folio in sorted({r.folio for r in resultados}):
        rs = [r for r in resultados if r.folio == folio]
        marcas = [("✅" if all(r.checks.get(n, True) for r in rs) else "❌") for n in nombres]
        lat = sorted(r.latencia_ms for r in rs)
        p95 = lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))]
        costo = sum((r.costo_usd for r in rs), Decimal(0)) / len(rs)
        lineas.append(
            f"| {folio} | "
            + " | ".join(marcas)
            + f" | {sum(r.ok for r in rs)}/{len(rs)} | {', '.join(sorted({r.origen for r in rs}))} "
            f"| {statistics.mean(r.llamadas for r in rs):.1f} "
            f"| {statistics.mean(r.tokens_entrada for r in rs):.0f}/{statistics.mean(r.tokens_salida for r in rs):.0f} "
            f"| {costo:.4f} | {sum(r.intervenciones_guardrail for r in rs)} | {statistics.median(lat):.0f}/{p95} |"
        )
    total = sum(r.ok for r in resultados)
    costo_total = sum((r.costo_usd for r in resultados), Decimal(0))
    lineas += ["", f"**Total: {total}/{len(resultados)} corridas aprobadas · costo total {costo_total:.4f} USD.**"]
    return "\n".join(lineas) + "\n"


def ruta_aprobaciones() -> Path:
    return RAIZ / "evals" / "aprobaciones.json"


def registrar_aprobacion(resultados: list[ResultadoCaso], cfg: Config, ruta_scorecard: Path) -> None:
    ruta = ruta_aprobaciones()
    datos = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {"aprobaciones": []}
    datos["aprobaciones"].append(
        {
            "huella": huella_modelo(cfg),
            "fecha": ahora_mx().isoformat(timespec="seconds"),
            "corridas": len(resultados),
            "aprobadas": sum(r.ok for r in resultados),
            "scorecard": ruta_scorecard.relative_to(RAIZ).as_posix(),
            "request_ids": [rid for r in resultados for rid in r.request_ids][:20],
        }
    )
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def candado_vigente(cfg: Config) -> bool:
    ruta = ruta_aprobaciones()
    if not ruta.exists():
        return False
    huella = huella_modelo(cfg)
    return any(
        a["huella"] == huella and a["aprobadas"] == a["corridas"]
        for a in json.loads(ruta.read_text(encoding="utf-8"))["aprobaciones"]
    )
