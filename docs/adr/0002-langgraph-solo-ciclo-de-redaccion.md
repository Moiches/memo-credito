# ADR-0002: LangGraph solo para el ciclo de redacción

- **Estado:** aceptada (2026-11-25)
- **Contexto:** la redacción del memo es un ciclo evaluador-optimizador: el LLM redacta, un verificador determinista revisa, el LLM corrige (como máximo 2 veces) y, si no pasa, sale la plantilla.
- **Decisión:** implementar ese ciclo como un grafo de LangGraph 1.x (`src/memo_credito/memo/grafo.py`) dentro de la Lambda de redacción.
  - Estado tipado con Pydantic, frozen.
  - Nodos: `redactar`, `verificar`, `corregir` y `plantilla`.
  - Aristas condicionales con el límite de correcciones.
  - Sin checkpointer, sin herramientas y con LangSmith desactivado.
  - Step Functions sigue orquestando todo el flujo, incluida la espera humana.
  - Los nodos llaman a Bedrock con boto3 Converse directamente (`llm/bedrock.py`), sin abstracciones de LangChain.
- **Por qué LangGraph:**
  - El flujo lo decide el código, no el modelo (a diferencia de Strands, Bedrock Agents o CrewAI).
  - El estado queda registrado en cada nodo: cada intento guarda petición, respuesta, tokens, costo y errores del verificador.
  - Cada nodo y cada ruta se prueban con pytest en local, con un cliente guionado y sin red (`tests/unit/test_grafo.py`).
  - El diagrama sale del código (`docs/grafo_redaccion.mmd`) y el CI falla si se desactualiza.
  - El mismo patrón sirve para la extracción de respaldo (P2) y para el copiloto de la v2 en AgentCore.
  - La versión 1.x es estable; está fijada en 1.2.24.
- **Lo que reconocemos:** para un solo ciclo bastaría Python simple. Elegimos LangGraph por la trazabilidad por nodo, las rutas probables de forma aislada, el diagrama generado y la reutilización del patrón.
- **Costos aceptados:** las dependencias transitivas (`langchain-core`, `langsmith`). Mitigación: versiones fijadas, `LANGSMITH_TRACING=false` y sin salida a internet en prod (VPC). Las tipificaciones de `add_node` requieren `type: ignore` puntual.
