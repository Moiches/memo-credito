# Registro del proceso

Bitácora de cómo se construyó la solución, cómo se usaron las herramientas de IA y qué se verificó a mano.

## 2026-11-25

### Planeación

- Se leyó el PDF del reto y se descomprimió el zip en una carpeta aislada.
- Un parser independiente (pdfplumber + `Decimal`) verificó al centavo la tabla de hallazgos y encontró 9 hallazgos adicionales (`docs/datos.md`).
- El dígito verificador del RFC se comprobó a mano con el algoritmo del SAT, validado primero con el RFC real `SAT970701NN3`.
- Se consultaron en AWS, solo en lectura, los precios (Price List API), las cuotas (Service Quotas), la configuración de Lambda y los perfiles de inferencia. Así se descubrió que la concurrencia de Lambda es 10.
- Resultado: `docs/plan_implementacion.md` (26 secciones).

### Prerrequisitos (T00)

- **Solicitud de cuota de Lambda**: 1,000 ejecuciones concurrentes (`L-B99A9384`), en estado PENDING.
- **Bedrock, cuotas**: AWS rechazó las solicitudes porque el valor por defecto de las cuotas de TPM de Sonnet 5.5 y Haiku 5.5 es de 6M y 10M; el 0 que se leyó es la cuota aplicada a la cuenta.
- **Bedrock, acceso a modelos**: una llamada mínima confirmó el bloqueo real: **Claude 5.x no está habilitado en la cuenta** ("not available for this account, contact AWS Sales").
- **Modelo para el camino real**: Claude Sonnet 4.6 y Haiku 4.5 sí responden con el perfil `us.`. Se usa Sonnet 4.6 y Sonnet 5.5 queda como objetivo configurable (ADR-0007).
- **Prueba de structured outputs con marcadores**:
  - Sonnet 4.6 usó bien `{{capacidad_pago}}`.
  - Haiku 4.5 escribió `{{1.85}}`, es decir, el valor dentro del marcador. Es justo el error que el verificador determinista detecta y que justifica la compuerta de evals.

### Uso de herramientas de IA

- **Asistente de código (Claude Code)**: generó el plan, el código, las pruebas y la infraestructura.
- **Verificación**: todo valor esperado de los tests se verificó contra el parser independiente y contra el memo de ejemplo; ningún valor esperado salió del código bajo prueba.
- **Verificado a mano**:
  - las cifras de SOL-0001 contra el memo de ejemplo;
  - los caracteres tipográficos del ejemplo (`−` U+2212, `–` U+2013, `·`);
  - el algoritmo del RFC;
  - la disponibilidad real de los modelos en Bedrock.
