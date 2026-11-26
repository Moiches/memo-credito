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
- **Modelo para el camino real**: Claude Sonnet 4.6 y Haiku 4.5 sí responden con el perfil `us.`. Se usa Sonnet 4.6 y Sonnet 5.5 queda como objetivo configurable (ADR-0006).
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

### Implementación de la infraestructura y despliegue en dev

- **Bootstrap de CDK**:
  - La cuenta tenía el rol huérfano `cdk-hnb659fds-cfn-exec-role-…` de un bootstrap anterior, y la CLI exigía importarlo.
  - En lugar de borrar recursos ajenos, se usó un calificador propio: `memocred`, con el stack `CDKToolkit-memocred` (ADR-0011).
  - El stack vacío que dejó el primer intento fallido (`REVIEW_IN_PROGRESS`, 0 recursos) se borró.
- **cdk-nag 3.1**:
  - Ya no tiene `NagSuppressions`: usa la API nativa `Validations.of(...).acknowledge(...)`.
  - Se verificó con una violación deliberada que el synth falla con `ValidationFailed`.
  - `tests/infra` limita las reglas que se pueden reconocer.
- **Validación de esquema de la CLI de CDK**: la descripción del perfil de inferencia no admite acentos ni paréntesis (patrón `^([0-9a-zA-Z:.][ _-]?)+$`). Se corrigió y quedó una prueba.
- **Guardrail desplegado**: `scripts/probar_guardrail.py` dio 5 de 5.
  - Intervino ante la recomendación de crédito, la CURP, el correo y el ataque de prompt.
  - Dejó pasar el texto limpio con marcadores.
- **Bug encontrado por el smoke**:
  - `LambdaInvoke` con `payload_response_only` y `TaskInput.from_json_path_at("$")` se renderiza como `"Parameters": "$"`, y la Lambda `extraer` recibía el string `"$"`.
  - Las pruebas con moto llamaban a los handlers directamente y no lo detectaron.
  - Se corrigió y se agregó una prueba de regresión sobre la definición sintetizada.
- **Smoke en dev**:
  - SOL-0001 (reproceso `-r1`) llegó a `PENDIENTE_REVISION` en unos 18 s con el LLM simulado.
  - La revisión con la CLI (`descargar` y `revisar`) terminó en `LISTO_PARA_COMITE`, con `revisor_id` igual al `aws:userid` del analista.
- **Cuota real de Bedrock**:
  - Sonnet 4.6 tiene 10 solicitudes por minuto (`L-00FF3314`); los tokens no limitan (6M TPM).
  - La primera corrida de evals reales y una prueba con Bedrock en Lambda coincidieron: 21 a 27 throttles por minuto. El memo de la Lambda salió con plantilla, que es el respaldo diseñado.
  - Cambios:
    - En Lambda el throttling se propaga y Step Functions reintenta con backoff largo antes de la plantilla.
    - Los evals reales usan un limitador de ritmo (`--rpm`, 6 por defecto).
    - El aviso del memo incluye el código de AWS.
  - La corrida de evals afectada se detuvo, porque no serviría como aprobación del candado.
- **Windows**: Git Bash convierte `/memo-credito/...` en una ruta de Windows al llamar a la CLI de AWS. Se usa `MSYS_NO_PATHCONV=1`; en el CI de Linux no aplica.
- **Bedrock dentro de la Lambda**:
  - SOL-0006, clonado como `IT-BEDROCK2-0006`, llegó a `PENDIENTE_REVISION` en 37 s con `origen=llm` y costo de USD 0.0458. Se ejercitaron el perfil de inferencia de aplicación, el guardrail y el `Deny` de IAM.
  - La alerta S01 aparece con su ubicación, y la prosa no cita el texto inyectado.
- **Integración contra dev**: 7 pasan y 2 se omiten; los negativos de permisos requieren el rol restringido `gh-pruebas-dev`.
- **Carga**:
  - E1: 8 de 8.
  - E2 (100 en ráfaga): 85 de 100 y luego 87 de 100. Las fallas estaban en `EsperarRevision`, la única tarea `waitForTaskToken`, que no tenía reintento para `TooManyRequestsException`. Corregido: 100 de 100, con p50 de 30.3 s y p95 de 41.8 s (`docs/escalamiento.md`).
