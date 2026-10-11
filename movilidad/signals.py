"""Mantiene al día los plazos automáticos cuando cambian la movilidad o sus versiones del LA."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from movilidad.models import EstadoMovilidad, Movilidad, ToR, VersionLA
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


@receiver(post_save, sender=ToR)
def _tor_guardado(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    movilidad = instance.movilidad
    if created and movilidad.estado == EstadoMovilidad.FINALIZADA:
        movilidad.cambiar_estado(EstadoMovilidad.TOR_RECIBIDO, nota=f"{instance} registrado")
    recalcular_plazos(movilidad)
