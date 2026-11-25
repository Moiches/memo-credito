"""Reconocimiento (supresión) de reglas de cdk-nag 3, uno por uno y con justificación; nunca globales.

cdk-nag 3 usa la API nativa de CDK: Validations.of(construct).acknowledge(...). Reconocer el id base de una regla
(p. ej. AwsSolutions-IAM5) reconoce todos sus hallazgos en ese construct y sus hijos.
"""

from __future__ import annotations

from collections.abc import Sequence

from aws_cdk import Acknowledgment, Validations
from constructs import IConstruct


def suprimir(construct: IConstruct, reglas: Sequence[tuple[str, str]]) -> None:
    Validations.of(construct).acknowledge(*[Acknowledgment(id=regla, reason=razon) for regla, razon in reglas])
