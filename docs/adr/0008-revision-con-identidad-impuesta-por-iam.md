# ADR-0008: La revisión se escribe en `revisiones/${aws:userid}/` y el sistema envía el token

- **Estado:** aceptada (2026-11-25)
- **Contexto:** si el CLI del analista llamara a `SendTaskSuccess`, la identidad del revisor la declararía el cliente, y otra persona podría firmar por él (objeción 3 del plan).
- **Decisión:**
  - El CLI, autenticado con SSO, escribe la revisión en `revisiones/<aws:userid>/<folio>/<memo_sha256>.json`. La política del analista solo permite `PutObject` en su propio prefijo mediante la variable de política `${aws:userid}`.
  - `aplicar_revision` toma la identidad de la clave, no del cuerpo, y valida:
    - el esquema;
    - que el estado sea `PENDIENTE_REVISION`;
    - que el hash coincida con el borrador vigente.
  - Después hace una escritura condicional y envía el token.
  - Ningún estado significa "aprobado": los finales son `LISTO_PARA_COMITE` y `REQUIERE_CAMBIOS`.
- **Consecuencias:**
  - La firma queda ligada a la identidad (CloudTrail, DynamoDB y auditoría) y al hash exacto del memo revisado.
  - Las revisiones repetidas o de un memo viejo se rechazan y se cuentan en `RevisionesRechazadas`.
