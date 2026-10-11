"""Envío del LA modificado a Administración, con copia al coordinador y al alumno.

Se llama automáticamente al firmar un During o una ampliación (VersionLA.firmar) y a mano desde el
admin. Cada intento queda en Comunicacion; si no se puede enviar, se crea la tarea pendiente
«Enviar el LA a Administración» con el motivo.
"""

from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage

from movilidad.models import (
    Comunicacion,
    EstadoPlazo,
    EstadoVersionLA,
    OrigenPlazo,
    Plazo,
    TipoPlazo,
)


def nombre_natural(nombre_completo: str) -> str:
    """'Apellidos, Nombre' -> 'Nombre Apellidos'."""
    apellidos, _, nombre = nombre_completo.partition(",")
    return f"{nombre.strip()} {apellidos.strip()}".strip() if nombre else nombre_completo.strip()


def componer(version):
    m = version.movilidad
    alumno = nombre_natural(m.estudiante.nombre_completo)
    asunto = f"LA modificado de {alumno} ({m.institucion.nombre}) · {version.get_tipo_display()}"
    firmas = ", ".join(
        f"{parte} {fecha:%d/%m/%Y}" for parte, fecha in (
            ("estudiante", version.fecha_firma_estudiante), ("UDC", version.fecha_firma_udc),
            ("destino", version.fecha_firma_destino)) if fecha
    )
    cuerpo = "\n".join([
        "Buenos días:",
        "",
        f"Os envío el acuerdo académico modificado de {alumno} ({m.get_programa_display()}, "
        f"{m.institucion.nombre}, {m.convocatoria.curso}), firmado ({firmas or 'sin fechas registradas'}), "
        "para el cambio de matrícula.",
        "",
        f"Versión: {version.get_tipo_display()} (v{version.numero}).",
        f"Motivo: {version.motivo}" if version.motivo else "",
        "",
        "Un saludo,",
        "Coordinación de movilidad · Facultade de Informática",
    ])
    return asunto, cuerpo


def _tarea_pendiente(version, motivo):
    tarea, _ = Plazo.objects.get_or_create(
        movilidad=version.movilidad, version_la=version, tipo=TipoPlazo.ENVIAR_LA_ADMINISTRACION,
        estado=EstadoPlazo.PENDIENTE,
        defaults={"origen": OrigenPlazo.REGLA, "descripcion": f"Enviar {version} a Administración"},
    )
    tarea.nota = motivo
    tarea.save(update_fields=["nota"])
    return tarea


def enviar_la_a_administracion(version, usuario=None) -> Comunicacion | None:
    """Devuelve la Comunicacion registrada, o None si faltan datos para intentarlo."""
    problemas = []
    if version.estado != EstadoVersionLA.FIRMADA:
        problemas.append("la versión no está firmada")
    if not version.pdf:
        problemas.append("falta el PDF firmado de la versión")
    if not settings.CORREO_ADMINISTRACION:
        problemas.append("no está configurado CORREO_ADMINISTRACION")
    if problemas:
        _tarea_pendiente(version, "No se envió automáticamente: " + "; ".join(problemas) + ".")
        return None

    estudiante = version.movilidad.estudiante
    cc = [c for c in (settings.CORREO_COORDINADOR, estudiante.email) if c]
    asunto, cuerpo = componer(version)
    nombre_pdf = Path(version.pdf.name).name
    mensaje = EmailMessage(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, [settings.CORREO_ADMINISTRACION], cc=cc)
    with version.pdf.open("rb") as fh:
        mensaje.attach(nombre_pdf, fh.read(), "application/pdf")
    enviada, error = True, ""
    try:
        mensaje.send()
    except Exception as e:  # el fallo queda registrado y como tarea; la firma ya está hecha
        enviada, error = False, f"{type(e).__name__}: {e}"
    if enviada and not estudiante.email:
        error = "Enviado sin copia al alumno: no tiene correo registrado."
    comunicacion = Comunicacion.objects.create(
        movilidad=version.movilidad, version_la=version, remitente=settings.DEFAULT_FROM_EMAIL,
        para=settings.CORREO_ADMINISTRACION, cc=", ".join(cc), asunto=asunto, cuerpo=cuerpo,
        adjuntos=nombre_pdf, enviada=enviada, error=error, usuario=usuario,
    )
    if comunicacion.enviada:
        for tarea in version.plazos.filter(tipo=TipoPlazo.ENVIAR_LA_ADMINISTRACION, estado=EstadoPlazo.PENDIENTE):
            tarea.cerrar(nota=f"Enviado el {comunicacion.fecha:%d/%m/%Y %H:%M}")
    else:
        _tarea_pendiente(version, f"Falló el envío: {error}")
    return comunicacion
