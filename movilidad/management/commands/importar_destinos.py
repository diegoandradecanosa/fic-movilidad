from django.core.management.base import BaseCommand

from movilidad.importers.destinos import importar_destinos


class Command(BaseCommand):
    help = "Importa el CSV de destinos de la FIC (buscador público de MoveON)."

    def add_arguments(self, parser):
        parser.add_argument("csv")

    def handle(self, csv, **_):
        r = importar_destinos(csv)
        self.stdout.write(f"Relaciones nuevas: {r['nuevas']} · actualizadas: {r['actualizadas']}")
