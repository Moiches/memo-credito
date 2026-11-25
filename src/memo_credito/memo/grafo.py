"""Ciclo evaluador-optimizador de la redacción como grafo de LangGraph 1.x.

Alcance (ADR-0002): solo el ciclo del LLM dentro de la Lambda de redacción. Sin checkpointer, sin herramientas,
sin telemetría de terceros (LangSmith desactivado). Step Functions orquesta todo lo demás.

Nodos: redactar -> verificar -> (fin | corregir -> verificar | plantilla). Máximo `max_correcciones` correcciones,
así que el modelo se llama como mucho 1 + max_correcciones veces.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from decimal import Decimal
from typing import Any

from langgraph.graph import END, START, StateGraph

from memo_credito.memo.modelos import (
    ErrorVerificacion,
    EstadoGrafo,
    HechosParaLLM,
    PeticionRedaccion,
    RegistroLlamada,
    RespuestaLLM,
    SalidaLLM,
)

# Ningún dato sale a servicios de terceros: LangSmith desactivado explícitamente.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

Verificador = Callable[[SalidaLLM | None], tuple[ErrorVerificacion, ...]]
Costeador = Callable[[RespuestaLLM], Decimal]
Subsegmento = Callable[[str], AbstractContextManager[Any]]


def _sin_subsegmento(_: str) -> AbstractContextManager[Any]:
    return nullcontext()


def construir_grafo(
    cliente: Any,
    verificador: Verificador,
    plantilla: Callable[[HechosParaLLM], SalidaLLM],
    costeador: Costeador,
    max_correcciones: int = 2,
    subsegmento: Subsegmento = _sin_subsegmento,
) -> Any:
    def _registrar(estado: EstadoGrafo, respuesta: RespuestaLLM, tipo: str) -> dict[str, Any]:
        registro = RegistroLlamada(
            intento=len(estado.llamadas) + 1,
            tipo=tipo,  # type: ignore[arg-type]
            respuesta=respuesta,
            costo_usd=costeador(respuesta),
        )
        return {"borrador": respuesta.salida, "llamadas": (*estado.llamadas, registro)}

    def redactar(estado: EstadoGrafo) -> dict[str, Any]:
        with subsegmento("grafo.redactar"):
            return _registrar(estado, cliente.redactar(PeticionRedaccion(hechos=estado.hechos)), "redactar")

    def corregir(estado: EstadoGrafo) -> dict[str, Any]:
        with subsegmento("grafo.corregir"):
            peticion = PeticionRedaccion(hechos=estado.hechos, borrador_previo=estado.borrador, errores=estado.errores)
            cambios = _registrar(estado, cliente.redactar(peticion), "corregir")
            cambios["correcciones"] = estado.correcciones + 1
            return cambios

    def verificar(estado: EstadoGrafo) -> dict[str, Any]:
        with subsegmento("grafo.verificar"):
            errores = verificador(estado.borrador)
            ultima = estado.llamadas[-1].respuesta
            if ultima.stop_reason != "end_turn":
                errores = (
                    ErrorVerificacion(
                        regla="salida_invalida", detalle=f"stopReason={ultima.stop_reason}", ubicacion="respuesta"
                    ),
                    *errores,
                )
            return {
                "errores": errores,
                "errores_por_intento": (*estado.errores_por_intento, errores),
                "origen": None if errores else "llm",
            }

    def plantilla_nodo(estado: EstadoGrafo) -> dict[str, Any]:
        with subsegmento("grafo.plantilla"):
            ultima = estado.llamadas[-1].respuesta if estado.llamadas else None
            if ultima is not None and ultima.guardrail.intervino and ultima.guardrail.origen == "entrada":
                motivo = "el guardrail bloqueó la entrada"
            else:
                reglas = sorted({e.regla for e in estado.errores})
                motivo = f"{len(estado.llamadas)} intentos; reglas: {', '.join(reglas)}"
            return {
                "borrador": plantilla(estado.hechos),
                "origen": "plantilla",
                "requiere_revision": True,
                "motivo_plantilla": motivo,
            }

    def tras_llm(estado: EstadoGrafo) -> str:
        g = estado.llamadas[-1].respuesta.guardrail
        return "plantilla" if g.intervino and g.origen == "entrada" else "verificar"

    def tras_verificar(estado: EstadoGrafo) -> str:
        if not estado.errores:
            return "fin"
        return "corregir" if estado.correcciones < max_correcciones else "plantilla"

    grafo = StateGraph(EstadoGrafo)
    grafo.add_node("redactar", redactar)  # type: ignore[call-overload]
    grafo.add_node("verificar", verificar)  # type: ignore[call-overload]
    grafo.add_node("corregir", corregir)  # type: ignore[call-overload]
    grafo.add_node("plantilla", plantilla_nodo)  # type: ignore[call-overload]
    grafo.add_edge(START, "redactar")
    grafo.add_conditional_edges("redactar", tras_llm, {"verificar": "verificar", "plantilla": "plantilla"})
    grafo.add_conditional_edges("corregir", tras_llm, {"verificar": "verificar", "plantilla": "plantilla"})
    grafo.add_conditional_edges(
        "verificar", tras_verificar, {"fin": END, "corregir": "corregir", "plantilla": "plantilla"}
    )
    grafo.add_edge("plantilla", END)
    return grafo.compile()


def ejecutar(grafo: Any, hechos: HechosParaLLM) -> EstadoGrafo:
    return EstadoGrafo.model_validate(grafo.invoke(EstadoGrafo(hechos=hechos)))


def diagrama_mermaid(grafo: Any) -> str:
    return str(grafo.get_graph().draw_mermaid())
