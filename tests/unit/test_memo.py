"""Contrato de hechos, plantilla, ensamblador (golden SOL-0001) y verificador regla por regla."""

from __future__ import annotations

import json

import pytest

from memo_credito.memo.ensamblador import Metadatos, ensamblar
from memo_credito.memo.hechos import construir_hechos
from memo_credito.memo.modelos import SalidaLLM, Vineta
from memo_credito.memo.plantilla import plantilla
from memo_credito.memo.verificador import verificar
from tests.conftest import DATOS, FOLIOS, RAIZ

META = Metadatos(
    fecha="2026-11-25",
    ejecucion="test",
    prompt_id="redaccion_v1@test",
    modelo="simulado",
    modo="simulado",
    commit="test",
)


@pytest.fixture(scope="module")
def hechos(expedientes, analisis, config):
    return {f: construir_hechos(expedientes[f], analisis[f], config) for f in FOLIOS}


def _memo(folio, salida, expedientes, analisis, hechos):
    return ensamblar(expedientes[folio], analisis[folio], hechos[folio], salida, META, "llm")


def test_hechos_sin_pii_ni_texto_crudo(hechos, casos, config, expedientes):
    volcado = json.dumps({f: h.model_dump(mode="json") for f, h in hechos.items()}, ensure_ascii=False)
    for canario in casos["SOL-0007"]["pii_canarios"]:
        assert canario not in volcado
    assert casos["SOL-0006"]["inyeccion"]["frase"] not in volcado
    for f, h in hechos.items():
        assert len(h.model_dump_json().encode()) <= config.limites.hechos_max_bytes, f
        rfcs = {expedientes[f].solicitud.solicitante.rfc, expedientes[f].estados_financieros.rfc.valor}
        assert not any(r in h.model_dump_json() for r in rfcs), f  # el valor del RFC nunca va al LLM


def test_hechos_no_calculable_sin_cifra(hechos):
    h = hechos["SOL-0004"]
    assert "flujo_promedio" not in h.cifras and "capacidad_pago" not in h.cifras
    assert h.indicadores["flujo_promedio"].estado == "no_calculable"
    assert h.indicadores["flujo_promedio"].motivo


@pytest.mark.parametrize("folio", FOLIOS)
def test_plantilla_es_determinista_y_pasa_el_verificador(folio, hechos, expedientes, analisis):
    s1, s2 = plantilla(hechos[folio]), plantilla(hechos[folio])
    assert s1.model_dump_json() == s2.model_dump_json()
    assert verificar(s1, _memo(folio, s1, expedientes, analisis, hechos), hechos[folio]) == []


def test_golden_sol0001_tabla_y_validaciones_identicas_al_ejemplo(hechos, expedientes, analisis):
    memo = _memo("SOL-0001", plantilla(hechos["SOL-0001"]), expedientes, analisis, hechos)
    ejemplo = (DATOS.parent / "memo_ejemplo_SOL-0001.md").read_text(encoding="utf-8").splitlines()
    filas_tabla = [linea for linea in ejemplo if linea.startswith("| ")]
    validaciones = ejemplo[ejemplo.index("## Validaciones") + 2 : ejemplo.index("## Validaciones") + 5]
    lineas = memo.splitlines()
    for linea in filas_tabla + validaciones:
        assert linea in lineas, linea
    assert ejemplo[0] == lineas[0]  # título
    assert "*La decisión corresponde al comité de crédito.*" in lineas
    assert lineas.index("## Resumen") < lineas.index("## Indicadores") < lineas.index("## Validaciones")
    assert "> Borrador generado por el sistema" in memo


def _salida(resumen="Empresa con {{antiguedad_anios}} años de operación.", obs=None, puntos=None):
    obs = obs or [
        Vineta(texto="Los estados financieros son internos y no están dictaminados."),
        Vineta(texto="El flujo de {{mes_flujo_mas_alto}} es el más alto del periodo."),
    ]
    puntos = puntos or [Vineta(texto="Valorar si se requieren estados financieros dictaminados dado el monto.")]
    return SalidaLLM(resumen=resumen, observaciones=tuple(obs), puntos_comite=tuple(puntos))


def _reglas(folio, salida, expedientes, analisis, hechos, canarios=()):
    memo = _memo(folio, salida, expedientes, analisis, hechos)
    return [e.regla for e in verificar(salida, memo, hechos[folio], canarios)], verificar(
        salida, memo, hechos[folio], canarios
    )


@pytest.mark.parametrize(
    ("resumen", "regla"),
    [
        ("La capacidad de pago es de 1.85 veces.", "cifra_literal"),
        ("Se revisaron tres meses de estados de cuenta.", "cifra_literal"),
        ("La capacidad es {{capacidad_inventada}}.", "cifra_desconocida"),
        ("La capacidad es {{1.85}}.", "marcador_invalido"),
        ("Se recomienda aprobar el crédito solicitado.", "lenguaje_recomendacion"),
        ("La solicitud es viable para el comité.", "lenguaje_recomendacion"),
        ("Ver detalle en [enlace](https://ejemplo.com).", "formato_inseguro"),
        ("El representante tiene CURP MOCS850723MJCNRF09.", "pii"),
        ("Contacto al 55 5374 4329.", "pii"),
    ],
)
def test_verificador_detecta_cada_regla(resumen, regla, expedientes, analisis, hechos):
    reglas, errores = _reglas("SOL-0001", _salida(resumen=resumen), expedientes, analisis, hechos)
    assert regla in reglas
    for e in errores:  # nunca reinyecta el valor detectado
        assert "MOCS850723" not in e.detalle and "5374" not in e.detalle and "1.85" not in e.detalle


def test_verificador_alertas_omitidas_e_inexistentes(expedientes, analisis, hechos):
    reglas, _ = _reglas("SOL-0003", _salida(), expedientes, analisis, hechos)
    assert "alerta_omitida" in reglas
    con_inexistente = _salida(puntos=[Vineta(texto="Revisar el RFC.", alertas_referidas=("A01", "R04"))])
    reglas, _ = _reglas("SOL-0003", con_inexistente, expedientes, analisis, hechos)
    assert "alerta_inexistente" in reglas and "alerta_omitida" not in reglas


def test_verificador_canarios_y_estructura(expedientes, analisis, hechos):
    con_nombre = _salida(resumen="La empresa tiene como contacto a Mariana Ortega.")
    reglas, _ = _reglas("SOL-0001", con_nombre, expedientes, analisis, hechos, canarios=("mariana ortega",))
    assert "pii" in reglas
    corta = _salida(obs=[Vineta(texto="Única observación.")])
    reglas, _ = _reglas("SOL-0001", corta, expedientes, analisis, hechos)
    assert "estructura" in reglas


def test_salida_invalida(hechos):
    assert [e.regla for e in verificar(None, None, hechos["SOL-0001"])] == ["salida_invalida"]


def test_prompt_y_esquema_versionados():
    prompt = (RAIZ / "prompts" / "redaccion_v1.md").read_text(encoding="utf-8")
    assert "{{id}}" in prompt and "La decisión corresponde al comité" in prompt
    esquema = json.loads((RAIZ / "prompts" / "salida_v1.schema.json").read_text(encoding="utf-8"))
    assert esquema["additionalProperties"] is False and "$ref" not in json.dumps(esquema)
