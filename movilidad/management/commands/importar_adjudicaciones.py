from django.core.management.base import BaseCommand

from movilidad.importers.adjudicaciones import importar_adjudicaciones
from movilidad.models import Listado


class Command(BaseCommand):
    help = "Importa el Excel de adjudicaciones de la ORI (provisional o definitiva)."

    def add_arguments(self, parser):
        parser.add_argument("excel")
        parser.add_argument(
            "--listado", choices=["provisional", "definitiva"], default="definitiva"
        )

    def handle(self, excel, listado, **_):
        informe = importar_adjudicaciones(excel, listado=Listado[listado.upper()])
        self.stdout.write(informe.resumen())
