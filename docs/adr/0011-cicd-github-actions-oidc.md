# ADR-0011: GitHub Actions con OIDC, el mismo cdk.out en dev y prod, y bootstrap con calificador propio

- **Estado:** aceptada (2026-11-25)
- **Contexto:** se requieren despliegues sin llaves de larga duración, con aprobación manual de prod y con trazabilidad de qué se desplegó.
- **Decisión:**
  - OIDC con `sub` por environment (`repo:<org>/<repo>:environment:dev|prod`). Los roles de despliegue solo pueden asumir los roles de bootstrap de CDK. `gh-pruebas-dev` limita el plano de datos de dev a los folios `IT-*` y `LT-*`.
  - Un solo synth en `build` y el mismo ensamblado en dev y prod. Prod exige la integración en verde y los revisores del environment.
  - Higiene del pipeline:
    - acciones fijadas por SHA;
    - `permissions` mínimos por job;
    - sin secretos (los ARN van en `vars`);
    - PRs de forks sin credenciales;
    - inputs de `workflow_dispatch` pasados por variables de entorno.
  - Bootstrap con el calificador `memocred` (`CDKToolkit-memocred`). La cuenta tenía un rol huérfano del bootstrap por defecto; el calificador propio aísla el proyecto sin tocar recursos ajenos.
- **Ajustes al conectar el repositorio real (2026-11-25):**
  - GitHub emite el `sub` con IDs inmutables (`repo:<owner>@<owner_id>/<repo>@<repo_id>:...`). La confianza exige ese sujeto exacto (`-c repo_sub=...`), con lo que un repositorio recreado con el mismo nombre no hereda permisos.
  - En repositorios privados, los revisores obligatorios dependen del plan de GitHub. `deploy-prod` solo corre con la variable `PROD_HABILITADO=true`, así que prod nunca se despliega por un push sin una decisión explícita.
- **Consecuencias:**
  - Rollback con `workflow_dispatch` y `ref=<sha>`.
  - El rol de ejecución de CloudFormation del bootstrap usa `AdministratorAccess`, el valor por defecto. Para prod se recomienda `--cloudformation-execution-policies` con una política acotada.