- **Evals reales con ritmo controlado (6 RPM), primera corrida: 36 de 40, USD 1.39, sin throttling.**
  - Diagnóstico: el **tema denegado del guardrail** bloqueó salidas legítimas de SOL-0002 tres veces seguidas.
  - Se recolectaron 6 salidas reales sin guardrail y se probaron con `ApplyGuardrail`: 3 de 6 eran falsos positivos. El clasificador reaccionaba a frases como "superando el umbral de holgura" y "se sugiere al comité revisar…".
  - Además, un bloqueo de salida deja al corrector a ciegas: solo recibe `stopReason=guardrail_intervened`, regenera y vuelve a chocar.
  - Cambios:
    1. Tema acotado a decisiones explícitas (aprobar, rechazar, autorizar, otorgar o negar). "Sugerir" y "viable" los sigue cubriendo el léxico del verificador, que sí permite corregir.
    2. Error `guardrail_salida` con una instrucción accionable según la política, sin contenido.
    3. Regla 6 del prompt: los puntos para el comité se formulan como verificaciones neutras.
  - Resultado con la versión 2 del guardrail: 0 de 6 falsos positivos, y `probar_guardrail` sigue en 5 de 5 (la recomendación explícita se bloquea).
- **Evals reales, segunda corrida (guardrail v2 y prompt ajustado): 38 de 40, USD 1.09, cero intervenciones del guardrail.**
  - Llamadas por memo: 1.0 en 6 de los 8 casos.
  - Diagnóstico de las 2 fallas (3 corridas de SOL-0004 y 3 de SOL-0006):
    - SOL-0006 pasó las 3; su falla anterior fue ocasional.
    - SOL-0004 disparaba `cifra_literal` y `cifras_memo` en cada primer intento porque el modelo citaba el **código de la alerta** ("…documental (A03)…"), y el "03" se leía como cifra.
    - Como el error nunca incluye el valor, el modelo no sabía qué corregir y en una corrida agotó los intentos.
  - Cambio: los códigos del catálogo se permiten en la prosa, no cuentan como cifras y deben existir en el caso (si no, `alerta_inexistente`). Primero se escribieron 3 pruebas en rojo.
- **Windows**: al pasar por bash, un parche colapsó `\b` a un byte de retroceso dentro de un regex. Se detectó por las pruebas en rojo, se corrigió con `chr(92)` y se agregó un escaneo de caracteres de control en el repo (limpio).
- **Evals reales, corrida final: 40 de 40, USD 0.97, cero intervenciones del guardrail.**
  - 1.0 llamadas por memo en 6 de los 8 casos; SOL-0004 1.8 y SOL-0006 1.2.
  - Candado registrado en `evals/aprobaciones.json`; `memo evals --verificar-candado` está vigente.
- **Versión final desplegada en dev**: integración 7 pasan y 2 omitidos (los negativos requieren el rol de OIDC).
- **Gasto aproximado de Bedrock del día**: unos USD 4 (tres corridas de evals, diagnósticos y pruebas en Lambda).

### Repositorio y CI/CD

- **Repositorio:** `Moiches/memo-credito`, privado, creado con la confirmación explícita del propietario. Antes de publicar se revisaron los archivos versionados: sin credenciales, sin correos y sin el ID de la cuenta.
- **Stack `memo-credito-cicd`:** proveedor OIDC de GitHub y roles `gh-pr-lectura`, `gh-deploy-dev`, `gh-deploy-prod` y `gh-pruebas-dev`. Sus ARN se guardan como secretos del repositorio: no son credenciales (el acceso es por OIDC, sin llaves), pero así GitHub los enmascara y el número de cuenta no aparece en los logs públicos.
- **Sin aprobación por revisores:** el plan de GitHub no admite revisores obligatorios en environments de repositorios privados (HTTP 422). `deploy-prod` queda apagado hasta definir `PROD_HABILITADO=true`, así que un push a `main` nunca despliega prod.
- **Primer `cd`:** falló al asumir el rol. CloudTrail mostró que GitHub emite el `sub` con IDs inmutables (`repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:dev`), no el formato heredado.
  - La confianza ahora exige ese sujeto exacto, que además impide que un repositorio recreado con el mismo nombre herede los permisos.
  - Lo cubre la prueba `tests/infra/test_cicd.py`.
- **Primer `ci`** (manual): `calidad` e `infra` en verde en GitHub. Incluyen lint, tipos, pruebas, evals, candado, pip-audit, gitleaks, el paquete de Lambda y el synth de dev y prod con cdk-nag.
- **PR de Dependabot:** no reciben token OIDC, así que el job `diff` se omite para ellos.
- **Segundo `cd`:** build, despliegue de dev con OIDC (mismo ensamblado que se sintetizó una sola vez) e integración con el rol restringido: 9 de 9 en 44 s, incluidos los negativos de permisos (escribir revisiones como otra identidad, leer expedientes que no son de prueba). Prod se omitió por diseño.
