"""Roles de la aplicación (grupos de Django), creados al migrar.

- Coordinación: todo.
- Administración: lectura de todos los expedientes, LA, ToR y actas; crea y cierra anotaciones
  (peticiones al alumno o al coordinador). No edita LA ni actas (RF-30).
- Tutor académico: previsto para más adelante (alumnos asignados).
"""

from django.contrib.auth.models import Group, Permission

COORDINACION = "Coordinación"
ADMINISTRACION = "Administración"


def configurar_grupos(**kwargs):
    permisos = Permission.objects.filter(content_type__app_label="movilidad")
    coordinacion, _ = Group.objects.get_or_create(name=COORDINACION)
    coordinacion.permissions.set(permisos)
    administracion, _ = Group.objects.get_or_create(name=ADMINISTRACION)
    administracion.permissions.set(
        permisos.filter(codename__startswith="view_")
        | permisos.filter(codename__in=["add_anotacion", "change_anotacion"])
    )


def en_grupo(usuario, nombre) -> bool:
    return usuario.is_authenticated and (usuario.is_superuser or usuario.groups.filter(name=nombre).exists())


def es_coordinacion(usuario) -> bool:
    return en_grupo(usuario, COORDINACION)


def es_administracion(usuario) -> bool:
    return usuario.is_authenticated and usuario.groups.filter(name=ADMINISTRACION).exists()
