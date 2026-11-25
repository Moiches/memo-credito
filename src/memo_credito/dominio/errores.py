"""Excepciones del dominio. Sus mensajes nunca incluyen contenido de documentos ni PII."""


class ErrorMemo(Exception):
    """Base de los errores del sistema; `codigo` es estable y se registra en DynamoDB y métricas."""

    codigo = "ERROR_INTERNO"

    def __init__(self, detalle: str = "") -> None:
        super().__init__(detalle or self.codigo)
        self.detalle = detalle


class DocumentoRechazado(ErrorMemo):
    codigo = "DOCUMENTO_RECHAZADO"


class FormatoNoSoportado(ErrorMemo):
    codigo = "FORMATO_NO_SOPORTADO"


class ExpedienteIncompleto(ErrorMemo):
    codigo = "EXPEDIENTE_INCOMPLETO"


class MontoInvalido(ErrorMemo, ValueError):
    codigo = "MONTO_INVALIDO"


class LLMNoDisponible(ErrorMemo):
    """Error transitorio de Bedrock después de agotar reintentos: el flujo cae a la plantilla."""

    codigo = "LLM_NO_DISPONIBLE"


class LLMErrorConfiguracion(ErrorMemo):
    """Error no reintentable (permisos, validación): también cae a la plantilla y alerta a operación."""

    codigo = "LLM_ERROR_CONFIGURACION"
