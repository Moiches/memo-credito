# Envoltorio de tareas.py (en Windows sin make: `uv run python tareas.py <objetivo>`).
ENV ?= dev
PROFILE ?=
N ?= 5
MODO ?= simulado
T = uv run python tareas.py --entorno $(ENV) $(if $(PROFILE),--perfil $(PROFILE)) --n $(N) --modo $(MODO)

.PHONY: setup lint test check evals evals-real procesar diagrama build-lambda synth diff deploy deploy-cicd smoke \
        smoke-bedrock probar-guardrail integracion carga revisar reprocesar activar-etiquetas-costo destroy

setup lint test check evals evals-real procesar diagrama build-lambda synth diff deploy deploy-cicd smoke \
smoke-bedrock probar-guardrail integracion carga revisar reprocesar activar-etiquetas-costo destroy:
	$(T) $@ $(ARGS)
