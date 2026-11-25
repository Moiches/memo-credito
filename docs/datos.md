# Datos del reto: verificación, hallazgos y supuestos

Verificado el 2026-11-25 con un parser propio e independiente (pdfplumber + `Decimal`). El script y su salida
están en `docs/anexos/verificacion_datos/`; el parser definitivo (`src/memo_credito/extraccion`) reproduce los
mismos resultados y los tests de regresión los fijan (`evals/casos.yaml`).

## Confirmado

- Los valores de la tabla de hallazgos coinciden al centavo en los 8 casos, incluido "no calculable" en SOL-0004.
- En los 23 estados de cuenta, la suma de movimientos coincide con los totales, SI + abonos − cargos = SF, el
  saldo cuadra renglón por renglón y los meses encadenan.
- SOL-0005, julio: 2 páginas y 36 movimientos; el último está en la página 2.
- SOL-0006, 2026-08-18: abono de $189,680.97 con un concepto de 4 líneas que contiene texto dirigido al
  "sistema de análisis" (inyección de prompt). El movimiento cuenta en los totales.
- SOL-0003: los EEFF dicen `TCG150817KP7`; los demás documentos, `TCG150817KP2`.
- SOL-0004: no hay agosto y el JSON no lo lista. El SF de julio es igual al SI de septiembre (718,204.26), así que el encadenamiento no detecta el hueco.
- SOL-0007: bloque `representante_legal` con nombre, CURP, fecha de nacimiento, teléfono, domicilio y estado civil.
- Las 8 constancias están en estatus ACTIVO y fueron emitidas el 2026-10-05; los 8 EEFF son internos y sin dictaminar, con cierre al 31-dic-2025.
- El único texto blanco son encabezados de tabla sobre rectángulos azul oscuro.

## Hallazgos adicionales

| # | Hallazgo | Impacto |
|---|---|---|
| H1 | La página 2 de SOL-0005 repite el encabezado de la tabla | El parser detecta columnas por página y omite esa fila |
| H2 | En el concepto multilínea los montos van en la primera línea; las continuaciones no tienen fecha | Las líneas sin fecha se agregan al movimiento anterior |
| H3 | El dígito verificador del RFC (algoritmo SAT, validado con `SAT970701NN3`) falla en los 9 RFC del set | Solo se valida el formato y la fecha embebida |
| H4 | SOL-0006 inició operaciones el 2016-02-29 | La antigüedad compara (mes, día), nunca `date.replace(year=…)` |
| H5 | Correos con caracteres no ASCII en SOL-0004, 0005 y 0008 | `contacto` es PII opaca, sin validador de email estricto |
| H6 | Balance y estado de resultados cuadran en los 8 casos | Esas validaciones no generan falsos positivos |
| H7 | La capacidad con entradas redondeadas o sin redondear da lo mismo en los 8 casos | Se usa la fórmula reproducible a mano |
| H8 | Pie de página gris en cursiva en cada página | Se descarta por fuente y posición |
| H9 | Ningún abono supera 1.92 veces la mediana de su caso | Umbral de depósito atípico: 3 veces la mediana (cero en el set) |

## Supuestos (configurables en `config/base.yaml`)

| Supuesto | Valor |
|---|---|
| Últimos 3 meses | Los 3 meses calendario completos anteriores al mes de `fecha_solicitud` |
| Vigencia de la constancia | ≤ 30 días antes de la solicitud y nunca después |
| Ejercicio esperado de los EEFF | Año de la solicitud − 1 |
| Tasa | 24% anual, i = 0.02 exacto |
| Redondeo | `ROUND_HALF_UP` a 2 decimales al final de cada indicador; la capacidad usa los valores publicados |
| Umbrales de riesgo | Capacidad < 1.00 (alta), < 1.25 (media); apalancamiento ≥ 2.00 (media) |
| Depósito atípico | Abono > 3 × mediana de los abonos de los 3 meses |
| Límites | PDF ≤ 5 MB y ≤ 10 páginas; JSON ≤ 64 KB; ≤ 12 archivos; texto libre ≤ 200 caracteres |
