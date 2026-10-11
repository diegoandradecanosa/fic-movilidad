"""Resumen diario de plazos (RF-12): lo que vence en 7 o 2 días y las tareas vencidas.

Pensado para cron, una vez al día: python manage.py avisar_plazos
"""

from datetime import date

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from movilidad.plazos import resumen_diario, texto_resumen


class Command(BaseCommand):
    help = "Envía por correo el resumen diario de plazos y tareas de movilidad."

    def add_arguments(self, parser):
        parser.add_argument("--fecha", type=date.fromisoformat, help="Fecha de referencia (AAAA-MM-DD); por defecto, hoy")
        parser.add_argument("--simular", action="store_true", help="Muestra el resumen sin enviarlo")

    def handle(self, fecha=None, simular=False, **_):
        from django.utils import timezone

        hoy = fecha or timezone.localdate()
        resumen = resumen_diario(hoy)
        if not resumen:
            self.stdout.write("Nada que avisar hoy.")
            return
        for destinatario, plazos in resumen.items():
            texto = texto_resumen(plazos, hoy)
            asunto = f"Movilidad FIC: {len(plazos)} plazo(s) a atender · {hoy:%d/%m/%Y}"
            if simular or not destinatario:
                if not destinatario:
                    self.stdout.write(self.style.WARNING("Sin destinatario: configura CORREO_COORDINADOR."))
                self.stdout.write(f"{asunto}\n{texto}\n")
                continue
            send_mail(asunto, texto, settings.DEFAULT_FROM_EMAIL, [destinatario])
            self.stdout.write(f"Enviado a {destinatario}: {len(plazos)} plazo(s).")
