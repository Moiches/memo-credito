# Arquitectura implementada

Este documento describe lo que está en el código y desplegado en dev. El razonamiento completo está en `docs/plan_implementacion.md` (§5 y §17) y en los ADR.

## Flujo

```mermaid
flowchart TB
  subgraph Entrada
    CLI["memo enviar (perfil SSO)"] -->|"documentos con checksum SHA256 y _manifest.json al final"| S3E[("S3 datos · entrada/{folio}/")]
  end
  S3E -->|"EventBridge: Object Created _manifest.json"| ING["ingesta<br/>folio y nombres válidos, sha256 contra manifiesto,<br/>dedupe condicional en DynamoDB"]
  ING -->|"StartExecution {folio}-{hash16}[-rN]"| SFN
  subgraph SFN["Step Functions Standard · memo-credito-{env}-flujo"]
    INI["Inicializar · RegistrarInicio"] --> MAP["Map ExtraerDocumentos<br/>(MaxConcurrency 5/10)"]
    MAP --> VAL["ValidarYCalcular"]
    VAL --> RED["RedactarMemo<br/>retry LLMNoDisponible 30s×2^n"]
    RED -->|Catch| PLA["RedactarConPlantilla"]
    RED --> ESP["EsperarRevision<br/>waitForTaskToken · timeout = SLA"]
    PLA --> ESP
    ESP -->|States.Timeout| ESC["EscalarRevision (SNS) → Choice"]
    ESC --> ESP
    ESP --> REG["RegistrarRevision"] --> OK(["Éxito"])
    INI & MAP & VAL & PLA & REG -.->|Catch| ERR["MarcarError → NotificarError → Fallo"]
  end
  RED <-->|"Converse + guardrail v2"| BR["Bedrock · perfil de aplicación<br/>us.anthropic.claude-sonnet-4-6"]
  ANA["Analista (perfil SSO)"] -->|"revisiones/${aws:userid}/{folio}/{sha}.json"| S3R[("S3 datos · revisiones/")]
  S3R -->|EventBridge| APL["aplicar_revision<br/>esquema, estado, hash vigente, escritura condicional"]
  APL -->|SendTaskSuccess| ESP
  VAL & RED & REG --> AUD[("S3 auditoría · Object Lock<br/>00..04 + 99_manifiesto con cadena de hashes")]
  ING & VAL & RED & ESP & APL & REG --> DDB[("DynamoDB solicitudes<br/>ESTADO y REVISION#ts · GSI por-estado")]
```

## Estados de una solicitud

`RECIBIDA` → `PROCESANDO` → `MEMO_GENERADO` → `PENDIENTE_REVISION` → `REVISION_RECIBIDA` → `LISTO_PARA_COMITE` o `REQUIERE_CAMBIOS`.

Las salidas por excepción son:

- `ERROR`, con `motivo`;
- `REVISION_VENCIDA`, cuando se agotan los escalamientos.

No existe ningún estado "aprobado".

## Componentes

| Componente | Responsabilidad | Detalles |
|---|---|---|
| `ingesta` | Valida el manifiesto y arranca la ejecución | Asíncrona desde EventBridge, con DLQ y destino de fallo; interruptor `modo_llm` (override solo en dev) |
| `extraer` | Una iteración del Map por documento | Verifica el sha256 antes de parsear; los rechazos no detienen el flujo |
| `validar_calcular` | Expediente, validaciones, alertas, indicadores en `Decimal` y hechos | Escribe las partes 00 a 02 de la auditoría |
| `redactar` | Grafo de LangGraph con Bedrock o simulado, verificador y ensamblado | Idempotente (Powertools); propaga el throttling para que reintente Step Functions; parte 03 |
| `esperar_revision` | Guarda el task token y deja `PENDIENTE_REVISION` | Tabla cifrada con KMS |
| `aplicar_revision` | Valida la revisión y envía el token | La identidad sale de la clave impuesta por IAM |
| `registrar_revision` | Métricas de retroalimentación, candidatos de eval, estado final | Partes 04 y 99; memo final en la auditoría solo si cambió y no trae PII |

## Datos y clasificación

| Lugar | Contenido | Retención |
|---|---|---|
| `datos/entrada/` | Documentos originales y manifiesto (PII) | 7 días en dev y 90 en prod; versiones previas, 7 días |
| `datos/trabajo/` | Extracciones, contexto, memo y memo final | 30 días en dev y 365 en prod |
| `datos/revisiones/` | Revisiones del analista | 30 días en dev y 365 en prod |
| `auditoria/` | Paquete sin PII (hashes y proyección) | Object Lock: GOVERNANCE de 1 día en dev y COMPLIANCE de 3,650 días en prod |
| DynamoDB `solicitudes` | Estado, hashes, token y revisiones (sin PII) | PITR |
| Logs | JSON sin PII, con `correlation_id` | 14 días en dev y 180 en prod; data protection en prod |

## Seguridad (resumen)

- **Cifrado:** una llave KMS por entorno, con rotación, para S3, DynamoDB, SNS, SQS y logs. TLS obligatorio en buckets y tópicos.
- **Permisos por función:** cada Lambda tiene su rol con acciones explícitas. El inventario está aprobado en `tests/infra/permisos_esperados.json` (regla 6) y cdk-nag 3 está limpio.
- **Bedrock:** solo `redactar` lo invoca, y un `Deny` exige el guardrail propio.
- **Personas:** políticas administradas para analista, comité, auditor y operación (§16.2), con `${aws:userid}` para el analista.
- **Red de prod:** subredes aisladas sin NAT, endpoints de gateway y de interfaz, y egreso solo por el puerto 443.
