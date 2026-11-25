"""Stage por entorno: datos y seguridad, workflow y observabilidad, con etiquetas de costo y cdk-nag."""

from __future__ import annotations

from typing import Any

from aws_cdk import Stage, Tags, Validations
from cdk_nag import AwsSolutionsChecks
from constructs import Construct

from infra.config import Entorno
from infra.stacks.datos_seguridad import DatosSeguridadStack
from infra.stacks.observabilidad import ObservabilidadStack
from infra.stacks.workflow import WorkflowStack

ETIQUETAS = {"proyecto": "memo-credito", "centro-de-costo": "riesgos-credito", "propietario": "riesgos-credito"}


class MemoCreditoStage(Stage):
    def __init__(self, scope: Construct, id_: str, entorno: Entorno, **kwargs: Any) -> None:
        super().__init__(scope, id_, **kwargs)
        p = entorno.prefijo
        proteger = not entorno.infra.destruir_al_borrar
        self.datos = DatosSeguridadStack(
            self,
            "Datos",
            entorno=entorno,
            stack_name=f"{p}-datos",
            termination_protection=proteger,
            description="memo-credito: KMS, buckets, DynamoDB, SNS, políticas de personas y VPC",
        )
        self.flujo = WorkflowStack(
            self,
            "Flujo",
            entorno=entorno,
            datos=self.datos,
            stack_name=f"{p}-flujo",
            termination_protection=proteger,
            description="memo-credito: Lambdas, guardrail, perfil de inferencia, Step Functions y EventBridge",
        )
        self.observabilidad = ObservabilidadStack(
            self,
            "Observabilidad",
            entorno=entorno,
            datos=self.datos,
            flujo=self.flujo,
            stack_name=f"{p}-observabilidad",
            description="memo-credito: alarmas, dashboard y presupuesto",
        )
        for clave, valor in {**ETIQUETAS, "entorno": entorno.nombre}.items():
            Tags.of(self).add(clave, valor)
        Validations.of(self).add_plugins(AwsSolutionsChecks(self, verbose=True))
