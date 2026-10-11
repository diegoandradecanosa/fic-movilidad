"""Importa el JSON oficial de equivalencias de calificaciones (Resolución de 18/09/2017).

El JSON (32 MB, público) vive fuera del repositorio, junto a los recursos de la skill
acta-reconocimiento: ToRs26/_recursos_acta/equivalencias_calificaciones_espana.json.
"""

import json
from pathlib import Path

from django.db import transaction

from movilidad.models import EscalaNotas
from movilidad.paises import codigo_pais

# Nombres del JSON que no coinciden con los de paises.py.
ISO_EXTRA = {
    "REPUBLICA DE COREA": "KR",
    "ESTADOS UNIDOS DE AMERICA": "US",
    "REPUBLICA DE SERBIA": "RS",
    "PAISES BAJOS": "NL",
    "REINO UNIDO": "GB",
    "MACEDONIA": "MK",
    "BOSNIA HERZEGOVINA": "BA",
}


def iso_de(pais_json: str) -> str:
    if pais_json in ISO_EXTRA:
        return ISO_EXTRA[pais_json]
    iso = codigo_pais(pais_json.lower())
    return iso if len(iso) == 2 and iso.isupper() else ""


@transaction.atomic
def importar_equivalencias(ruta) -> dict:
    datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    r = {"escalas": 0, "paises": 0, "sin_iso": []}
    for pais, info in datos["paises"].items():
        r["paises"] += 1
        iso = iso_de(pais)
        if not iso:
            r["sin_iso"].append(pais)
        for e in info.get("escalas", []):
            EscalaNotas.objects.update_or_create(
                pais=pais, id_escala=e["id_escala"],
                defaults={
                    "pais_iso": iso,
                    "tipo_nota_origen": e.get("tipo_nota_origen") or "",
                    "num_calificaciones": e.get("num_calificaciones") or len(e.get("calificaciones", {})),
                    "nota_minima_aprobatoria": str(e.get("nota_minima_aprobatoria") or ""),
                    "nota_maxima": str(e.get("nota_maxima") or ""),
                    "calificaciones": e.get("calificaciones", {}),
                },
            )
            r["escalas"] += 1
    return r
