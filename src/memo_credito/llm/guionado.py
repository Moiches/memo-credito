"""Cliente guionado para tests: devuelve una secuencia programada de respuestas (o lanza excepciones)."""

from __future__ import annotations

from collections.abc import Sequence

from memo_credito.memo.modelos import (
    PeticionRedaccion,
    RespuestaLLM,
    ResultadoGuardrail,
    SalidaLLM,
    UsoTokens,
)

Paso = SalidaLLM | RespuestaLLM | Exception | str


class ClienteGuionado:
    modelo = "guionado"
    modo = "guionado"

    def __init__(self, guion: Sequence[Paso]) -> None:
        self._guion = list(guion)
        self.peticiones: list[PeticionRedaccion] = []

    def redactar(self, peticion: PeticionRedaccion) -> RespuestaLLM:
        self.peticiones.append(peticion)
        if not self._guion:
            raise AssertionError("el guion se agotó: el grafo llamó al modelo más veces de lo esperado")
        paso = self._guion.pop(0)
        if isinstance(paso, Exception):
            raise paso
        if isinstance(paso, RespuestaLLM):
            return paso
        if isinstance(paso, str):  # texto crudo no conforme
            return RespuestaLLM(
                salida=None,
                texto_crudo=paso,
                stop_reason="end_turn",
                modelo=self.modelo,
                uso=UsoTokens(entrada=100, salida=50),
            )
        return RespuestaLLM(
            salida=paso,
            texto_crudo=paso.model_dump_json(),
            stop_reason="end_turn",
            modelo=self.modelo,
            uso=UsoTokens(entrada=100, salida=50),
            guardrail=ResultadoGuardrail(),
        )


def bloqueo_guardrail(origen: str) -> RespuestaLLM:
    return RespuestaLLM(
        salida=None,
        texto_crudo="[GUARDRAIL_ENTRADA]" if origen == "entrada" else "[GUARDRAIL_SALIDA]",
        stop_reason="guardrail_intervened",
        modelo="guionado",
        uso=UsoTokens(entrada=100, salida=0 if origen == "entrada" else 40),
        guardrail=ResultadoGuardrail(intervino=True, origen=origen, politicas=("prueba",)),  # type: ignore[arg-type]
    )
