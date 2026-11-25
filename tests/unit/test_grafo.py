"""Grafo LangGraph: un test por nodo y uno por ruta, sin red (ClienteGuionado)."""

from __future__ import annotations

import os

import pytest
from pytest_socket import disable_socket, enable_socket

from memo_credito.dominio.errores import LLMNoDisponible
from memo_credito.llm.guionado import ClienteGuionado, bloqueo_guardrail
from memo_credito.llm.simulado import ClienteSimulado
from memo_credito.memo.ensamblador import Metadatos
from memo_credito.memo.grafo import construir_grafo, diagrama_mermaid
from memo_credito.memo.hechos import construir_hechos
from memo_credito.memo.modelos import SalidaLLM, Vineta
from memo_credito.memo.plantilla import plantilla
from memo_credito.memo.servicio import redactar_memo
from tests.conftest import RAIZ

META = Metadatos(fecha="2026-11-25", ejecucion="test", prompt_id="p", modelo="guionado", modo="guionado", commit="t")
INVALIDA = SalidaLLM(resumen="La capacidad es 1.85.", observaciones=(Vineta(texto="x"),), puntos_comite=())


@pytest.fixture(scope="module")
def contexto(expedientes, analisis, config):
    folio = "SOL-0005"  # tiene dos alertas (R01, R03)
    h = construir_hechos(expedientes[folio], analisis[folio], config)
    return expedientes[folio], analisis[folio], h, config


def _correr(contexto, guion):
    exp, a, h, cfg = contexto
    cliente = ClienteGuionado(guion)
    res = redactar_memo(exp, a, h, cliente, cfg, META)
    return res, cliente


def test_ruta_aprueba_al_primer_intento(contexto):
    res, cliente = _correr(contexto, [plantilla(contexto[2])])
    assert len(cliente.peticiones) == 1 and res.estado.origen == "llm" and not res.errores_finales


def test_ruta_aprueba_tras_una_correccion(contexto):
    res, cliente = _correr(contexto, [INVALIDA, plantilla(contexto[2])])
    assert len(cliente.peticiones) == 2 and res.estado.origen == "llm"
    correccion = cliente.peticiones[1]
    assert correccion.errores and correccion.borrador_previo == INVALIDA  # nodo corregir recibe errores y borrador
    assert res.estado.correcciones == 1


def test_ruta_falla_dos_veces_sale_plantilla(contexto):
    res, cliente = _correr(contexto, [INVALIDA, INVALIDA, INVALIDA])
    assert len(cliente.peticiones) == 3  # nunca más de 1 + 2 correcciones
    assert res.estado.origen == "plantilla" and res.estado.requiere_revision
    assert "Requiere revisión" in res.memo and not res.errores_finales


def test_ruta_guardrail_bloquea_entrada(contexto):
    res, cliente = _correr(contexto, [bloqueo_guardrail("entrada")])
    assert len(cliente.peticiones) == 1 and res.estado.origen == "plantilla"
    assert res.estado.motivo_plantilla == "el guardrail bloqueó la entrada"


def test_ruta_guardrail_bloquea_salida_y_corrige(contexto):
    res, cliente = _correr(contexto, [bloqueo_guardrail("salida"), plantilla(contexto[2])])
    assert len(cliente.peticiones) == 2 and res.estado.origen == "llm"
    assert res.estado.errores_por_intento[0][0].regla == "salida_invalida"


def test_modelo_no_disponible_cae_a_plantilla(contexto):
    res, _ = _correr(contexto, [LLMNoDisponible("ThrottlingException")])
    assert res.estado.origen == "plantilla" and "LLM_NO_DISPONIBLE" in (res.estado.motivo_plantilla or "")


def test_nodos_registran_costo_tokens_y_errores_por_intento(contexto):
    res, _ = _correr(contexto, [INVALIDA, plantilla(contexto[2])])
    assert [ll.tipo for ll in res.estado.llamadas] == ["redactar", "corregir"]
    assert res.tokens == (200, 100)
    assert len(res.estado.errores_por_intento) == 2 and res.estado.errores_por_intento[1] == ()


def test_nodo_plantilla_determinista(contexto):
    assert plantilla(contexto[2]) == plantilla(contexto[2])


def test_sin_red_ni_telemetria_de_terceros(contexto):
    assert os.environ.get("LANGSMITH_TRACING") == "false"
    disable_socket()
    try:
        res, _ = _correr(contexto, [plantilla(contexto[2])])
    finally:
        enable_socket()
    assert res.estado.origen == "llm"


def test_diagrama_generado_desde_el_codigo_esta_actualizado(config):
    grafo = construir_grafo(ClienteSimulado(), lambda _s: (), plantilla, lambda _r: 0, config.llm.max_correcciones)
    assert (RAIZ / "docs" / "grafo_redaccion.mmd").read_text(encoding="utf-8") == diagrama_mermaid(grafo)
