"""Datos y seguridad: KMS, buckets (datos, auditoría con Object Lock, accesos), DynamoDB, SNS, políticas de personas y VPC."""

from __future__ import annotations

import hashlib

from aws_cdk import (
    Aws,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from infra.config import Entorno
from infra.nag import suprimir


class DatosSeguridadStack(Stack):
    def __init__(self, scope: Construct, id_: str, entorno: Entorno, **kwargs: object) -> None:
        super().__init__(scope, id_, **kwargs)
        cfg = entorno.infra
        p = entorno.prefijo
        borrado = RemovalPolicy.DESTROY if cfg.destruir_al_borrar else RemovalPolicy.RETAIN

        # --- KMS ------------------------------------------------------------------------------------------
        self.llave = kms.Key(
            self,
            "Llave",
            alias=f"alias/{p}",
            enable_key_rotation=True,
            removal_policy=borrado,
            pending_window=Duration.days(7),
            description=f"Datos de {p} (documentos, auditoría, estado, logs)",
        )
        for servicio in ("cloudwatch.amazonaws.com", "budgets.amazonaws.com", "events.amazonaws.com"):
            self.llave.add_to_resource_policy(
                iam.PolicyStatement(
                    actions=["kms:Decrypt", "kms:GenerateDataKey*"],
                    principals=[iam.ServicePrincipal(servicio)],
                    resources=["*"],
                    conditions={"StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID}},
                )
            )
        self.llave.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"],
                principals=[iam.ServicePrincipal(f"logs.{Aws.REGION}.amazonaws.com")],
                resources=["*"],
                conditions={
                    "ArnLike": {
                        "kms:EncryptionContext:aws:logs:arn": f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:*"
                    }
                },
            )
        )

        # --- Buckets --------------------------------------------------------------------------------------
        self.accesos = s3.Bucket(
            self,
            "Accesos",
            bucket_name=f"{p}-accesos-{Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=borrado,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(90))],
        )
        suprimir(
            self.accesos,
            [
                (
                    "AwsSolutions-S1",
                    "Es el bucket destino de los logs de acceso; registrarse a sí mismo sería recursivo.",
                )
            ],
        )

        self.datos = s3.Bucket(
            self,
            "Datos",
            bucket_name=f"{p}-datos-{Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.llave,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=borrado,
            server_access_logs_bucket=self.accesos,
            server_access_logs_prefix="datos/",
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="entrada", prefix="entrada/", expiration=Duration.days(cfg.ciclo_vida_entrada_dias)
                ),
                s3.LifecycleRule(
                    id="trabajo", prefix="trabajo/", expiration=Duration.days(cfg.ciclo_vida_trabajo_dias)
                ),
                s3.LifecycleRule(
                    id="revisiones", prefix="revisiones/", expiration=Duration.days(cfg.ciclo_vida_trabajo_dias)
                ),
                s3.LifecycleRule(id="versiones", noncurrent_version_expiration=Duration.days(7)),
            ],
        )
        cfn_datos = self.datos.node.default_child
        assert isinstance(cfn_datos, s3.CfnBucket)
        cfn_datos.add_property_override("NotificationConfiguration.EventBridgeConfiguration.EventBridgeEnabled", True)
        retencion = (
            s3.ObjectLockRetention.compliance(Duration.days(cfg.auditoria_dias))
            if cfg.auditoria_modo == "COMPLIANCE"
            else s3.ObjectLockRetention.governance(Duration.days(cfg.auditoria_dias))
        )
        self.auditoria = s3.Bucket(
            self,
            "Auditoria",
            bucket_name=f"{p}-auditoria-{Aws.ACCOUNT_ID}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.llave,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            object_lock_enabled=True,
            object_lock_default_retention=retencion,
            removal_policy=RemovalPolicy.RETAIN if cfg.auditoria_modo == "COMPLIANCE" else borrado,
            server_access_logs_bucket=self.accesos,
            server_access_logs_prefix="auditoria/",
            lifecycle_rules=[
                s3.LifecycleRule(id="versiones", noncurrent_version_expiration=Duration.days(cfg.auditoria_dias + 1))
            ],
        )

        # --- DynamoDB -------------------------------------------------------------------------------------
        self.solicitudes = ddb.Table(
            self,
            "Solicitudes",
            table_name=f"{p}-solicitudes",
            partition_key=ddb.Attribute(name="folio", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=ddb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=self.llave,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=borrado,
        )
        self.solicitudes.add_global_secondary_index(
            index_name="por-estado",
            partition_key=ddb.Attribute(name="estado", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="actualizado", type=ddb.AttributeType.STRING),
            projection_type=ddb.ProjectionType.INCLUDE,
            non_key_attributes=["memo_sha256", "memo_s3_key", "pendiente_desde"],
        )
        self.idempotencia = ddb.Table(
            self,
            "Idempotencia",
            table_name=f"{p}-idempotencia",
            partition_key=ddb.Attribute(name="id", type=ddb.AttributeType.STRING),
            time_to_live_attribute="expiration",
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            encryption=ddb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=self.llave,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- SNS ------------------------------------------------------------------------------------------
        self.alarmas = sns.Topic(self, "Alarmas", topic_name=f"{p}-alarmas", master_key=self.llave, enforce_ssl=True)
        self.escalamiento = sns.Topic(
            self, "Escalamiento", topic_name=f"{p}-escalamiento", master_key=self.llave, enforce_ssl=True
        )
        self.alarmas.add_to_resource_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                resources=[self.alarmas.topic_arn],
                conditions={"StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID}},
            )
        )
        for correo in cfg.suscriptores_alarmas:
            sns.Subscription(
                self,
                f"Sub{hashlib.sha256(correo.encode()).hexdigest()[:8]}",
                topic=self.alarmas,
                endpoint=correo,
                protocol=sns.SubscriptionProtocol.EMAIL,
            )

        # --- VPC (solo prod) -----------------------------------------------------------------------------
        self.vpc: ec2.Vpc | None = None
        if cfg.vpc:
            self.vpc = ec2.Vpc(
                self,
                "Vpc",
                max_azs=2,
                nat_gateways=0,
                subnet_configuration=[
                    ec2.SubnetConfiguration(name="privada", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24)
                ],
                flow_logs={
                    "flujo": ec2.FlowLogOptions(
                        destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                            logs.LogGroup(
                                self,
                                "LogsFlujoVpc",
                                retention=logs.RetentionDays.SIX_MONTHS,
                                encryption_key=self.llave,
                                removal_policy=borrado,
                            )
                        )
                    )
                },
                gateway_endpoints={
                    "S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3),
                    "DynamoDB": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.DYNAMODB),
                },
            )
            for nombre, servicio in (
                ("BedrockRuntime", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME),
                ("StepFunctions", ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS),
                ("XRay", ec2.InterfaceVpcEndpointAwsService.XRAY),
            ):
                self.vpc.add_interface_endpoint(nombre, service=servicio, private_dns_enabled=True)

        # --- Políticas de personas (se adjuntan a permission sets de IAM Identity Center) -----------------
        lectura_kms = iam.PolicyStatement(
            actions=["kms:Decrypt", "kms:GenerateDataKey"],
            resources=[self.llave.key_arn],
            conditions={
                "StringLike": {
                    "kms:ViaService": [f"s3.{Aws.REGION}.amazonaws.com", f"dynamodb.{Aws.REGION}.amazonaws.com"]
                }
            },
        )
        ssm_lectura = iam.PolicyStatement(
            actions=["ssm:GetParametersByPath", "ssm:GetParameter"],
            resources=[
                f"arn:{Aws.PARTITION}:ssm:{Aws.REGION}:{Aws.ACCOUNT_ID}:parameter/memo-credito/{entorno.nombre}*"
            ],
        )
        self.politica_analista = iam.ManagedPolicy(
            self,
            "PoliticaAnalista",
            managed_policy_name=f"{p}-analista",
            statements=[
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[
                        self.datos.arn_for_objects("trabajo/*/memo.md"),
                        self.datos.arn_for_objects("trabajo/*/memo_final.md"),
                    ],
                ),
                iam.PolicyStatement(
                    actions=["s3:PutObject"], resources=[self.datos.arn_for_objects("revisiones/${aws:userid}/*")]
                ),
                iam.PolicyStatement(
                    actions=["dynamodb:GetItem", "dynamodb:Query"],
                    resources=[self.solicitudes.table_arn, f"{self.solicitudes.table_arn}/index/por-estado"],
                ),
                lectura_kms,
                ssm_lectura,
            ],
        )
        self.politica_comite = iam.ManagedPolicy(
            self,
            "PoliticaComite",
            managed_policy_name=f"{p}-comite",
            statements=[
                iam.PolicyStatement(
                    actions=["s3:GetObject"], resources=[self.datos.arn_for_objects("trabajo/*/memo_final.md")]
                ),
                lectura_kms,
                ssm_lectura,
            ],
        )
        self.politica_auditor = iam.ManagedPolicy(
            self,
            "PoliticaAuditor",
            managed_policy_name=f"{p}-auditor",
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "s3:GetObject",
                        "s3:GetObjectRetention",
                        "s3:GetObjectVersion",
                        "s3:ListBucket",
                        "s3:ListBucketVersions",
                    ],
                    resources=[self.auditoria.bucket_arn, self.auditoria.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    actions=["dynamodb:GetItem", "dynamodb:Query"], resources=[self.solicitudes.table_arn]
                ),
                iam.PolicyStatement(actions=["cloudtrail:LookupEvents"], resources=["*"]),
                lectura_kms,
                ssm_lectura,
            ],
        )
        self.politica_operacion = iam.ManagedPolicy(
            self,
            "PoliticaOperacion",
            managed_policy_name=f"{p}-operacion",
            statements=[
                # Reprocesar (§16.2): volver a subir el expediente y su manifiesto; sin lectura de documentos ni memos
                iam.PolicyStatement(actions=["s3:PutObject"], resources=[self.datos.arn_for_objects("entrada/*")]),
                iam.PolicyStatement(actions=["dynamodb:GetItem"], resources=[self.solicitudes.table_arn]),
                lectura_kms,
                iam.PolicyStatement(
                    actions=[
                        "states:DescribeExecution",
                        "states:ListExecutions",
                        "states:GetExecutionHistory",
                        "states:StopExecution",
                        "states:RedriveExecution",
                        "states:DescribeStateMachine",
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:states:{Aws.REGION}:{Aws.ACCOUNT_ID}:stateMachine:{p}-flujo",
                        f"arn:{Aws.PARTITION}:states:{Aws.REGION}:{Aws.ACCOUNT_ID}:execution:{p}-flujo:*",
                    ],
                ),
                iam.PolicyStatement(
                    actions=[
                        "cloudwatch:GetMetricData",
                        "cloudwatch:DescribeAlarms",
                        "logs:StartQuery",
                        "logs:GetQueryResults",
                        "logs:FilterLogEvents",
                    ],
                    resources=["*"],
                ),
                ssm_lectura,
            ],
        )
        for politica, razon in (
            (
                self.politica_analista,
                "Comodín de objetos por prefijo; ${aws:userid} limita la escritura a la propia identidad.",
            ),
            (self.politica_comite, "Comodín de objetos limitado a memos finales."),
            (
                self.politica_auditor,
                "Lectura de todo el bucket de auditoría (es su función); LookupEvents no admite recursos.",
            ),
            (
                self.politica_operacion,
                "Métricas y consultas de logs no admiten permisos a nivel de recurso; sin acceso a documentos.",
            ),
        ):
            suprimir(politica, [("AwsSolutions-IAM5", razon)])

        # --- Parámetros SSM para la CLI y los tests --------------------------------------------------------
        for nombre, valor in (
            ("bucket-datos", self.datos.bucket_name),
            ("bucket-auditoria", self.auditoria.bucket_name),
            ("tabla-solicitudes", self.solicitudes.table_name),
            ("topico-alarmas-arn", self.alarmas.topic_arn),
        ):
            ssm.StringParameter(
                self, f"Param{nombre}", parameter_name=f"/memo-credito/{entorno.nombre}/{nombre}", string_value=valor
            )

        Tags.of(self.datos).add("clasificacion-datos", "restringido")
        Tags.of(self.auditoria).add("clasificacion-datos", "restringido")
        Tags.of(self.solicitudes).add("clasificacion-datos", "restringido")
        Tags.of(self.llave).add("clasificacion-datos", "restringido")
        Tags.of(self.accesos).add("clasificacion-datos", "interno")
