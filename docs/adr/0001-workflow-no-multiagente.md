# ADR-0001: Workflow con núcleo determinista, no multiagente

- **Estado:** aceptada (2026-11-25)
- **Contexto:** cada solicitud trae estados de cuenta, constancia, estados financieros y un JSON. Hay que extraer, validar, calcular indicadores exactos y redactar un memo auditable. El analista y el comité deciden.
- **Decisión:** un workflow fijo (Step Functions) con núcleo determinista: extracción, validación y cálculo hechos con código y `Decimal`. La agencia se limita a un punto: la redacción del memo, como ciclo evaluador-optimizador (redactar → verificar → corregir, como máximo 2 correcciones) con salida de respaldo determinista.
- **Por qué no multiagente:**
  - Tres tipos de documento no son tres agentes: son tres parsers.
  - El orden de los pasos nunca cambia, así que no hay nada que planear en tiempo de ejecución.
  - Los cálculos deben ser exactos y reproducibles: eso es trabajo del código, no de un modelo.
- **Consecuencias:**
  - Costo y latencia predecibles y pruebas deterministas de punta a punta.
  - La flexibilidad del LLM se limita a la prosa, verificada por código.
  - Un formato de banco nuevo requiere un parser nuevo (o la extracción de respaldo con LLM, propuesta P2).
