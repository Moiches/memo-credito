# Modelo de amenazas

Alcance: el flujo completo, desde la ingesta de documentos hasta la revisión humana y la auditoría, en dev y prod. Los datos del reto son sintéticos, pero el diseño asume datos reales de PyMEs y de sus representantes.

## Activos y fronteras de confianza

- **Activos:**
  - Documentos de la solicitud (`entrada/`), que contienen PII del representante y del contacto.
  - Hechos y memo (`trabajo/`).
  - Paquete de auditoría (bucket con Object Lock).
  - Estado y task tokens (DynamoDB cifrado con KMS).
  - Identidad del revisor.
- **Fronteras:**
  - Del canal de originación a S3.
  - De S3 a las Lambdas, que nunca confían en el contenido de los documentos.
  - Del código al LLM: solo recibe `HechosParaLLM`, nunca texto crudo.
  - Del LLM al memo: la salida pasa por el guardrail y el verificador determinista.
  - Del analista a la revisión: la identidad la impone IAM.
  - De GitHub a AWS: OIDC con `sub` por environment.

## Amenazas, controles y evidencia

| # | Amenaza | Vector | Control | Cómo se prueba |
|---|---|---|---|---|
| 1 | Inyección de prompt en PDFs | Conceptos de movimientos (SOL-0006), líneas extra en los EEFF | El LLM no ve texto crudo; el detector emite S01 y el memo no cita el texto; Guardrail `PROMPT_ATTACK`; verificador | Golden de SOL-0006, ADV-02 y `probar_guardrail` |
| 2 | Inyección en campos libres del JSON | `destino`, `razon_social` | Lista blanca, ≤ 200 caracteres, detector, sustitución en los hechos | ADV-01 |
| 3 | Texto oculto | Blanco sin fondo, fuente diminuta, fuera de la página | Detector S03 | ADV-07 |
| 4 | Fuga de PII | `representante_legal` y `contacto` (SOL-0007) | `extra="ignore"` registrando solo nombres; `PiiStr`; hechos sin PII; regex y PII en el Guardrail; verificador con canarios; logs sin contenido; data protection de logs (P1) | Canarios de SOL-0007 en prompt, memo, logs y auditoría (unitario e integración) |
| 5 | Documentos alterados | Totales manipulados, mes duplicado o faltante, saldos | Conciliación al centavo, periodos explícitos, encadenamiento, sha256 | ADV-03 y ADV-04, SOL-0004 |
| 6 | Documentos maliciosos | PDF con JavaScript, cifrado, enorme | Magic bytes, límites, rechazo de objetos activos, tiempo límite, memoria acotada | ADV-08 |
| 7 | Permisos excesivos | Roles amplios, `*` | Un rol por función, cdk-nag, tests de infraestructura, `Deny` sin guardrail | `tests/infra/`; negativos de integración |
| 8 | Recomendación indebida | El LLM dictamina, por sesgo o inyección | Tema denegado, léxico del verificador, revisión humana obligatoria; el sistema no tiene ningún estado "aprobado" | ADV-09; evals |
| 9 | Suplantación del analista | Firmar una revisión como otra persona | `revisiones/${aws:userid}/` impuesto por IAM; `memo_sha256` | Negativo de integración |
| 10 | Secretos | Llaves en el repo, el pipeline o Lambda | OIDC sin llaves; ningún secreto en variables; gitleaks en pre-commit y CI; API key de Bedrock solo local y de corto plazo (hasta 12 h), en `.env` ignorado | CI; test de infraestructura: ninguna Lambda tiene `AWS_BEARER_TOKEN_BEDROCK` ni nombres tipo `*SECRET*` |
| 11 | Dependencias (equipo con asistentes de código) | Typosquatting, paquete comprometido | `uv.lock` con hashes y `--frozen`, `pip-audit`, Dependabot, CODEOWNERS, regla 11 de AGENTS.md | Job `calidad` |
| 12 | El pipeline de CI/CD | PR malicioso, robo de token | OIDC con `sub` por environment, aprobación manual de prod, acciones fijadas por SHA, `permissions` mínimos, PRs de forks sin credenciales | Revisión de los workflows; tests de la política de confianza |
| 13 | Telemetría de terceros | LangSmith (llega por la dependencia transitiva `langsmith`) | `LANGSMITH_TRACING=false`, sin API key, sin salida a internet en prod (VPC) | `test_sin_telemetria_terceros`; test de infraestructura de variables de entorno |
| 14 | Datos procesados en otras regiones | Inferencia global | Perfil geográfico `us.` y perfil de guardrail `us.` | Test de infraestructura sobre el ARN del perfil |
| 15 | Alteración de la auditoría | Borrar o editar el paquete | Object Lock (compliance en prod), cadena de hashes, CloudTrail | Integración: sobrescritura fallida; `memo auditoria verificar` |
| 16 | Inyección de segundo orden | El memo lo lee otro sistema (copiloto v2) | El memo nunca copia texto con forma de instrucción y la prosa no lleva Markdown ni enlaces | Golden de SOL-0006 y regla `formato_inseguro` |
| 17 | Abuso de costo o DoS | Ráfagas o documentos grandes | Límites de tamaño, `MaxConcurrency`, `maxTokens`, máximo 3 llamadas, alarmas, Budgets | Carga; alarmas |

## Evidencia ejecutada (2026-11-25)

- `tests/adversarial/`: PDFs con JavaScript, cifrados, con texto oculto o totales alterados, inyección en campos libres y en conceptos.
- `tests/unit/test_handlers.py` (moto):
  - Revisión con hash ajeno, doble aplicación y cuerpo fuera del esquema rechazados.
  - Identidad tomada de la clave.
  - Manifiesto alterado y nombres con ruta rechazados.
  - Auditoría write-once y sin canarios de PII.
- `tests/infra/`:
  - cdk-nag sin hallazgos sin reconocer.
  - Inventario aprobado de permisos por rol (regla 6) y ausencia de comodines de servicio.
  - `Deny` de Bedrock sin el guardrail propio.
  - Object Lock por entorno, TLS obligatorio, ausencia de secretos y de `AWS_BEARER_TOKEN_BEDROCK` en las plantillas, y LangSmith apagado.
- `scripts/probar_guardrail.py` contra el guardrail desplegado en dev: 5 de 5 casos correctos.
  - Intervino ante la recomendación de crédito, la CURP, el correo y el ataque de prompt.
  - Dejó pasar el texto limpio con marcadores.
- `tests/integracion/test_dev.py`: los negativos de permisos (escribir como otra identidad, leer expedientes que no son de prueba) solo corren con el rol restringido `memo-credito-gh-pruebas-dev`. Con un perfil de administrador se omiten, y así lo reporta pytest.

## Riesgos residuales

- **Rol de ejecución de CloudFormation:** el bootstrap usa `AdministratorAccess` por defecto. En prod debe acotarse con `--cloudformation-execution-policies`.
- **Retención de los logs de Bedrock:** el guardrail enmascara la respuesta, pero las invocaciones podrían registrarse en texto plano si se habilita el *model invocation logging*. No está habilitado. Si se habilita, debe ir a un bucket cifrado con acceso restringido.
- **Personas:** las políticas se crean en CDK, pero los permission sets de IAM Identity Center son un prerrequisito manual (§16.2 del plan).
- **Canal de originación:** fuera del alcance. En dev, los expedientes los sube un perfil con permisos de escritura en `entrada/`.
