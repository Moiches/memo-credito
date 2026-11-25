"""Paquete de auditoría: write-once, cadena de hashes, sin PII y reproducibilidad exacta del análisis."""

from __future__ import annotations

import json

import pytest

from memo_credito.auditoria.canonico import json_canonico
from memo_credito.auditoria.paquete import (
    AlmacenLocal,
    escribir_manifiesto,
    escribir_parte,
    proyectar_extraccion,
    rehidratar,
    verificar_paquete,
)
from memo_credito.calculos.analisis import analizar
from memo_credito.extraccion.expediente import armar_expediente, extraer_archivos, leer_carpeta
from memo_credito.llm.simulado import ClienteSimulado
from memo_credito.pipeline import procesar_carpeta
from tests.conftest import DATOS, FOLIOS


@pytest.mark.parametrize("folio", FOLIOS)
def test_reproducibilidad_desde_la_extraccion_guardada(folio, config):
    exp, resultados = extraer_archivos(leer_carpeta(DATOS / folio), config)
    original = json_canonico(analizar(exp, config))
    guardado = json.loads(json_canonico([proyectar_extraccion(r) for r in resultados]))  # lo que queda en S3
    recalculado = json_canonico(analizar(armar_expediente(rehidratar(guardado)), config))
    assert recalculado == original


def test_proyeccion_sin_pii_ni_texto_inyectado(config, casos):
    for folio in ("SOL-0006", "SOL-0007", "SOL-0001"):
        _, resultados = extraer_archivos(leer_carpeta(DATOS / folio), config)
        texto = json_canonico([proyectar_extraccion(r) for r in resultados])
        for canario in casos["SOL-0007"]["pii_canarios"]:
            assert canario not in texto
        assert "IGNORA" not in texto and "FRESNOS" not in texto  # concepto inyectado y domicilio
        assert "mariana" not in texto.lower()


def test_paquete_write_once_y_cadena(tmp_path, config):
    res = procesar_carpeta(
        DATOS / "SOL-0001",
        config,
        ClienteSimulado(),
        salida_base=tmp_path,
        fecha="2026-11-25",
        ejecucion="e1",
        commit="t",
    )
    almacen = AlmacenLocal(res.ruta_auditoria)
    assert verificar_paquete(almacen, "e1") == []
    with pytest.raises(FileExistsError):
        escribir_parte(almacen, "e1", "02_analisis.json", {"alterado": True})
    ruta = res.ruta_auditoria / "e1" / "03_redaccion.json"
    ruta.write_bytes(ruta.read_bytes().replace(b"Empresa", b"Empresx"))
    assert verificar_paquete(almacen, "e1")  # la alteración se detecta


def test_manifiesto_encadena_las_partes(tmp_path):
    almacen = AlmacenLocal(tmp_path)
    escribir_parte(almacen, "x", "00_entrada.json", {"a": 1})
    escribir_parte(almacen, "x", "02_analisis.json", {"b": 2})
    m = escribir_manifiesto(almacen, "x")
    assert [p["nombre"] for p in m["partes"]] == ["00_entrada.json", "02_analisis.json"]
    assert m["hash_final"] == m["partes"][-1]["cadena"]
