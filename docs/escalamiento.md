# Escalamiento y prueba de carga

Resultados medidos en la cuenta de dev (`us-east-1`) el 2026-11-25 con `scripts/carga.py`. El script clona los 8 expedientes del reto con folios `LT-<corrida>-<n>`, sube documentos y manifiesto con checksum y mide desde la subida hasta `pendiente_desde`, el instante en que la Lambda deja el borrador en `PENDIENTE_REVISION`.

## Cuotas reales de la cuenta

| Recurso | Valor aplicado | Necesario con 300 solicitudes diarias (pico de ~1 por minuto) | Con 10× (~10 por minuto) |
|---|---|---|---|
| Concurrencia de Lambda (`L-B99A9384`) | **10**; se pidió 1,000 y el caso está abierto | Ráfagas de 5 a 10 | Cientos en ráfaga |
| Bedrock Sonnet 4.6, solicitudes por minuto (`L-00FF3314`) | **10** | ~2 (2 llamadas por memo) | ~20: pedir 50 |
| Bedrock Sonnet 4.6, tokens por minuto (`L-15B8E632`) | 6,000,000 | ~10K | ~100K |
| Step Functions, DynamoDB on-demand y S3 | Holgados | — | — |

Con estas cuotas, el límite efectivo lo dan las **10 solicitudes por minuto** de Bedrock (unos 5 memos por minuto con LLM real) y la concurrencia de 10 de Lambda. Ninguno de los dos se nota con el volumen actual.

## Resultados

| Escenario | Carga | Modo | Resultado | Latencia hasta el borrador |
|---|---|---|---|---|
| E1 humo | 8 simultáneas | Simulado | 8/8 | p50 20.9 s, p95 23.2 s (cota superior; medición anterior al cambio a `pendiente_desde`) |
| E2 ráfaga, 1.ª corrida | 100 en ~10 s | Simulado | **85/100**; 15 en `ERROR` | p50 32.8 s, p95 86.7 s |
| E2 ráfaga, 2.ª corrida (más reintentos en el Map) | 100 en ~10 s | Simulado | **87/100**; 13 en `ERROR` | p50 33.3 s, p95 75.5 s |
| E2 ráfaga, 3.ª corrida (reintento en `EsperarRevision`) | 100 en ~10 s | Simulado | **100/100** | p50 30.3 s, p95 41.8 s, máx. 46.8 s |
| E4 real en Lambda | SOL-0006 | Bedrock (Sonnet 4.6, guardrail) | Origen `llm`, USD 0.0458 | 37 s |
| Integración | SOL-0001, 0004, 0006 y 0007 con revisión completa | Simulado | 7 pasan, 2 omitidos (requieren el rol restringido) | 39 s en total |

## Qué se aprendió de la carga

1. **El throttling de Lambda sí se absorbe, pero cada tarea necesita su reintento.**
   - Las 13 a 15 ejecuciones fallidas murieron en `EsperarRevision`. Era la única tarea con la integración `lambda:invoke.waitForTaskToken` y no tenía reintento para `Lambda.TooManyRequestsException`.
   - Los 47 throttles del Map sí se absorbieron con sus reintentos.
   - Se agregó el reintento (5 s, backoff 2, tope de 60 s, 12 intentos). Es seguro, porque si la invocación fue rechazada el token no llegó a guardarse.
   - Hay una prueba en `tests/infra` que lo exige.
2. **El throttling de Bedrock retrasa, no degrada.**
   - En la primera corrida de evals reales hubo 21 a 27 throttles por minuto con la cuota de 10 RPM. Un memo procesado en Lambda en ese momento salió con plantilla.
   - Ahora `redactar` propaga `LLMNoDisponible` y Step Functions reintenta: 30 s, 60 s, 120 s y 240 s.
   - Los evals reales usan un limitador de 6 solicitudes por minuto, con 0 throttles medidos.
3. **El tiempo de punta a punta lo domina la cola de Lambda**, no el cómputo: un memo aislado tarda unos 18 s, y en ráfaga 30 s de mediana. La ráfaga de 100 necesita unos 1,200 segundos-Lambda, es decir, al menos 2 minutos con concurrencia de 10.

## Qué cambia con 10 veces el volumen o con formatos de banco nuevos

- **Cuotas:** concurrencia de Lambda a 1,000 (ya pedida) y Bedrock a 50 solicitudes por minuto para Sonnet 4.6, o la cuota equivalente del modelo de producción.
- **Regulador:** la concurrencia reservada de `redactar` (5 en prod).
- **Ráfagas mayores que la cuota:** SQS entre `ingesta` y `StartExecution`, con `MaximumConcurrency` en el consumidor.
- **Costo:** lineal. En la corrida final de evals (40/40) el promedio fue de USD 0.024 por memo con Sonnet 4.6 (1.0 llamadas en 6 de 8 casos). Eso da unos USD 220 al mes en el modelo con 300 diarias y unos USD 2,200 con 10×, más unos USD 0.003 por memo de guardrail. Palancas: caché del prompt, un modelo más barato validado con los evals y batch para reprocesos nocturnos.
- **Revisión humana:** con 3,000 memos diarios es el cuello real. Se prioriza por severidad de las alertas y se mide `TiempoRevisionMin`.
- **Formatos de banco nuevos:**
  - Registro de parsers por huella de layout, cada uno con su golden.
  - La conciliación al centavo es el verificador universal.
  - Un formato desconocido termina en `FORMATO_NO_SOPORTADO`, en la cola humana, nunca con cifras inventadas.

## Cómo repetir

```bash
uv run python tareas.py carga --entorno dev --perfil <perfil> --n 100 --modo simulado
```

Los folios `LT-*` son sintéticos. Sus objetos de `entrada/` y `trabajo/` expiran por ciclo de vida, y la auditoría de dev vence en 1 día.
