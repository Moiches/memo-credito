# Implementado frente a propuesto

Estado al 2026-11-25. "Desplegado" significa que funciona en la cuenta de dev (`us-east-1`) y se verificó con las pruebas o comandos indicados.

## Implementado

| Componente | Estado | Evidencia |
|---|---|---|
| Extracción determinista (estado de cuenta por coordenadas, constancia, EEFF, JSON) con procedencia | Implementado | Goldens de los 8 casos (`tests/regresion`), 689 movimientos al centavo |
| Validaciones, catálogo de alertas (A01–A16, R01–R04, S01–S03, D01) y periodos explícitos | Implementado | `tests/unit/test_validacion.py`, `evals/casos.yaml` |
| Indicadores en `Decimal` con estado "no calculable" | Implementado | SOL-0004: flujo y capacidad no calculables con A03 |
| Seguridad de entrada (PDF con JavaScript, cifrado, texto oculto, inyección) | Implementado | `tests/adversarial/` |
| Grafo LangGraph redactar → verificar → corregir (máximo 2) → plantilla | Implementado | `tests/unit/test_grafo.py`, `docs/grafo_redaccion.mmd` generado del código |
| Adaptador Bedrock Converse (structured outputs, guardrail, costo, auditoría cruda) | Implementado y ejecutado con Sonnet 4.6 | Corridas reales locales (`docs/registro_proceso.md`) |
| Guardrail Standard (`us.guardrail.v1:0`) obligatorio por IAM | Desplegado | `probar_guardrail`: 5 de 5; `Deny` sin guardrail probado en `tests/infra` |
| Perfil de inferencia de aplicación con etiquetas de costo | Desplegado | Stack `memo-credito-dev-flujo` |
| S3 (KMS, TLS, ciclo de vida), DynamoDB (PITR, KMS), SNS cifrado, Step Functions, 7 Lambdas arm64 | Desplegado | 3 stacks en dev; smoke SOL-0001 hasta `PENDIENTE_REVISION` en unos 18 s con el LLM simulado |
| Revisión con task token, CLI e identidad impuesta por IAM (`revisiones/${aws:userid}/`) | Desplegado | Revisión con la CLI → `LISTO_PARA_COMITE` con `revisor_id` igual al `aws:userid`; negativos en `tests/unit/test_handlers.py` |
| Throttling de Bedrock: reintentos en Step Functions antes de la plantilla | Desplegado | Cuota de 10 RPM observada; prueba en `tests/infra` y `tests/unit/test_grafo.py` |
| Auditoría inmutable (Object Lock, write-once, cadena de hashes, proyección sin PII) | Desplegado | Prueba de reproducibilidad, `verificar_paquete` con moto |
| Alarmas a SNS (17), dashboard y presupuesto mensual | Desplegado | Stack `memo-credito-dev-observabilidad` |
| Logs JSON sin PII con `correlation_id` y métricas EMF | Desplegado | Powertools más filtro de PII en `handlers/_comun.py` |
| Políticas de personas (analista, comité, auditor, operación) | Desplegado (las políticas) | `tests/infra/test_iam.py`; los permission sets de Identity Center son un prerrequisito manual |
| Inventario aprobado de permisos IAM (regla 6) y cdk-nag 3 limpio | Implementado | `tests/infra/permisos_esperados.json` |
| VPC aislada con endpoints, data protection de logs y COMPLIANCE en prod | Implementado en la configuración de prod (synth y pruebas) | `tests/infra/test_recursos.py`; prod no se desplegó |
| Workflows de GitHub Actions (ci, cd, evals-reales, carga), dependabot y CODEOWNERS | **En uso** en el repositorio privado `Moiches/memo-credito`: `ci` en verde; `cd` despliega dev con OIDC e integra; prod está apagado (`PROD_HABILITADO`) | Corridas de GitHub Actions del 2026-11-25 |
| Pruebas de integración contra dev | **9/9 en el pipeline** con el rol restringido `gh-pruebas-dev`, incluidos los negativos de permisos (en local, con el perfil administrador, esos 2 se omiten) | `tests/integracion/test_dev.py`, job `integracion-dev` |
| Prueba de carga | 100 de 100 en ráfaga (p95 de 41.8 s) tras corregir un reintento | `docs/escalamiento.md` |
| Evals reales 5 × 8 y candado del prompt | **40/40 con Sonnet 4.6 y el guardrail de dev**; candado aprobado | `evals/scorecards/20261125_1458_bedrock_5x.md`, `evals/aprobaciones.json` |

## Propuesto (no implementado)

| Componente | Por qué no está | Siguiente paso |
|---|---|---|
| Sonnet 5.5 como modelo de producción | Claude 5.x no está habilitado en la cuenta | Pedir acceso y rehacer los evals reales (ADR-0006) |
| Despliegue de prod | Requiere el pipeline con aprobación y el aumento de cuotas (Lambda 1,000, Bedrock) | `cd.yml` despliega el mismo ensamblado tras la integración |
| Caché del prompt de sistema | El prompt (unos 1,500 tokens) está cerca del mínimo cacheable; el ahorro es pequeño | Medir `cacheReadInputTokens` con `cachePoint` en el system prompt |
| Macie en prod | Solo hay datos sintéticos | Habilitar en la cuenta de prod |
| Extracción de respaldo con LLM para formatos de banco nuevos | El set trae un solo banco | Patrón del grafo con la conciliación como evaluador |
| LLM como evaluador de la redacción | No toca cifras; prioridad baja | Rúbrica sí/no sobre los evals |
| App web (API Gateway y Cognito) | Decisión 6: CLI con SSO | — |
| Copiloto con LangGraph en AgentCore | Solo en las diapositivas (v2) | — |
| Cuentas separadas (Organizations y SCP) | La demo usa una sola cuenta, con prefijos por entorno | — |
| `mx-central-1` | Sin los modelos ni un perfil geográfico para México | Revisar disponibilidad |
