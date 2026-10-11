from django.core.management.base import BaseCommand

from movilidad.importers.equivalencias import importar_equivalencias


class Command(BaseCommand):
    help = "Importa el JSON oficial de equivalencias de calificaciones (Resol. 18/09/2017)."

    def add_arguments(self, parser):
        parser.add_argument("json")

    def handle(self, json, **_):
        r = importar_equivalencias(json)
        self.stdout.write(f"Países: {r['paises']} · escalas: {r['escalas']}")
        if r["sin_iso"]:
            self.stdout.write(self.style.WARNING(
                "Sin código ISO (no se emparejarán con las instituciones): " + ", ".join(r["sin_iso"])))
