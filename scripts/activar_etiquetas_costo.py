"""Activa las etiquetas de asignación de costos del proyecto en Billing (proyecto, entorno, centro-de-costo).

AWS solo permite activarlas después de que aparecen en la facturación (hasta 24 h tras el primer despliegue).
Sin activarlas, el presupuesto filtrado por `user:proyecto` y Cost Explorer por etiqueta quedan en cero.
"""

from __future__ import annotations

import sys

import boto3
from botocore.exceptions import ClientError

ETIQUETAS = ["proyecto", "entorno", "centro-de-costo"]


def main() -> int:
    ce = boto3.client("ce", region_name="us-east-1")
    try:
        r = ce.update_cost_allocation_tags_status(
            CostAllocationTagsStatus=[{"TagKey": t, "Status": "Active"} for t in ETIQUETAS]
        )
    except ClientError as exc:
        print(f"no se pudieron activar: {exc.response.get('Error', {}).get('Code')}")
        return 1
    for error in r.get("Errors", []):
        print(f"{error.get('TagKey')}: {error.get('Code')} {error.get('Message')}")
    print("activas:", [t for t in ETIQUETAS if t not in {e.get("TagKey") for e in r.get("Errors", [])}])
    return 0


if __name__ == "__main__":
    sys.exit(main())
