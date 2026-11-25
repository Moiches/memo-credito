# ADR-0005: Bedrock con boto3 Converse directo, structured outputs y sin temperatura

- **Estado:** aceptada (2026-11-25)
- **Contexto:** se necesita control exacto de la petición (guardrail, esquema de salida, `maxTokens`, `effort`) y guardar la petición y la respuesta crudas para auditoría, sin abstracciones que la modifiquen.
- **Decisión:**
  - Solo `llm/bedrock.py` llama a `bedrock-runtime`, con `converse`. `outputConfig.textFormat` lleva el JSON Schema `salida_v1`, `maxTokens` es explícito y `guardrailConfig` lleva trace.
  - Prohibidos `langchain_aws` y `ChatBedrockConverse`; `tests/arquitectura` lo verifica.
  - Sin `temperature`, `top_p` ni `top_k`, que los modelos recientes ya no aceptan. La estabilidad viene del esquema, el verificador y los evals de 5 corridas (objeción 2 del plan).
  - Reintentos `adaptive` (3) y timeouts explícitos: connect de 5 s y read de 60 s.
- **Consecuencias:**
  - La auditoría guarda exactamente lo enviado y lo recibido.
  - Cambiar de modelo es editar `config/base.yaml` y volver a aprobar el candado de evals (regla 10).
