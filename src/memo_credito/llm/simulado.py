"""LLM simulado: implementación determinista de la misma interfaz (la plantilla del punto 1a).

Permite correr el sistema completo, la CI y las pruebas de carga sin modelo y sin costo.
"""

from __future__ import annotations

from memo_credito.memo.modelos import PeticionRedaccion, RespuestaLLM
from memo_credito.memo.plantilla import plantilla


class ClienteSimulado:
    modelo = "simulado"
    modo = "simulado"

    def redactar(self, peticion: PeticionRedaccion) -> RespuestaLLM:
        salida = plantilla(peticion.hechos)
        return RespuestaLLM(
            salida=salida,
            texto_crudo=salida.model_dump_json(),
            stop_reason="end_turn",
            modelo=self.modelo,
            peticion={"modo": "simulado", "hechos": peticion.hechos.model_dump(mode="json")},
        )
