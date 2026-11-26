# ADR-0013: Consola local del analista con Streamlit

- **Estado:** aceptada (2026-11-25)
- **Contexto:** el analista operaba con la CLI (`memo enviar`, `pendientes`, `descargar`, `revisar`) y la consola de AWS. Hacía falta una interfaz para enviar expedientes, seguir el flujo de Step Functions, leer el borrador con la evidencia de cada cifra y registrar la revisión, sin romper tres propiedades del diseño: la identidad del revisor la impone IAM (ADR-0008), el LLM nunca recibe texto crudo y los logs no llevan PII.
- **Decisión:**
  - Una app de Streamlit **local** (`app/consola.py`) que corre en el equipo del analista, escucha solo en `localhost` y usa su propio perfil de AWS. No hay backend, API Gateway ni rol compartido: al revisar, la clave sigue siendo `revisiones/<aws:userid del analista>/...`.
  - La lógica vive en `src/memo_credito/ui/`: `formato.py` (funciones puras) y `servicios.py` (operaciones con la sesión del analista). Tiene tipado estricto y pruebas (`tests/unit/test_ui.py`). La vista no tiene reglas de negocio.
  - El cuerpo de la revisión sale de la misma función que usa la CLI (`cli/nube.revision`) y se valida en las pruebas contra `RevisionEntrada`, el modelo de `aplicar_revision`.
  - Streamlit 1.67.0 va en el grupo de dependencias `ui`, con versión fija y en `uv.lock`. No entra a las Lambdas (`--no-default-groups`) ni al CI.
  - Endurecimiento:
    - Telemetría de Streamlit apagada.
    - Archivos de hasta 5 MB, igual que la ingesta.
    - Lo que escribe el solicitante (razón social, destino, nombres de archivo) se escapa antes de mostrarlo como Markdown.
    - El memo se muestra sin HTML.
    - `unsafe_allow_html` solo se usa en el bloque de estilos, que es estático.
    - Los archivos subidos viven en una carpeta temporal que se borra al terminar.
  - Diseño con la skill ui-ux-pro-max:
    - Estilo Minimalism & Swiss, en azul marino con dorado e IBM Plex Sans/Mono.
    - Contraste AA: texto oscuro sobre el dorado de los botones primarios.
    - El color siempre va acompañado de texto, los íconos son vectoriales y se respeta la preferencia de menos movimiento.
- **Por qué Streamlit:**
  - Usa el mismo lenguaje que el núcleo, así que reutiliza la CLI y el pipeline sin una API intermedia.
  - Se construye en horas.
  - Como corre en local, conserva el modelo de identidad sin infraestructura nueva.
- **Alternativas descartadas:**
  - **App web alojada (React o Next.js con API Gateway y Cognito):** es la app web P2 del plan. Exige autenticación, propagar la identidad de cada usuario a IAM, CORS, permisos nuevos y su propio modelo de amenazas.
  - **Streamlit en un servidor compartido:** todos firmarían con el rol del servidor y se perdería la identidad del revisor.
- **Consecuencias y límites:**
  - No es la app web de producción: es una consola local para analistas y demos.
  - Algunos textos nativos de Streamlit siguen en inglés, como los del cargador de archivos.
  - Los ajustes finos de estilo usan CSS sobre los `data-testid` de Streamlit, que pueden cambiar entre versiones. Son tres reglas y se revisan al actualizar Streamlit.
  - El navegador descarga IBM Plex de Google Fonts; esa petición no lleva datos del expediente.
  - Si el rol del analista no puede leer el historial de Step Functions o la auditoría, la consola lo dice y sigue funcionando sin esa vista.
  - El `pip-audit` del CI cubre solo las dependencias de ejecución. Para el grupo `ui` hay que correrlo aparte; el 2026-11-25 no encontró vulnerabilidades:

    ```bash
    uv export --frozen --only-group ui --no-emit-project --format requirements-txt -o req_ui.txt
    uv run pip-audit --require-hashes --disable-pip -r req_ui.txt
    ```
- **Verificación (2026-11-25, dev):** desde la consola se envió un expediente de demostración y se siguió en vivo hasta `PENDIENTE_REVISION`. La revisión `requiere_cambios`, con una calificación y un comentario, se registró desde el navegador. `aplicar_revision` la aceptó y el folio terminó en `REQUIERE_CAMBIOS` con la ejecución en `SUCCEEDED`.
