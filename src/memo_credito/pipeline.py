"""Procesamiento de punta a punta de una solicitud (local). Las Lambdas usan las mismas funciones por pasos."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from memo_credito.auditoria.canonico import a_json, sha256_texto
from memo_credito.auditoria.paquete import AlmacenLocal, escribir_manifiesto, escribir_parte, proyectar_extraccion
from memo_credito.calculos.analisis import analizar
from memo_credito.config import Config
from memo_credito.dominio.modelos import Analisis
from memo_credito.extraccion.expediente import extraer_archivos, leer_carpeta, sha256_de
from memo_credito.extraccion.solicitud import canarios
from memo_credito.llm.puerto import huella_modelo, prompt_id
from memo_credito.memo.ensamblador import Metadatos
from memo_credito.memo.hechos import construir_hechos
from memo_credito.memo.modelos import HechosParaLLM
from memo_credito.memo.servicio import ResultadoRedaccion, redactar_memo

ZONA_MX = timezone(timedelta(hours=-6))  # Ciudad de México, sin horario de verano desde 2022


def ahora_mx() -> datetime:
    return datetime.now(ZONA_MX)


def commit_actual() -> str:
    try:
        salida = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)  # noqa: S607
        return salida.stdout.strip() or "desconocido"
    except OSError:
        return "desconocido"


@dataclass(frozen=True)
class ResultadoProceso:
    folio: str
    analisis: Analisis
    hechos: HechosParaLLM
    redaccion: ResultadoRedaccion
    memo_sha256: str
    canarios: tuple[str, ...]
    ruta_memo: Path | None = None
    ruta_auditoria: Path | None = None


def parte_redaccion(
    res: ResultadoRedaccion, cfg: Config, meta: Metadatos, invocacion: str | None = None
) -> dict[str, Any]:
    return {
        "commit": meta.commit,
        "ejecucion": meta.ejecucion,
        "prompt": {"id": meta.prompt_id, **{k: v for k, v in huella_modelo(cfg).items() if k.endswith("sha256")}},
        "modelo": {
            "id": meta.modelo,
            "invocacion": invocacion or meta.modelo,
            "modo": meta.modo,
            "effort": cfg.llm.effort,
            "max_tokens": cfg.llm.max_tokens,
            "max_correcciones": cfg.llm.max_correcciones,
        },
        "intentos": [
            {
                "intento": ll.intento,
                "tipo": ll.tipo,
                "peticion": ll.respuesta.peticion,
                "respuesta_cruda": ll.respuesta.texto_crudo,
                "stop_reason": ll.respuesta.stop_reason,
                "uso": ll.respuesta.uso,
                "guardrail": ll.respuesta.guardrail,
                "costo_usd": ll.costo_usd,
                "latencia_ms": ll.respuesta.latencia_ms,
                "reintentos": ll.respuesta.reintentos,
                "request_id": ll.respuesta.request_id,
                "errores_verificacion": res.estado.errores_por_intento[i]
                if i < len(res.estado.errores_por_intento)
                else [],
            }
            for i, ll in enumerate(res.estado.llamadas)
        ],
        "origen": res.estado.origen,
        "requiere_revision": res.estado.requiere_revision,
        "motivo_plantilla": res.estado.motivo_plantilla,
        "errores_finales": res.errores_finales,
        "costo_total_usd": res.costo_usd,
        "memo_sha256": sha256_texto(res.memo),
        "memo_md": res.memo,
    }


def procesar_carpeta(
    carpeta: Path,
    cfg: Config,
    cliente: Any,
    salida_base: Path | None = None,
    fecha: str | None = None,
    ejecucion: str | None = None,
    commit: str | None = None,
) -> ResultadoProceso:
    archivos = leer_carpeta(carpeta)
    canarios_sol = canarios(archivos["solicitud.json"]) if "solicitud.json" in archivos else ()
    exp, resultados = extraer_archivos(archivos, cfg)
    analisis = analizar(exp, cfg)
    hechos = construir_hechos(exp, analisis, cfg)
    momento = ahora_mx()
    meta = Metadatos(
        fecha=fecha or momento.date().isoformat(),
        ejecucion=ejecucion or f"local-{momento:%Y%m%dT%H%M%S}",
        prompt_id=prompt_id(cfg),
        modelo=cliente.modelo,
        modo=cliente.modo,
        commit=commit or commit_actual(),
    )
    res = redactar_memo(exp, analisis, hechos, cliente, cfg, meta, canarios_sol)
    memo_sha = sha256_texto(res.memo)
    ruta_memo = ruta_aud = None
    if salida_base is not None:
        destino = salida_base / exp.solicitud.folio
        destino.mkdir(parents=True, exist_ok=True)
        ruta_memo = destino / "memo.md"
        ruta_memo.write_text(res.memo, encoding="utf-8", newline="\n")
        ruta_aud = destino / "auditoria"
        almacen = AlmacenLocal(ruta_aud)
        prefijo = meta.ejecucion
        escribir_parte(
            almacen,
            prefijo,
            "00_entrada.json",
            {
                "folio": exp.solicitud.folio,
                "documentos": [
                    {"nombre": n, "sha256": sha256_de(d), "bytes": len(d)} for n, d in sorted(archivos.items())
                ],
                "input_hash": sha256_texto("|".join(f"{n}:{sha256_de(d)}" for n, d in sorted(archivos.items()))),
                "recibido_en": momento.isoformat(),
            },
        )
        escribir_parte(
            almacen, prefijo, "01_extraccion.json", {"documentos": [proyectar_extraccion(r) for r in resultados]}
        )
        escribir_parte(
            almacen,
            prefijo,
            "02_analisis.json",
            {
                "analisis": a_json(analisis),
                "hechos": a_json(hechos),
                "config": {
                    "politica": a_json(cfg.politica),
                    "limites": a_json(cfg.limites),
                    "fuente_precios": cfg.precios.fuente,
                },
            },
        )
        escribir_parte(almacen, prefijo, "03_redaccion.json", parte_redaccion(res, cfg, meta))
        escribir_manifiesto(almacen, prefijo)
    return ResultadoProceso(exp.solicitud.folio, analisis, hechos, res, memo_sha, canarios_sol, ruta_memo, ruta_aud)
