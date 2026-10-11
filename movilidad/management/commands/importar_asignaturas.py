from django.core.management.base import BaseCommand

from movilidad.importers.asignaturas import importar_asignaturas


class Command(BaseCommand):
    help = "Importa el catálogo de asignaturas de la FIC (CSV de data/)."

    def add_arguments(self, parser):
        parser.add_argument("csv")
        parser.add_argument("--curso", help="Curso académico (2026/27); por defecto, el del nombre del fichero")

    def handle(self, csv, curso=None, **_):
        r = importar_asignaturas(csv, curso)
        self.stdout.write(
            f"Asignaturas nuevas: {r['nuevas']} · actualizadas: {r['actualizadas']} · "
            f"titulaciones nuevas: {r['titulaciones_nuevas']}"
        )
        for c in r["conflictos"]:
            self.stdout.write(self.style.WARNING(c))
