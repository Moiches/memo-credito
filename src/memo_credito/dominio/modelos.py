"""Modelos inmutables del dominio (Pydantic v2).

- Todo monto es Decimal (nunca float).
- Cada valor extraído guarda su procedencia: documento, sha256, página y renglón.
- Los valores se guardan tal como vienen: el RFC se valida pero no se corrige.
- La PII usa SecretStr con repr=False: no aparece en repr, logs ni model_dump_json.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr

from memo_credito.dominio.alertas import Categoria, CodigoAlerta, Severidad

T = TypeVar("T")


def _a_lo_mas_dos_decimales(valor: Decimal) -> Decimal:
    if valor != valor.quantize(Decimal("0.01")):
        raise ValueError("el monto debe tener a lo más 2 decimales")
    return valor


Dinero = Annotated[Decimal, AfterValidator(_a_lo_mas_dos_decimales)]
PiiStr = Annotated[SecretStr, Field(repr=False)]
PiiOpcional = Annotated[SecretStr | None, Field(default=None, repr=False)]
TextoNoConfiable = Annotated[str, Field(repr=False, max_length=4000)]
TipoDocumento = Literal["estado_cuenta", "constancia", "estados_financieros", "solicitud"]


class Inmutable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Procedencia(Inmutable):
    documento: str
    sha256: str
    pagina: int | None = None
    renglon: int | None = None
    campo: str


class ValorExtraido(Inmutable, Generic[T]):
    valor: T
    texto_original: str = Field(repr=False)
    procedencia: Procedencia


class ValorPii(Inmutable):
    valor: PiiStr
    procedencia: Procedencia


class Periodo(Inmutable):
    anio: int
    mes: int = Field(ge=1, le=12)

    @property
    def clave(self) -> str:
        return f"{self.anio:04d}-{self.mes:02d}"

    @property
    def orden(self) -> int:
        return self.anio * 12 + self.mes


class HallazgoSeguridad(Inmutable):
    """Hallazgo sin texto crudo: solo ubicación y un detalle estructural."""

    tipo: Literal["instruccion", "texto_oculto"]
    procedencia: Procedencia
    detalle: str


class RefDocumento(Inmutable):
    nombre: str
    sha256: str
    bytes: int
    tipo: TipoDocumento
    paginas: int | None = None


class Movimiento(Inmutable):
    fecha: date
    concepto: TextoNoConfiable
    cargo: Dinero | None
    abono: Dinero | None
    saldo: Dinero | None
    lineas_concepto: int
    clasificacion: Literal["efectivo", "tpv", "spei", "otro"]
    marca_instruccion: bool
    procedencia: Procedencia


class EstadoCuenta(Inmutable):
    documento: str
    sha256: str
    formato: str
    banco: str
    titular: ValorExtraido[str]
    rfc: ValorExtraido[str]
    cuenta: ValorPii
    periodo: Periodo
    periodo_nombre_archivo: Periodo | None
    saldo_inicial: ValorExtraido[Decimal]
    total_abonos: ValorExtraido[Decimal]
    total_cargos: ValorExtraido[Decimal]
    saldo_final: ValorExtraido[Decimal]
    movimientos: tuple[Movimiento, ...]
    paginas: int
    texto_oculto: tuple[HallazgoSeguridad, ...] = ()


class ConstanciaFiscal(Inmutable):
    documento: str
    sha256: str
    rfc: ValorExtraido[str]
    razon_social: ValorExtraido[str]
    regimen_capital: ValorExtraido[str]
    regimen_fiscal: ValorExtraido[str]
    inicio_operaciones: ValorExtraido[date]
    estatus: ValorExtraido[str]
    fecha_emision: ValorExtraido[date]
    actividad_economica: ValorExtraido[str]
    domicilio_fiscal: ValorPii
    paginas: int
    hallazgos: tuple[HallazgoSeguridad, ...] = ()


PARTIDAS_BALANCE = (
    "activo_circulante",
    "activo_fijo_neto",
    "total_activo",
    "pasivo_corto_plazo",
    "pasivo_largo_plazo",
    "total_pasivo",
    "capital_contable",
    "total_pasivo_capital",
)
PARTIDAS_RESULTADOS = (
    "ventas_netas",
    "costo_ventas",
    "utilidad_bruta",
    "gastos_operacion",
    "utilidad_operacion",
    "gastos_financieros",
    "utilidad_antes_impuestos",
    "isr",
    "utilidad_neta",
)


class EstadosFinancieros(Inmutable):
    documento: str
    sha256: str
    razon_social: ValorExtraido[str]
    rfc: ValorExtraido[str]
    fecha_cierre: date
    ejercicio: int
    partidas: dict[str, ValorExtraido[Decimal]]
    dictaminados: bool
    leyenda: str
    paginas: int
    hallazgos: tuple[HallazgoSeguridad, ...] = ()


class Solicitante(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    razon_social: str = Field(max_length=300)
    rfc: str = Field(max_length=20)


class Credito(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    monto_solicitado: Decimal
    plazo_meses: int
    moneda: str = Field(max_length=10)
    destino: str = Field(max_length=500)


class Contacto(BaseModel):
    """PII opaca: no se valida el correo con un validador estricto (hay correos no ASCII, hallazgo H5)."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    nombre: PiiOpcional
    puesto: str | None = None
    correo: PiiOpcional
    telefono_oficina: PiiOpcional


