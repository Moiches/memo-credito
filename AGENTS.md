# Reglas para asistentes de código (y personas)

Este repositorio genera borradores de memos de crédito para PyMEs. Las cifras deben ser exactas y
reproducibles, cada memo debe ser auditable y los documentos contienen datos sensibles. Antes de cambiar
algo, lee `docs/plan_implementacion.md` (secciones 5, 8 a 10 y 16) y los ADR de `docs/adr/`.

## Reglas obligatorias

1. **Solo `Decimal`** para montos e indicadores. Prohibido `float` en `dominio/`, `calculos/` y `memo/`;
   `tests/arquitectura` lo verifica. Las únicas excepciones son las coordenadas de PDF (`extraccion/pdf_util.py`)
   y la emisión de métricas.
2. **Nada de texto crudo al LLM.** El modelo solo recibe `HechosParaLLM` (`memo/hechos.py`). Nunca le pases
   conceptos de movimientos, líneas de documentos ni campos de contacto.
3. **Nada de PII en logs.** Usa `observabilidad.logger`. Nunca registres un modelo de dominio completo,
   contenido de documentos, conceptos, nombres, RFC ni datos de contacto.
4. **Toda alerta nueva** lleva código en `dominio/alertas.py`, un test positivo, uno negativo y su caso en
   `evals/casos.yaml`.
5. **No cambies valores esperados** (`evals/casos.yaml`, goldens) sin justificarlo en el PR y sin aprobación
   de CODEOWNERS.
6. **Ningún permiso de IAM nuevo** sin su test en `tests/infra/`.
7. **LangGraph solo para el ciclo del LLM** (`memo/grafo.py`): no orquesta el flujo, no guarda estado entre
   invocaciones y no lleva checkpointer. Step Functions orquesta todo lo demás.
8. **Bedrock se llama solo con boto3 Converse** en `llm/bedrock.py`. Prohibido `langchain_aws`,
   `ChatBedrockConverse` o cualquier otra abstracción de LangChain.
9. **Todo cambio al grafo** exige `uv run python tareas.py diagrama`; el CI falla si `docs/grafo_redaccion.mmd`
   difiere del generado.
10. **Todo cambio en `prompts/` o del modelo** exige `uv run python tareas.py evals-real` y actualizar
    `evals/aprobaciones.json`; el CI verifica el candado.
11. **Dependencias nuevas** solo con justificación en el PR y `uv lock`; nada sin versión fijada.
12. Corre `uv run python tareas.py check` antes de cada commit.

## Mapa rápido

| Carpeta | Responsabilidad |
|---|---|
| `src/memo_credito/dominio` | Modelos Pydantic inmutables, dinero, catálogo de alertas (sin AWS ni frameworks) |
| `src/memo_credito/extraccion` | Parsers deterministas con procedencia y seguridad de entrada |
| `src/memo_credito/validacion` | Conciliación, periodos y reglas que generan alertas |
| `src/memo_credito/calculos` | Indicadores y hechos derivados (funciones puras) |
| `src/memo_credito/memo` | Hechos para el LLM, plantilla, ensamblador, verificador y grafo LangGraph |
| `src/memo_credito/llm` | Puerto `ClienteLLM`, cliente simulado, guionado (tests) y Bedrock |
| `src/memo_credito/auditoria` | Paquete de auditoría con JSON canónico y cadena de hashes |
| `src/memo_credito/handlers` | Lambdas delgadas |
| `infra/` | CDK en Python (stacks, stage, cdk-nag) |

## Comandos

`uv run python tareas.py check | evals | procesar | synth | deploy --entorno dev --perfil <perfil>`
(o `make <objetivo>` donde haya make).
