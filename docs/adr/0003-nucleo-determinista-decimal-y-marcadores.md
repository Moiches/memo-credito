# ADR-0003: Núcleo determinista en Decimal y cifras citadas por marcador

- **Estado:** aceptada (2026-11-25)
- **Contexto:** las cifras del memo deben ser exactas y reproducibles. Un LLM que copia o recalcula números puede alterar un dígito sin que se note.
- **Decisión:**
  - Todo monto e indicador es `Decimal` con `ROUND_HALF_UP` y se redondea solo al presentar. `float` está prohibido en `dominio/`, `calculos/` y `memo/`, y `tests/arquitectura` lo verifica.
  - El LLM nunca escribe una cifra: cita `{{id}}` de `HechosParaLLM`, y el ensamblador sustituye el valor ya formateado.
  - El verificador rechaza dígitos y números en palabras, marcadores inexistentes, alertas omitidas o inventadas, lenguaje de recomendación y PII. Sus errores nunca incluyen el valor detectado.
- **Consecuencias:**
  - Una alucinación numérica es imposible por construcción y no depende del prompt.
  - El tono de la prosa queda más rígido. En las corridas reales hizo falta en promedio 1 corrección por memo (`docs/registro_proceso.md`).
  - Las cifras del memo coinciden al centavo con los goldens de `evals/casos.yaml`.