class Solicitud(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    documento: str = "solicitud.json"
    sha256: str = ""
    folio: str = Field(pattern=r"^[A-Z]{2,4}-[A-Z0-9-]{1,40}$")
    fecha_solicitud: date
    solicitante: Solicitante
    credito: Credito
    contacto: Contacto | None = None
    documentos: tuple[str, ...] = ()
    campos_no_esperados: tuple[str, ...] = ()
    hallazgos: tuple[HallazgoSeguridad, ...] = ()


class DocumentoRechazadoInfo(Inmutable):
    nombre: str
    sha256: str
    motivo: str


class Expediente(Inmutable):
    solicitud: Solicitud
    estados_cuenta: tuple[EstadoCuenta, ...]
    constancia: ConstanciaFiscal | None
    estados_financieros: EstadosFinancieros | None
    documentos: tuple[RefDocumento, ...]
    rechazados: tuple[DocumentoRechazadoInfo, ...] = ()


class Conciliacion(Inmutable):
    documento: str
    periodo: str
    suma_abonos_ok: bool
    suma_cargos_ok: bool
    saldo_final_ok: bool
    renglones_ok: bool
    fechas_ok: bool
    diferencias: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.suma_abonos_ok and self.suma_cargos_ok and self.saldo_final_ok and self.renglones_ok


class Alerta(Inmutable):
    codigo: CodigoAlerta
    categoria: Categoria
    severidad: Severidad
    titulo: str
    mensaje: str
    evidencia: tuple[Procedencia, ...] = ()
    cifras: tuple[str, ...] = ()


class Validacion(Inmutable):
    id: str
    resultado: Literal["ok", "falla"]
    texto_memo: str


class Indicador(Inmutable):
    id: str
    nombre: str
    estado: Literal["calculado", "no_calculable"]
    valor: Decimal | None
    valor_texto: str
    motivo: str | None
    formula: str
    entradas: dict[str, Decimal]
    fuente_memo: str


class DepositoAtipico(Inmutable):
    fecha: date
    monto: Decimal
    procedencia: Procedencia


class HechosDerivados(Inmutable):
    antiguedad_anios: int | None
    ventas_netas: Decimal | None
    utilidad_neta: Decimal | None
    monto_pct_ventas: Decimal | None
    mes_flujo_mas_alto: Periodo | None
    mes_flujo_mas_bajo: Periodo | None
    flujos_positivos: bool | None
    pct_abonos_efectivo: Decimal | None
    depositos_atipicos: tuple[DepositoAtipico, ...] | None
    eeff_dictaminados: bool | None
    constancia_estatus: str | None
    constancia_dias: int | None


class Analisis(Inmutable):
    folio: str
    periodos_esperados: tuple[Periodo, ...]
    periodos_presentes: tuple[str, ...]
    conciliaciones: tuple[Conciliacion, ...]
    validaciones: tuple[Validacion, ...]
    alertas: tuple[Alerta, ...]
    indicadores: tuple[Indicador, ...]
    derivados: HechosDerivados

    def indicador(self, id_: str) -> Indicador:
        for ind in self.indicadores:
            if ind.id == id_:
                return ind
        raise KeyError(id_)

    @property
    def codigos_alerta(self) -> tuple[str, ...]:
        return tuple(a.codigo for a in self.alertas)
