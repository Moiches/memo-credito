# memo-credito: del expediente a un memo verificable

Presentación de 10 diapositivas. Las cifras provienen del repo y de la cuenta de dev al 2026-11-25.

---

## 1. Del expediente a un memo verificable

- Entran 3 estados de cuenta, la constancia, los estados financieros y la solicitud en JSON. Sale un **borrador** de memo con tabla de indicadores, validaciones, alertas y prosa.
- Cada cifra se rastrea: `memo evidencia SOL-0001 capacidad_pago` muestra la fórmula y sus entradas, y las alertas citan la página y el renglón del PDF (por ejemplo, S01 en SOL-0006: pág. 1, renglón 16).
- El analista revisa y el comité decide. El sistema **no tiene un estado "aprobado"**.
- **Demo:** SOL-0001 en dev:
  1. `memo enviar` lleva el expediente a `PENDIENTE_REVISION` en unos 18 s.
  2. `memo descargar` y `memo revisar` lo dejan en `LISTO_PARA_COMITE`.

## 2. Primero los datos, después el código

- Un parser independiente cuadró al centavo los 23 estados de cuenta (689 movimientos) y encontró 9 hallazgos adicionales. Entre ellos: el dígito verificador del RFC, el año bisiesto en la antigüedad y un encabezado repetido.
- TDD con goldens: `evals/casos.yaml` es la única fuente de valores esperados, verificada contra el parser independiente y contra el memo de ejemplo. Ningún valor esperado salió del código bajo prueba.
- **IA (Claude Code)**: generó el plan, el código, las pruebas y la infraestructura.
- **A mano**: las cifras de SOL-0001, la tipografía del ejemplo, el algoritmo del RFC y la disponibilidad real de modelos y cuotas.

## 3. Por qué no es multiagente

- Tres documentos no son tres agentes: son tres parsers.
- El orden de los pasos nunca cambia: no hay nada que planear.
- La exactitud la da el código (`Decimal`, `ROUND_HALF_UP`), no un modelo.
- La agencia vive en un solo punto: la redacción, en un ciclo evaluador-optimizador con un máximo de 2 correcciones y una plantilla determinista de respaldo (ADR-0001 y ADR-0002).

## 4. Arquitectura y sus tradeoffs

- **Flujo:** S3 → EventBridge → Step Functions Standard (Map por documento, `waitForTaskToken` para el humano) → 7 Lambdas arm64 → Bedrock (Converse con guardrail) → DynamoDB → S3 de auditoría con Object Lock.
- **Región:** perfil geográfico `us.` (us-east-1, us-east-2, us-west-2) frente a residencia en México. Es aceptable porque el modelo solo recibe hechos sin PII.
- **Red:** VPC aislada con endpoints solo en prod (unos USD 44 al mes); en dev no hay VPC.
- **Auditoría:** COMPLIANCE de 10 años en prod y GOVERNANCE de 1 día en dev, para permitir el teardown.
- **Infraestructura:** CDK en Python, 3 stacks, cdk-nag 3 limpio y un inventario aprobado de permisos IAM.

## 5. Por qué LangGraph (y solo ahí)

- Diagrama generado desde el código (`docs/grafo_redaccion.mmd`): redactar → verificar → corregir (máximo 2) → plantilla.
- El flujo lo decide el código (a diferencia de Bedrock Agents o CrewAI). El estado se audita en cada nodo y cada ruta se prueba en local con un cliente guionado.
- No orquesta, no guarda estado entre invocaciones ni lleva checkpointer: eso lo hace Step Functions.
- Bedrock se llama con boto3 Converse directo, sin abstracciones de LangChain.

## 6. Seguridad en capas

- **SOL-0006**: la inyección en un concepto se detecta (S01), no se obedece y no se cita.
- **SOL-0007**: la PII nunca llega al modelo, al memo, a los logs ni a la auditoría (canarios).
- **SOL-0003**: el RFC no se "corrige"; se reporta.
- **Guardrail obligatorio por IAM**: `Deny` de `InvokeModel` sin el guardrail propio. Contra el guardrail desplegado, 5 de 5 casos de prueba correctos.
- **Revisor**: la identidad la impone IAM (`revisiones/${aws:userid}/`), no el cliente.
- **Secretos**: ninguno en el repo, el pipeline ni las Lambdas; OIDC sin llaves.

## 7. Calidad medida y el humano en el ciclo

- **Evals de los 8 casos en cada PR** (indicadores, alertas, verificador, PII, inyección). El candado de prompt y modelo exige una corrida real aprobada (`evals/aprobaciones.json`).
- **Evals reales con Sonnet 4.6 y el guardrail desplegado**: 36/40, luego 38/40 y finalmente **40/40**.
  - Cada falla tenía una causa medible y corregida: el tema del guardrail era demasiado amplio, y el verificador leía "A03" como cifra.
  - En la corrida final, 6 de los 8 casos pasaron al primer intento.
- **Revisión**: se registran la aceptación sin cambios, la distancia de edición, el tiempo y la categoría del error. Los errores vuelven como candidatos de eval.

## 8. Escala y costo

- **Costo medido**: USD 0.024 por memo en promedio (40/40, unos 2,400 tokens de entrada y 730 de salida por llamada). Con 300 solicitudes diarias son unos USD 220 al mes en el modelo.
- **Cuellos de botella reales hoy**:
  - Bedrock: 10 solicitudes por minuto para Sonnet 4.6.
  - Lambda: concurrencia de 10 en la cuenta.
  - Ambos se absorben con reintentos con backoff en Step Functions.
  - El siguiente cuello es la revisión humana.
- **Con 10 veces el volumen**: la arquitectura no cambia. Se suben las cuotas, la concurrencia reservada de la redacción se vuelve el regulador y, si hace falta, se agrega SQS antes de `StartExecution`. Resultados en `docs/escalamiento.md`.

## 9. Implementado frente a propuesto

- **Desplegado en dev:** todo el corte vertical: datos, flujo, guardrail, revisión, auditoría, alarmas, dashboard y presupuesto.
- **Escrito sin ejecutar:** los workflows de GitHub Actions y el stack de OIDC (falta el repositorio). Prod se sintetiza y se prueba, pero no se desplegó.
- **Propuesto:** Sonnet 5.5 (sin acceso en la cuenta), caché del prompt, Macie, extracción de respaldo con LLM, app web y multicuenta.
- **Detalle:** `docs/implementado_vs_propuesto.md`.

## 10. v2: copiloto del analista

- LangGraph en AgentCore Runtime con herramientas de solo lectura: evidencia de una cifra, comparación con casos previos y preguntas sobre el expediente.
- El mismo guardrail, sin acceso a PII y **sin capacidad de decidir ni de escribir en el memo**.
- Reusa el contrato `HechosParaLLM` y el verificador. El memo nunca copia texto con forma de instrucción, así que la inyección de segundo orden queda cubierta.
