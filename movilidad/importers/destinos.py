"""Importa el catálogo público de destinos de la FIC (export del buscador de MoveON)."""

import csv
import re
from pathlib import Path

from django.db import transaction

from movilidad.importers.adjudicaciones import parse_programa
from movilidad.importers.comun import buscar_o_crear_institucion
from movilidad.models import RelacionDestino


@transaction.atomic
def importar_destinos(ruta) -> dict:
    nuevas = actualizadas = 0
    with Path(ruta).open(encoding="utf-8", newline="") as fh:
        for fila in csv.DictReader(fh):
            institucion = buscar_o_crear_institucion(fila["institucion"], fila["pais"])
            for campo, col in (("ciudad", "ciudad"), ("codigo_erasmus", "codigo_erasmus"), ("web", "web")):
                if fila.get(col) and not getattr(institucion, campo):
                    setattr(institucion, campo, fila[col].strip())
            institucion.save()
            plazas = fila.get("plazas_restantes_2026_27", "")
            _, creada = RelacionDestino.objects.update_or_create(
                oportunidad=fila["oportunidad_movilidad"].strip(),
                defaults={
                    "institucion": institucion,
                    "programa": parse_programa(fila["programa"]),
                    "nivel": fila.get("nivel", ""),
                    "isced": fila.get("isced", ""),
                    "idioma_ensenanza": fila.get("idioma_ensenanza", ""),
                    "requisito_idioma": fila.get("requisito_idioma", ""),
                    "plazas_restantes": int(plazas) if re.fullmatch(r"\d+", plazas or "") else None,
                    "curso_plazas": "2026/27",
                    "observaciones": fila.get("observaciones", ""),
                },
            )
            nuevas += creada
            actualizadas += not creada
    return {"nuevas": nuevas, "actualizadas": actualizadas}
