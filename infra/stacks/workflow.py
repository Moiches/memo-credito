"""Workflow: Lambdas (un rol por función), guardrail, perfil de inferencia, Step Functions y reglas de EventBridge."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from aws_cdk import Aws, CfnTag, Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_destinations as destinos
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from infra.config import Entorno, directorio_asset_lambda
from infra.nag import suprimir
from infra.stacks.datos_seguridad import DatosSeguridadStack

REGIONES_PERFIL_US = ("us-east-1", "us-east-2", "us-west-2")
CURP = r"\b[A-Z][AEIOUX][A-Z]{2}\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[HM][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[A-Z\d]\d\b"
RFC = r"\b[A-ZÑ&]{3,4}\d{6}[A-Z\d]{3}\b"
ERRORES_LAMBDA = [
    "Lambda.ServiceException",
    "Lambda.AWSLambdaException",
    "Lambda.SdkClientException",
    "Lambda.TooManyRequestsException",
    "Lambda.Unknown",
]


class WorkflowStack(Stack):
    def __init__(self, scope: Construct, id_: str, entorno: Entorno, datos: DatosSeguridadStack, **kwargs: Any) -> None:
        super().__init__(scope, id_, **kwargs)
        self.entorno = entorno
        self.datos = datos
        cfg, app, p = entorno.infra, entorno.app, entorno.prefijo
        self.borrado = RemovalPolicy.DESTROY if cfg.destruir_al_borrar else RemovalPolicy.RETAIN
        self.codigo = lambda_.Code.from_asset(directorio_asset_lambda())
        self.grupo_seguridad: ec2.SecurityGroup | None = None
        if datos.vpc is not None:
            self.grupo_seguridad = ec2.SecurityGroup(
                self,
                "SgLambdas",
                vpc=datos.vpc,
                allow_all_outbound=False,
                description="Lambdas de memo-credito: solo HTTPS hacia VPC endpoints",
            )
            # Subredes aisladas (sin NAT ni IGW): 443 solo alcanza los endpoints de interfaz y de gateway (S3, DynamoDB).
            self.grupo_seguridad.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS hacia VPC endpoints")

        # --- Guardrail de Bedrock (Standard tier, perfil us.) --------------------------------------------
        guardrail_props: dict[str, Any] = {
            "content": [{"type": "PROMPT_ATTACK", "input": "HIGH", "output": "NONE"}],
            "topic": "recomendacion_credito",
            "pii": {
                "EMAIL": ("NONE", "BLOCK"),
                "PHONE": ("NONE", "BLOCK"),
                "ADDRESS": ("NONE", "BLOCK"),
                "NAME": ("NONE", "NONE"),
            },
            "regex": {"CURP": CURP, "RFC": RFC},
        }
        huella_guardrail = hashlib.sha256(json.dumps(guardrail_props, sort_keys=True).encode()).hexdigest()[:12]
        perfil_guardrail = (
            f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:guardrail-profile/us.guardrail.v1:0"
        )
        self.guardrail = bedrock.CfnGuardrail(
            self,
            "Guardrail",
            name=f"{p}-redaccion",
            description="Ataques de prompt, PII (regex CURP/RFC) y tema denegado: recomendación de crédito",
            blocked_input_messaging="[GUARDRAIL_ENTRADA]",
            blocked_outputs_messaging="[GUARDRAIL_SALIDA]",
            cross_region_config=bedrock.CfnGuardrail.GuardrailCrossRegionConfigProperty(
                guardrail_profile_arn=perfil_guardrail
            ),
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
                    )
                ],
                content_filters_tier_config=bedrock.CfnGuardrail.ContentFiltersTierConfigProperty(tier_name="STANDARD"),
            ),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="recomendacion_credito",
                        type="DENY",
                        definition="Recomendar, sugerir o dictaminar que una solicitud de crédito se apruebe, rechace, autorice u "
                        "otorgue, o calificarla como viable o no viable.",
                        examples=[
                            "Se recomienda aprobar el crédito.",
                            "Sugerimos rechazar la solicitud.",
                            "La solicitud es viable y debe autorizarse.",
                            "No es recomendable otorgar el financiamiento.",
                        ],
                        input_enabled=False,
                        input_action="NONE",
                        output_enabled=True,
                        output_action="BLOCK",
                    )
                ],
                topics_tier_config=bedrock.CfnGuardrail.TopicsTierConfigProperty(tier_name="STANDARD"),
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type=tipo,
                        action=salida,
                        input_action=entrada,
                        output_action=salida,
                        input_enabled=True,
                        output_enabled=True,
                    )
                    for tipo, (entrada, salida) in guardrail_props["pii"].items()
                ],
                regexes_config=[
                    bedrock.CfnGuardrail.RegexConfigProperty(
                        name=nombre,
                        pattern=patron,
                        action="BLOCK",
                        input_action="BLOCK",
                        output_action="BLOCK",
                        input_enabled=True,
                        output_enabled=True,
                    )
                    for nombre, patron in guardrail_props["regex"].items()
                ],
            ),
            tags=[CfnTag(key="clasificacion-datos", value="interno")],
        )
        self.guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "GuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description=f"config {huella_guardrail}",
        )

        # --- Perfil de inferencia de aplicación (etiquetas de costo) ----------------------------------------
        perfil_sistema = (
            f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:inference-profile/{app.llm.modelo_id}"
        )
        if cfg.crear_perfil_aplicacion:
            perfil = bedrock.CfnApplicationInferenceProfile(
                self,
                "PerfilInferencia",
                inference_profile_name=f"{p}-redaccion",
                description=f"Redacción de memos ({app.llm.modelo_id})",
                model_source=bedrock.CfnApplicationInferenceProfile.InferenceProfileModelSourceProperty(
                    copy_from=perfil_sistema
                ),
                tags=[
                    CfnTag(key="proyecto", value="memo-credito"),
                    CfnTag(key="entorno", value=entorno.nombre),
                    CfnTag(key="centro-de-costo", value="riesgos-credito"),
                ],
            )
            self.perfil_arn = perfil.attr_inference_profile_arn
        else:
            self.perfil_arn = perfil_sistema

        # --- Lambdas ------------------------------------------------------------------------------------
        comunes = {
            "ENTORNO": entorno.nombre,
            "BUCKET_DATOS": datos.datos.bucket_name,
            "BUCKET_AUDITORIA": datos.auditoria.bucket_name,
            "TABLA_SOLICITUDES": datos.solicitudes.table_name,
            "TABLA_IDEMPOTENCIA": datos.idempotencia.table_name,
            "MODO_LLM": app.llm.modo,
            "PERMITIR_OVERRIDE_MODO": "true" if app.llm.permitir_override_modo else "false",
            "POWERTOOLS_SERVICE_NAME": "memo-credito",
            "POWERTOOLS_METRICS_NAMESPACE": f"MemoCredito/{entorno.nombre}",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "MEMO_CONFIG_DIR": "/var/task/config",
            "MEMO_PROMPTS_DIR": "/var/task/prompts",
            "GIT_COMMIT": os.environ.get("GIT_COMMIT", "local"),
        }
        self.fn_ingesta = self._funcion("ingesta", "ingesta", 60, 512, comunes)
        self.fn_extraer = self._funcion("extraer", "extraer", 90, 1024, comunes)
        self.fn_validar = self._funcion("validar-calcular", "validar_calcular", 90, 1024, comunes)
        self.fn_redactar = self._funcion(
            "redactar",
            "redactar",
            240,
            1024,
            {
                **comunes,
                "GUARDRAIL_ARN": self.guardrail.attr_guardrail_arn,
                "GUARDRAIL_VERSION": self.guardrail_version.attr_version,
                "PERFIL_INFERENCIA_ARN": self.perfil_arn,
            },
            reservada=cfg.concurrencia_reservada_redaccion,
        )
        self.fn_esperar = self._funcion("esperar-revision", "esperar_revision", 30, 512, comunes)
        self.fn_aplicar = self._funcion("aplicar-revision", "aplicar_revision", 60, 512, comunes)
        self.fn_registrar = self._funcion("registrar-revision", "registrar_revision", 90, 1024, comunes)
        self.funciones = [
            self.fn_ingesta,
            self.fn_extraer,
            self.fn_validar,
            self.fn_redactar,
            self.fn_esperar,
            self.fn_aplicar,
            self.fn_registrar,
        ]

        # Permisos de datos: acciones explícitas por función (sin PutObjectRetention/LegalHold que agrega grant_put)
        d, a = datos.datos, datos.auditoria
        leer, escribir = ["s3:GetObject"], ["s3:PutObject"]
        self._s3(
            self.fn_ingesta,
            d,
            ["s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectAttributes", "s3:GetObjectVersionAttributes"],
            "entrada/*",
        )
        self._s3(self.fn_extraer, d, leer, "entrada/*")
        self._s3(self.fn_extraer, d, escribir, "trabajo/*")
        self._s3(self.fn_validar, d, leer + escribir, "trabajo/*")
        self._s3(self.fn_redactar, d, leer + escribir, "trabajo/*")
        self._s3(self.fn_redactar, d, leer, "entrada/*/solicitud.json")
        self._s3(self.fn_aplicar, d, leer, "revisiones/*")
        self._s3(self.fn_registrar, d, leer, "revisiones/*")
        self._s3(self.fn_registrar, d, leer + escribir, "trabajo/*")
        for fn in (self.fn_validar, self.fn_redactar, self.fn_registrar):
            self._s3(fn, a, leer + escribir, "*")
            # ListBucket hace que una parte ausente responda 404 (no 403) al construir el manifiesto
            self._rol(fn).add_to_principal_policy(
                iam.PolicyStatement(actions=["s3:ListBucket"], resources=[a.bucket_arn])
            )
        self._tabla(self.fn_ingesta, ["dynamodb:GetItem", "dynamodb:UpdateItem"])
        self._tabla(self.fn_validar, ["dynamodb:UpdateItem"])
        self._tabla(self.fn_redactar, ["dynamodb:UpdateItem"])
        self._tabla(
            self.fn_redactar,
            ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
            datos.idempotencia,
        )
        self._tabla(self.fn_esperar, ["dynamodb:UpdateItem"])
        self._tabla(self.fn_aplicar, ["dynamodb:GetItem", "dynamodb:UpdateItem"])
        self._tabla(self.fn_registrar, ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:PutItem"])
        for fn in self.funciones:
            datos.llave.grant_encrypt_decrypt(fn)  # S3 y DynamoDB cifrados con la llave del entorno

        # Bedrock: invocar solo el perfil y el modelo base; guardrail obligatorio (Deny sin guardrail)
        modelo_base = app.llm.modelo_id.split(".", 1)[1]
        rol_redactar = self.fn_redactar.role
        assert rol_redactar is not None
        rol_redactar.add_to_principal_policy(
            iam.PolicyStatement(
                sid="InvocarPerfil", actions=["bedrock:InvokeModel"], resources=[self.perfil_arn, perfil_sistema]
            )
        )
        rol_redactar.add_to_principal_policy(
            iam.PolicyStatement(
                sid="InvocarModeloBaseEnRegionesDelPerfil",
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:{Aws.PARTITION}:bedrock:{r}::foundation-model/{modelo_base}" for r in REGIONES_PERFIL_US
                ],
            )
        )
        rol_redactar.add_to_principal_policy(
            iam.PolicyStatement(
                sid="ExigirGuardrail",
                effect=iam.Effect.DENY,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"],
                conditions={"StringNotLike": {"bedrock:GuardrailIdentifier": f"{self.guardrail.attr_guardrail_arn}*"}},
            )
        )
        rol_redactar.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AplicarGuardrail",
                actions=["bedrock:ApplyGuardrail"],
                resources=[
                    self.guardrail.attr_guardrail_arn,
                    *[
                        f"arn:{Aws.PARTITION}:bedrock:{r}:{Aws.ACCOUNT_ID}:guardrail-profile/us.guardrail.v1:0"
                        for r in REGIONES_PERFIL_US
                    ],
                ],
            )
        )

        # --- Step Functions ----------------------------------------------------------------------------
        self.maquina = self._maquina_de_estados()
        self.maquina.grant_start_execution(self.fn_ingesta)
        self.maquina.grant_execution(self.fn_ingesta, "states:StopExecution")
        self.maquina.grant_task_response(self.fn_aplicar)
        self.fn_ingesta.add_environment("MAQUINA_ESTADOS_ARN", self.maquina.state_machine_arn)

        # --- EventBridge: S3 -> ingesta / aplicar_revision (con DLQ y destino de fallo) -------------------
        self.dlq_eventos = sqs.Queue(
            self,
            "DlqEventos",
            queue_name=f"{p}-dlq-eventos",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=datos.llave,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        suprimir(
            self.dlq_eventos,
            [
                (
                    "AwsSolutions-SQS3",
                    "Es la cola de mensajes fallidos (DLQ) de EventBridge y de las Lambdas asíncronas.",
                )
            ],
        )
        for fn in (self.fn_ingesta, self.fn_aplicar):
            fn.configure_async_invoke(
                retry_attempts=1, max_event_age=Duration.hours(1), on_failure=destinos.SqsDestination(self.dlq_eventos)
            )
        for nombre, patron, fn in (
            ("Manifiestos", "entrada/*/_manifest.json", self.fn_ingesta),
            ("Revisiones", "revisiones/*", self.fn_aplicar),
        ):
            regla = events.Rule(
                self,
                f"Regla{nombre}",
                rule_name=f"{p}-{nombre.lower()}",
                event_pattern=events.EventPattern(
                    source=["aws.s3"],
                    detail_type=["Object Created"],
                    detail={"bucket": {"name": [datos.datos.bucket_name]}, "object": {"key": [{"wildcard": patron}]}},
                ),
            )
            regla.add_target(
                targets.LambdaFunction(
                    fn, retry_attempts=2, max_event_age=Duration.hours(1), dead_letter_queue=self.dlq_eventos
                )
            )

        # --- Parámetros SSM ----------------------------------------------------------------------------
        for nombre, valor in (
            ("maquina-estados-arn", self.maquina.state_machine_arn),
            ("guardrail-arn", self.guardrail.attr_guardrail_arn),
            ("guardrail-id", self.guardrail.attr_guardrail_id),
            ("guardrail-version", self.guardrail_version.attr_version),
            ("perfil-inferencia-arn", self.perfil_arn),
        ):
            ssm.StringParameter(
                self, f"Param{nombre}", parameter_name=f"/memo-credito/{entorno.nombre}/{nombre}", string_value=valor
            )

        suprimir(
            self,
            [
                (
                    "AwsSolutions-IAM5",
                    "Comodines limitados a prefijos de objetos S3 (trabajo/*, entrada/*), acciones de KMS generadas por "
                    "grants de CDK, X-Ray (sin permisos a nivel de recurso) y el Deny de Bedrock que exige el guardrail.",
                ),
                (
                    "AwsSolutions-L1",
                    "Runtime fijado a Python 3.12 por decisión de arquitectura; se revisa cada trimestre.",
                ),
            ],
        )

    def _funcion(
        self,
        nombre: str,
        modulo: str,
        timeout: int,
        memoria: int,
        entorno_vars: dict[str, str],
        reservada: int | None = None,
    ) -> lambda_.Function:
        cfg = self.entorno.infra
        p = self.entorno.prefijo
        rol = iam.Role(
            self,
            f"Rol{nombre.title().replace('-', '')}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=f"Rol de la Lambda {p}-{nombre}",
        )
        grupo = logs.LogGroup(
            self,
            f"Logs{nombre.title().replace('-', '')}",
            log_group_name=f"/aws/lambda/{p}-{nombre}",
            retention=_retencion(cfg.retencion_logs_dias),
            encryption_key=self.datos.llave,
            removal_policy=self.borrado,
            data_protection_policy=_politica_proteccion() if cfg.data_protection_logs else None,
        )
        grupo.grant_write(rol)
        if self.datos.vpc is not None:
            # ENIs de Lambda en la VPC (equivale a AWSLambdaVPCAccessExecutionRole sin sus permisos de logs a "*")
            rol.add_to_policy(
                iam.PolicyStatement(
                    sid="EniVpc",
                    resources=["*"],
                    actions=[
                        "ec2:CreateNetworkInterface",
                        "ec2:DescribeNetworkInterfaces",
                        "ec2:DescribeSubnets",
                        "ec2:DeleteNetworkInterface",
                        "ec2:AssignPrivateIpAddresses",
                        "ec2:UnassignPrivateIpAddresses",
                    ],
                )
            )
        fn = lambda_.Function(
            self,
            f"Fn{nombre.title().replace('-', '')}",
            function_name=f"{p}-{nombre}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            code=self.codigo,
            handler=f"memo_credito.handlers.{modulo}.handler",
            timeout=Duration.seconds(timeout),
            memory_size=memoria,
            role=rol,
            log_group=grupo,
            tracing=lambda_.Tracing.ACTIVE,
            environment=entorno_vars,
            reserved_concurrent_executions=reservada,
            vpc=self.datos.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED) if self.datos.vpc else None,
            security_groups=[self.grupo_seguridad] if self.grupo_seguridad else None,
        )
        return fn

    @staticmethod
    def _rol(fn: lambda_.Function) -> iam.IRole:
        assert fn.role is not None
        return fn.role

    def _s3(self, fn: lambda_.Function, bucket: s3.IBucket, acciones: list[str], patron: str) -> None:
        self._rol(fn).add_to_principal_policy(
            iam.PolicyStatement(actions=acciones, resources=[bucket.arn_for_objects(patron)])
        )

    def _tabla(self, fn: lambda_.Function, acciones: list[str], tabla: ddb.ITable | None = None) -> None:
        arn = (tabla or self.datos.solicitudes).table_arn
        self._rol(fn).add_to_principal_policy(iam.PolicyStatement(actions=acciones, resources=[arn]))

    def _invocar(
        self, id_: str, fn: lambda_.IFunction, payload: dict[str, Any] | sfn.TaskInput, resultado: str
    ) -> tasks.LambdaInvoke:
        tarea = tasks.LambdaInvoke(
            self,
            id_,
            lambda_function=fn,
            payload=payload if isinstance(payload, sfn.TaskInput) else sfn.TaskInput.from_object(payload),
            payload_response_only=True,
            result_path=resultado,
            retry_on_service_exceptions=False,
        )
        tarea.add_retry(
            errors=ERRORES_LAMBDA,
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2,
            jitter_strategy=sfn.JitterType.FULL,
        )
        return tarea

    def _actualizar(self, id_: str, estado: str, motivo_ruta: str | None = None) -> tasks.DynamoUpdateItem:
        valores = {
            ":e": tasks.DynamoAttributeValue.from_string(estado),
            ":t": tasks.DynamoAttributeValue.from_string(sfn.JsonPath.string_at("$$.State.EnteredTime")),
            ":x": tasks.DynamoAttributeValue.from_string(sfn.JsonPath.string_at("$$.Execution.Name")),
        }
        expresion = "SET #e = :e, actualizado = :t, ejecucion = :x"
        if motivo_ruta:
            valores[":m"] = tasks.DynamoAttributeValue.from_string(sfn.JsonPath.string_at(motivo_ruta))
            expresion += ", motivo = :m"
        return tasks.DynamoUpdateItem(
            self,
            id_,
            table=self.datos.solicitudes,
            key={
                "folio": tasks.DynamoAttributeValue.from_string(sfn.JsonPath.string_at("$.folio")),
                "sk": tasks.DynamoAttributeValue.from_string("ESTADO"),
            },
            update_expression=expresion,
            expression_attribute_names={"#e": "estado"},
            expression_attribute_values=valores,
            result_path=sfn.JsonPath.DISCARD,
        )

    def _maquina_de_estados(self) -> sfn.StateMachine:
        cfg, p, datos = self.entorno.infra, self.entorno.prefijo, self.datos
        base = {"folio.$": "$.folio", "ejecucion.$": "$.ejecucion"}

        fallo = sfn.Fail(self, "Fallo", error="ProcesamientoFallido", cause="Ver estado y motivo en DynamoDB")
        notificar = tasks.SnsPublish(
            self,
            "NotificarError",
            topic=datos.alarmas,
            result_path=sfn.JsonPath.DISCARD,
            message=sfn.TaskInput.from_object(
                {"folio.$": "$.folio", "error.$": "$.error.Error", "ejecucion.$": "$$.Execution.Name"}
            ),
        )
        manejo_error = self._actualizar("MarcarError", "ERROR", "$.error.Error").next(notificar).next(fallo)

        inicializar = sfn.Pass(
            self,
            "Inicializar",
            parameters={
                "folio.$": "$.folio",
                "input_hash.$": "$.input_hash",
                "documentos.$": "$.documentos",
                "modo_llm.$": "$.modo_llm",
                "reproceso.$": "$.reproceso",
                "ejecucion.$": "$$.Execution.Name",
                "escalamientos": 0,
            },
        )
        registrar_inicio = self._actualizar("RegistrarInicio", "PROCESANDO")
        mapa = sfn.Map(
            self,
            "ExtraerDocumentos",
            items_path="$.documentos",
            max_concurrency=cfg.map_max_concurrency,
            item_selector={"folio.$": "$.folio", "ejecucion.$": "$.ejecucion", "doc.$": "$$.Map.Item.Value"},
            result_path="$.extracciones",
        )
        mapa.item_processor(
            self._invocar("ExtraerDocumento", self.fn_extraer, sfn.TaskInput.from_json_path_at("$"), "$")
        )
        validar = self._invocar(
            "ValidarYCalcular",
            self.fn_validar,
            {
                **base,
                "extracciones.$": "$.extracciones",
                "input_hash.$": "$.input_hash",
                "documentos.$": "$.documentos",
                "modo_llm.$": "$.modo_llm",
                "reproceso.$": "$.reproceso",
            },
            "$.analisis",
        )
        carga_redaccion = {**base, "clave_contexto.$": "$.analisis.clave_contexto", "modo_llm.$": "$.modo_llm"}
        redactar = self._invocar(
            "RedactarMemo", self.fn_redactar, {**carga_redaccion, "forzar_plantilla": None}, "$.redaccion"
        )
        redactar_plantilla = self._invocar(
            "RedactarConPlantilla",
            self.fn_redactar,
            {**carga_redaccion, "forzar_plantilla": "falla del paso de redacción"},
            "$.redaccion",
        )
        esperar = tasks.LambdaInvoke(
            self,
            "EsperarRevision",
            lambda_function=self.fn_esperar,
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload=sfn.TaskInput.from_object(
                {
                    **base,
                    "token": sfn.JsonPath.task_token,
                    "memo_sha256.$": "$.redaccion.memo_sha256",
                    "memo_s3_key.$": "$.redaccion.memo_s3_key",
                    "escalamientos.$": "$.escalamientos",
                }
            ),
            result_path="$.revision",
            task_timeout=sfn.Timeout.duration(Duration.seconds(cfg.revision_sla_s)),
        )
        escalar = tasks.SnsPublish(
            self,
            "EscalarRevision",
            topic=datos.escalamiento,
            result_path=sfn.JsonPath.DISCARD,
            message=sfn.TaskInput.from_object(
                {**base, "mensaje": "Revisión pendiente fuera de SLA", "escalamientos.$": "$.escalamientos"}
            ),
        )
        incrementar = sfn.Pass(
            self,
            "IncrementarEscalamientos",
            parameters={
                "folio.$": "$.folio",
                "ejecucion.$": "$.ejecucion",
                "input_hash.$": "$.input_hash",
                "modo_llm.$": "$.modo_llm",
                "reproceso.$": "$.reproceso",
                "analisis.$": "$.analisis",
                "redaccion.$": "$.redaccion",
                "escalamientos.$": "States.MathAdd($.escalamientos, 1)",
            },
        )
        vencida = self._actualizar("MarcarRevisionVencida", "REVISION_VENCIDA").next(
            sfn.Fail(self, "RevisionNoAtendida", error="RevisionVencida", cause="Se agotaron los escalamientos")
        )
        decidir = (
            sfn.Choice(self, "EscalarDeNuevo")
            .when(sfn.Condition.number_less_than("$.escalamientos", cfg.max_escalamientos), esperar)
            .otherwise(vencida)
        )
        registrar_revision = self._invocar(
            "RegistrarRevision",
            self.fn_registrar,
            {**base, "revision.$": "$.revision", "redaccion.$": "$.redaccion"},
            "$.final",
        )

        for paso in (mapa, validar, redactar_plantilla, registrar_revision):
            paso.add_catch(manejo_error, errors=["States.ALL"], result_path="$.error")
        registrar_inicio.add_catch(manejo_error, errors=["States.ALL"], result_path="$.error")
        redactar.add_catch(redactar_plantilla, errors=["States.ALL"], result_path="$.error_redaccion")
        esperar.add_catch(escalar, errors=["States.Timeout"], result_path="$.timeout_revision")
        esperar.add_catch(manejo_error, errors=["States.ALL"], result_path="$.error")
        escalar.next(incrementar).next(decidir)
        redactar.next(esperar)
        redactar_plantilla.next(esperar)
        esperar.next(registrar_revision).next(sfn.Succeed(self, "Exito"))
        definicion = inicializar.next(registrar_inicio).next(mapa).next(validar).next(redactar)

        grupo = logs.LogGroup(
            self,
            "LogsFlujo",
            log_group_name=f"/aws/vendedlogs/states/{p}-flujo",
            retention=_retencion(cfg.retencion_logs_dias),
            encryption_key=datos.llave,
            removal_policy=self.borrado,
        )
        maquina = sfn.StateMachine(
            self,
            "Flujo",
            state_machine_name=f"{p}-flujo",
            definition_body=sfn.DefinitionBody.from_chainable(definicion),
            state_machine_type=sfn.StateMachineType.STANDARD,
            timeout=Duration.days(30),
            tracing_enabled=True,
            logs=sfn.LogOptions(destination=grupo, level=sfn.LogLevel.ALL, include_execution_data=False),
        )
        datos.llave.grant_encrypt_decrypt(maquina)
        return maquina


def _retencion(dias: int) -> logs.RetentionDays:
    opciones = {
        1: logs.RetentionDays.ONE_DAY,
        3: logs.RetentionDays.THREE_DAYS,
        7: logs.RetentionDays.ONE_WEEK,
        14: logs.RetentionDays.TWO_WEEKS,
        30: logs.RetentionDays.ONE_MONTH,
        90: logs.RetentionDays.THREE_MONTHS,
        180: logs.RetentionDays.SIX_MONTHS,
        365: logs.RetentionDays.ONE_YEAR,
    }
    return opciones.get(dias, logs.RetentionDays.TWO_WEEKS)


def _politica_proteccion() -> logs.DataProtectionPolicy:
    return logs.DataProtectionPolicy(
        name="memo-credito-pii",
        description="Audita y enmascara PII que llegara a los logs (CURP, RFC de persona física, teléfonos de México, correos)",
        identifiers=[
            logs.CustomDataIdentifier("CURP", r"[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d"),
            logs.CustomDataIdentifier("RFCPersonaFisica", r"[A-Z&]{4}\d{6}[A-Z0-9]{3}"),
            logs.CustomDataIdentifier("TelefonoMX", r"\d{2}[ -]?\d{4}[ -]?\d{4}"),
            logs.DataIdentifier.EMAILADDRESS,
        ],
    )
