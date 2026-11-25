# ADR-0006: us-east-1 con perfil geográfico `us.`; Sonnet 4.6 mientras no haya acceso a Claude 5.x

- **Estado:** aceptada (2026-11-25)
- **Contexto:** el plan proponía Sonnet 5.5, pero en esta cuenta los modelos Claude 5.x no están habilitados (cuota aplicada 0). `us.anthropic.claude-sonnet-4-6` sí responde. Los datos son de México, y `mx-central-1` aún no ofrece estos modelos.
- **Decisión:**
  - `us-east-1` con el perfil de inferencia `us.`. Enruta a us-east-1, us-east-2 y us-west-2, verificado con `GetInferenceProfile`. Los permisos IAM sobre el modelo cubren esas tres regiones.
  - `modelo_id: us.anthropic.claude-sonnet-4-6` en `config/base.yaml`, con `modelo_objetivo` igual a Sonnet 5.5 para cuando haya acceso.
  - Perfil de inferencia de aplicación etiquetado (proyecto, entorno, centro de costo) para atribuir el gasto.
  - Guardrail en tier Standard con el perfil `us.guardrail.v1:0`.
- **Consecuencias:**
  - Costo medido de unos USD 0.044 por memo con Sonnet 4.6, con 1 corrección en promedio.
  - Migrar a 5.5 exige rehacer los evals reales y actualizar `evals/aprobaciones.json`.
  - El texto puede procesarse en las tres regiones de EE. UU. Es aceptable porque el LLM solo recibe hechos sin PII (regla 2).
