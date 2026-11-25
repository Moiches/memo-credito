# ADR-0004: Step Functions Standard orquesta; idempotencia por nombre de ejecución

- **Estado:** aceptada (2026-11-25)
- **Contexto:** el proceso dura de segundos (extracción y redacción) a días (revisión humana), debe sobrevivir a reintentos y a eventos duplicados de S3, y dejar un historial auditable.
- **Decisión:**
  - Step Functions Standard con:
    - un Map por documento;
    - un Catch global hacia `MarcarError`;
    - plantilla determinista si la redacción falla;
    - `.waitForTaskToken` con timeout igual al SLA y escalamiento por SNS.
  - El nombre de la ejecución es `{folio}-{input_hash[:16]}[-rN]`. La ingesta deduplica con una escritura condicional en DynamoDB, de modo que un evento repetido no regresa el estado.
  - `redactar` usa la idempotencia de Powertools para no volver a pagar tokens en un reintento.
  - LangGraph no orquesta, no guarda estado entre invocaciones ni lleva checkpointer (ADR-0002).
- **Consecuencias:**
  - Las reejecuciones son seguras y el historial queda en Step Functions.
  - El costo es de unas 20 transiciones por memo (USD 0.025 por 1,000), despreciable frente al LLM.
  - Reprocesar exige un `reproceso` nuevo (`-rN`), explícito y auditable.
