"""Mantiene al día los plazos automáticos cuando cambian la movilidad o sus versiones del LA."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from movilidad.models import Movilidad, VersionLA
from movilidad.plazos import recalcular_plazos


@receiver(post_save, sender=Movilidad)
def _movilidad_guardada(sender, instance, raw=False, **kwargs):
    if not raw:
        recalcular_plazos(instance)


# Al borrar un borrador, su tarea «Revisar» se borra en cascada (Plazo.version_la): no hace falta post_delete.
@receiver(post_save, sender=VersionLA)
def _version_cambiada(sender, instance, raw=False, **kwargs):
    if not raw:
        recalcular_plazos(instance.movilidad)
