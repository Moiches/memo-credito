# Runbook de operación

Las alarmas publican en el tópico SNS `memo-credito-{env}-alarmas`. El dashboard es `memo-credito-{env}-operacion`. El `correlation_id` de los logs es `{folio}#{ejecucion}`. Los logs no contienen PII: solo conteos, hashes y códigos.

Comandos base (perfil con permisos de operación):

```bash
uv run memo --entorno dev pendientes                         # folios en PENDIENTE_REVISION
uv run python tareas.py reprocesar --entorno dev --perfil <p> --folio SOL-0001 --motivo "<motivo>"
uv run python tareas.py probar-guardrail --entorno dev --perfil <p>
```

## 1. Throttling de Bedrock (`bedrock-throttles`, `throttles-lambda`)

- **Diagnóstico:**
  - `BedrockThrottles` y `EstimatedTPMQuotaUsage` frente a la cuota del modelo.
  - ¿Subió `maxTokens` en `config/base.yaml`? ¿Hay una ráfaga?
  - ¿La concurrencia de Lambda de la cuenta sigue en 10?
- **Acción:**
  - Bajar `map_max_concurrency` y la concurrencia reservada en `config/{env}.yaml` y redesplegar.
  - Confirmar `maxTokens` y pedir un aumento de cuota.
  - Para cortar el uso del LLM, activar el interruptor `llm.modo: plantilla` en `config/{env}.yaml` y redesplegar. Los memos salen con la plantilla determinista, marcados "Requiere revisión", y las cifras siguen siendo exactas.

## 2. Pico de conciliaciones fallidas (`ConciliacionFallida` en el dashboard)

- **Diagnóstico:**
  - ¿Comparten el mismo `formato` o banco? ¿Hay un layout nuevo? Revisar `01_extraccion.json` del paquete de auditoría.
- **Acción:**
  - Escalar a ingeniería con un caso de muestra.
  - Crear la versión nueva del parser con su golden en `evals/casos.yaml`; cambiar valores esperados requiere CODEOWNERS (regla 5).
  - Mientras tanto, los casos salen con la alerta correspondiente y sin el cálculo afectado, no en error.

## 3. Fallas del verificador o memos con plantilla (`memo-plantilla`, `VerificadorFallas`)

- **Diagnóstico:**
  - `VerificadorFallas` por regla.
  - ¿Cambió el prompt, el modelo o el guardrail?
  - `03_redaccion.json` guarda la petición, la respuesta cruda y los errores por intento.
- **Acción:**
  - Rollback al commit anterior (`cd.yml` con `workflow_dispatch` y `ref=<sha>`).
  - Correr `uv run python tareas.py evals-real --perfil <p>` y comparar el scorecard.

## 4. Ejecuciones en error (`flujo-fallido`, `errores-*`, `dlq-eventos`)

- **Diagnóstico:**
  - `estado=ERROR` y `motivo` en DynamoDB (`memo-credito-{env}-solicitudes`, clave `folio` y `sk=ESTADO`).
  - Historial de la ejecución en Step Functions.
  - Mensajes en `memo-credito-{env}-dlq-eventos`: eventos de S3 que no llegaron a la ingesta o revisiones que no se aplicaron.
- **Acción:**
  - Error transitorio: `aws stepfunctions redrive-execution --execution-arn <arn>`.
  - Error de datos: corregir el expediente y reprocesar. Reenviar el mismo manifiesto se deduplica a propósito; `reprocesar` crea la ejecución `-rN`.

## 5. Costo fuera de rango (`costo-memo`, `costo-hora`, presupuesto)

- **Diagnóstico:**
  - Tokens por llamada (`TokensEntrada` y `TokensSalida`) y `LlamadasLLM` por memo.
  - Tamaño de los hechos: no debe pasar de 8 KB, lo valida `construir_hechos`.
  - AWS Budgets por la etiqueta `proyecto`; ejecutar `tareas.py activar-etiquetas-costo` una vez.
- **Acción:**
  - Verificar los límites (`maxTokens` y máximo de 2 correcciones).
  - Activar el interruptor `plantilla` del punto 1.
  - Revisar si hay un bucle de reprocesos.

## 6. Revisión vencida (`REVISION_VENCIDA`, tópico de escalamiento)

- **Diagnóstico:**
  - Folios con `estado=REVISION_VENCIDA`: se agotaron los escalamientos (SLA de 1 h en dev y 24 h en prod; 2 escalamientos en dev y 3 en prod).
- **Acción:**
  - Reasignar a otro analista y reprocesar el folio para generar un borrador nuevo con su propio token.
  - La revisión anterior queda en la auditoría.

## Teardown de dev

```bash
uv run python tareas.py destroy --entorno dev --perfil <p>
```

Vacía los buckets de dev, incluida la auditoría GOVERNANCE de 1 día con bypass explícito, y después corre `cdk destroy`. El script se niega a correr en prod: allí la auditoría está en modo COMPLIANCE y no se puede borrar por diseño.
