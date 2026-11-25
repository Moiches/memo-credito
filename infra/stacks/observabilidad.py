"""Observabilidad: alarmas (operación, calidad del LLM, costo y revisión), dashboard y presupuesto mensual."""

from __future__ import annotations

from typing import Any

from aws_cdk import Duration, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from constructs import Construct

from infra.config import Entorno
from infra.stacks.datos_seguridad import DatosSeguridadStack
from infra.stacks.workflow import WorkflowStack

SERVICIO = {"service": "memo-credito"}


class ObservabilidadStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id_: str,
        entorno: Entorno,
        datos: DatosSeguridadStack,
        flujo: WorkflowStack,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, id_, **kwargs)
        p, costos = entorno.prefijo, entorno.infra.costos
        self.namespace = f"MemoCredito/{entorno.nombre}"
        accion = cw_actions.SnsAction(datos.alarmas)
        self.alarmas: list[cw.Alarm] = []

        def alarma(nombre: str, metrica: cw.IMetric, umbral: float, descripcion: str) -> None:
            a = cw.Alarm(
                self,
                f"Alarma{nombre.title().replace('-', '')}",
                alarm_name=f"{p}-{nombre}",
                metric=metrica,
                threshold=umbral,
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=descripcion,
            )
            a.add_alarm_action(accion)
            self.alarmas.append(a)

        cinco, hora = Duration.minutes(5), Duration.hours(1)
        # Operación
        alarma(
            "flujo-fallido",
            flujo.maquina.metric_failed(period=cinco),
            1,
            "Una ejecución terminó en Fallo: ver MarcarError en DynamoDB.",
        )
        alarma(
            "flujo-timeout", flujo.maquina.metric_timed_out(period=cinco), 1, "Una ejecución excedió el timeout global."
        )
        for fn in flujo.funciones:
            nombre = fn.node.id.removeprefix("Fn").lower()
            alarma(f"errores-{nombre}", fn.metric_errors(period=cinco), 1, f"Errores en la Lambda {nombre}.")
        throttles = cw.MathExpression(
            expression="+".join(f"t{i}" for i in range(len(flujo.funciones))),
            using_metrics={f"t{i}": fn.metric_throttles(period=cinco) for i, fn in enumerate(flujo.funciones)},
            label="Throttles Lambda",
            period=cinco,
        )
        alarma("throttles-lambda", throttles, 1, "Lambdas limitadas por concurrencia (cuenta con cuota baja).")
        alarma(
            "dlq-eventos",
            flujo.dlq_eventos.metric_approximate_number_of_messages_visible(period=cinco, statistic="Maximum"),
            1,
            "Eventos de S3 que no llegaron a ingesta o a aplicar_revision.",
        )
        # Calidad del LLM
        alarma(
            "memo-plantilla",
            self.metrica("MemoPlantilla", periodo=hora),
            1,
            "Se generó un memo con plantilla determinista (marcado 'requiere revisión').",
        )
        alarma(
            "guardrail", self.metrica("GuardrailIntervenciones", periodo=hora), 1, "El guardrail de Bedrock intervino."
        )
        alarma(
            "bedrock-throttles",
            self.metrica("BedrockThrottles", periodo=Duration.minutes(15)),
            5,
            "Reintentos por throttling.",
        )
        # Costo
        alarma(
            "costo-memo",
            self.metrica("CostoMemoUSD", "Maximum", cinco),
            float(costos.umbral_memo_usd),
            f"Un memo costó más de {costos.umbral_memo_usd} USD.",
        )
        alarma(
            "costo-hora",
            self.metrica("CostoMemoUSD", "Sum", hora),
            float(costos.umbral_hora_usd),
            f"El gasto de LLM de la última hora superó {costos.umbral_hora_usd} USD.",
        )
        # Revisión
        alarma(
            "revisiones-rechazadas",
            self.metrica("RevisionesRechazadas", periodo=hora),
            3,
            "Revisiones rechazadas (hash distinto, esquema inválido o fuera de estado).",
        )

        self._dashboard(p, flujo)
        budgets.CfnBudget(
            self,
            "Presupuesto",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{p}-mensual",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=float(costos.presupuesto_mes_usd), unit="USD"),
                cost_filters={"TagKeyValue": ["user:proyecto$memo-credito"]},
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type=tipo,
                        comparison_operator="GREATER_THAN",
                        threshold=umbral,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(subscription_type="SNS", address=datos.alarmas.topic_arn)
                    ],
                )
                for tipo, umbral in (("ACTUAL", 80), ("FORECASTED", 100))
            ],
        )

    def metrica(self, nombre: str, estadistica: str = "Sum", periodo: Duration | None = None) -> cw.Metric:
        return cw.Metric(
            namespace=self.namespace,
            metric_name=nombre,
            dimensions_map=SERVICIO,
            statistic=estadistica,
            period=periodo or Duration.minutes(5),
        )

    def _dashboard(self, p: str, flujo: WorkflowStack) -> None:
        m, h = self.metrica, Duration.hours(1)
        tablero = cw.Dashboard(self, "Tablero", dashboard_name=f"{p}-operacion", default_interval=Duration.days(7))
        tablero.add_widgets(
            cw.TextWidget(
                markdown=f"# {p}\nFlujo, calidad del LLM, costo y revisión humana. "
                "Sin PII: solo conteos, hashes y códigos.",
                width=24,
                height=2,
            )
        )
        tablero.add_widgets(
            cw.GraphWidget(
                title="Ejecuciones",
                width=8,
                left=[
                    flujo.maquina.metric_started(period=h),
                    flujo.maquina.metric_succeeded(period=h),
                    flujo.maquina.metric_failed(period=h),
                ],
            ),
            cw.GraphWidget(
                title="Memos (total vs plantilla)",
                width=8,
                left=[m("MemosGenerados", periodo=h), m("MemoPlantilla", periodo=h)],
            ),
            cw.GraphWidget(
                title="Documentos",
                width=8,
                left=[
                    m("DocumentosExtraidos", periodo=h),
                    m("DocumentosRechazados", periodo=h),
                    m("ConciliacionFallida", periodo=h),
                ],
            ),
        )
        tablero.add_widgets(
            cw.GraphWidget(
                title="Costo LLM (USD)", width=8, left=[m("CostoMemoUSD", "Sum", h), m("CostoMemoUSD", "Maximum", h)]
            ),
            cw.GraphWidget(title="Tokens", width=8, left=[m("TokensEntrada", periodo=h), m("TokensSalida", periodo=h)]),
            cw.GraphWidget(
                title="Llamadas, verificador y guardrail",
                width=8,
                left=[
                    m("LlamadasLLM", periodo=h),
                    m("VerificadorFallas", periodo=h),
                    m("GuardrailIntervenciones", periodo=h),
                    m("BedrockThrottles", periodo=h),
                ],
            ),
        )
        tablero.add_widgets(
            cw.GraphWidget(
                title="Aceptación sin cambios (promedio)", width=8, left=[m("TasaAceptacionSinCambios", "Average", h)]
            ),
            cw.GraphWidget(
                title="Distancia de edición (promedio)", width=8, left=[m("DistanciaEdicion", "Average", h)]
            ),
            cw.GraphWidget(
                title="Minutos de revisión (p50 / p90)",
                width=8,
                left=[m("TiempoRevisionMin", "p50", h), m("TiempoRevisionMin", "p90", h)],
            ),
        )
        tablero.add_widgets(
            cw.GraphWidget(
                title="Duración Lambda p95 (ms)",
                width=12,
                left=[fn.metric_duration(statistic="p95", period=h) for fn in flujo.funciones],
            ),
            cw.AlarmStatusWidget(title="Alarmas", width=12, alarms=self.alarmas),
        )
