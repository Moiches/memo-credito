# ADR-0010: Evals deterministas en cada PR, candado de prompt y modelo, y evals reales con OIDC

- **Estado:** aceptada (2026-11-25)
- **Contexto:** correr 40 llamadas a Bedrock en cada PR exige credenciales y presupuesto en un contexto de PR (objeción 7 del plan).
- **Decisión:**
  - Cada PR corre los evals de los 8 casos con el LLM simulado: indicadores, alertas, verificador, ausencia de PII y que la inyección no llegue al modelo.
  - `memo evals --verificar-candado` falla si el hash de `prompts/` o el modelo no tienen una aprobación en `evals/aprobaciones.json` (regla 10).
  - `evals-reales.yml`, manual y semanal, corre 5 corridas por caso con Bedrock en dev mediante OIDC y el guardrail desplegado, y falla por debajo del 100%.
- **Consecuencias:**
  - Un cambio de prompt no llega a main sin una corrida real aprobada.
  - Los PR no necesitan credenciales de AWS.
