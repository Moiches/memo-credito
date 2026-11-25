from __future__ import annotations

from decimal import Decimal

from pydantic import SecretStr


def test_sol0005_julio_dos_paginas_ultimo_movimiento_en_pagina_2(expedientes):
    julio = expedientes["SOL-0005"].estados_cuenta[0]
    assert julio.periodo.clave == "2026-07" and julio.paginas == 2
    assert len(julio.movimientos) == 36
    ultimo = julio.movimientos[-1]
    assert ultimo.procedencia.pagina == 2 and ultimo.procedencia.renglon == 36
    assert ultimo.concepto == "PAGO FLETES Y MANIOBRAS"
    assert all(
        "Fecha" not in m.concepto and "Concepto" not in m.concepto for m in julio.movimientos
    )  # encabezado repetido (H1)


def test_sol0006_concepto_multilinea_con_monto_en_primera_linea(expedientes, casos):
    agosto = next(e for e in expedientes["SOL-0006"].estados_cuenta if e.periodo.clave == "2026-08")
    inyectado = [m for m in agosto.movimientos if m.lineas_concepto > 1]
    assert len(inyectado) == 1
    m = inyectado[0]
    esperado = casos["SOL-0006"]["inyeccion"]
    assert m.lineas_concepto == esperado["lineas"]
    assert m.abono == Decimal(esperado["abono"]) and m.cargo is None
    assert m.fecha.isoformat() == esperado["fecha"]
    assert esperado["frase"] in m.concepto
    assert m.marca_instruccion is True
    assert m.procedencia.pagina == 1 and m.procedencia.renglon == 16


def test_una_sola_marca_de_instruccion_en_todo_el_set(expedientes):
    movimientos = [m for exp in expedientes.values() for e in exp.estados_cuenta for m in e.movimientos]
    assert len(movimientos) == 689
    assert sum(m.marca_instruccion for m in movimientos) == 1  # cero falsos positivos


def test_pie_de_pagina_ignorado(expedientes):
    conceptos = [m.concepto for exp in expedientes.values() for e in exp.estados_cuenta for m in e.movimientos]
    assert not any("DOCUMENTO FICTICIO" in c for c in conceptos)


def test_sol0007_campos_no_esperados_solo_por_nombre_y_pii_oculta(expedientes, casos):
    sol = expedientes["SOL-0007"].solicitud
    assert sol.campos_no_esperados == ("representante_legal",)
    volcado = repr(sol) + sol.model_dump_json() + repr(expedientes["SOL-0007"])
    for canario in casos["SOL-0007"]["pii_canarios"]:
        assert canario not in volcado
    assert "Gabriela" not in volcado  # nombre del contacto
    assert isinstance(sol.contacto.correo, SecretStr)


def test_correos_no_ascii_se_aceptan(expedientes):
    correo = expedientes["SOL-0004"].solicitud.contacto.correo
    assert correo is not None and "í" in correo.get_secret_value()


def test_constancia_y_eeff(expedientes):
    exp = expedientes["SOL-0001"]
    c = exp.constancia
    assert c.rfc.valor == "ASP170418QK2" and c.estatus.valor == "ACTIVO"
    assert c.fecha_emision.valor.isoformat() == "2026-10-05"
    assert c.inicio_operaciones.valor.isoformat() == "2017-04-18"
    assert c.actividad_economica.valor == "Comercio al por mayor de abarrotes"
    assert "FRESNOS" not in repr(c)  # domicilio oculto
    ef = exp.estados_financieros
    assert ef.ejercicio == 2025 and ef.dictaminados is False and len(ef.partidas) == 17
    assert ef.partidas["utilidad_neta"].valor == Decimal("1197000.00")
    assert ef.partidas["total_pasivo"].procedencia.renglon is not None


def test_procedencia_y_sha256(expedientes):
    e = expedientes["SOL-0001"].estados_cuenta[0]
    assert len(e.sha256) == 64
    assert e.total_abonos.procedencia.documento == "estado_cuenta_2026-07.pdf"
    assert e.total_abonos.texto_original == "$1,555,566.24"
