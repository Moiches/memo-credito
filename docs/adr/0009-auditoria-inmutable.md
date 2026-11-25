# ADR-0009: Auditoría inmutable con Object Lock, cadena de hashes y proyección sin PII

- **Estado:** aceptada (2026-11-25)
- **Contexto:** cada memo debe poder reconstruirse y probarse íntegro años después, pero lo que no se puede borrar no debe contener datos personales (derechos ARCO).
- **Decisión:**
  - Bucket de auditoría con Object Lock y escritura write-once (`If-None-Match: *`):
    - COMPLIANCE de 3,650 días en prod;
    - GOVERNANCE de 1 día en dev, para permitir el teardown con bypass explícito (objeción 6).
  - Partes `00_entrada` … `04_revision` y `99_manifiesto`, con la cadena `h_n = sha256(h_{n-1} + sha256(parte_n))`.
  - La extracción se proyecta reemplazando PII y conceptos por `sha256:` y se puede rehidratar. El análisis recalculado es idéntico byte a byte (prueba de reproducibilidad).
  - El memo final entra a la auditoría solo si cambió y no contiene PII. Si no, queda el hash y el texto vive en `trabajo/`, que sí se puede borrar (objeción 8).
- **Consecuencias:**
  - `memo auditoria <folio>` y las pruebas con moto verifican la integridad.
  - En prod el bucket no se puede vaciar, ni siquiera por un administrador.
  - Los originales tienen ciclo de vida (90 días en prod) y la auditoría no depende de ellos.
