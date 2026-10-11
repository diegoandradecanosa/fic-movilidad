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


# --- «ATM de [Nombre]»: acta firmada a Administración (RF-19) --------------------------------------


class EnvioImposible(ValueError):
    pass


def adjuntos_atm(acta) -> list:
    """(nombre, contenido) del acta firmada, los ToR usados y las versiones del LA vigente."""
    from movilidad.la_vigente import la_vigente

    ficheros = [acta.pdf_firmado]
    ficheros += [t.pdf for t in acta.tors.all() if t.pdf]
    ficheros += [v.pdf for v in la_vigente(acta.movilidad).versiones if v.pdf]
    adjuntos = []
    for f in ficheros:
        with f.open("rb") as fh:
            adjuntos.append((Path(f.name).name, fh.read()))
    return adjuntos


def componer_atm(acta):
    alumno = nombre_natural(acta.movilidad.estudiante.nombre_completo)
    asunto = f"{'RE: ' if acta.corrige_id else ''}ATM de {alumno}"
    lineas = ["Buenos días:", ""]
    if acta.corrige_id:
        lineas += [f"Os envío el acta corregida (v{acta.numero}) de {alumno}, que sustituye a la v{acta.corrige.numero}.",
                   f"Motivo de la corrección: {acta.motivo_correccion}", ""]
    else:
        lineas += [f"Os envío el acta de reconocimiento («After the Mobility») de {alumno} "
                   f"({acta.movilidad.institucion.nombre}, {acta.movilidad.convocatoria.curso}), firmada, "
                   "junto con el certificado de notas (ToR) y el acuerdo académico.", ""]
    lineas += ["Un saludo,", "Coordinación de movilidad · Facultade de Informática"]
    return asunto, "\n".join(lineas)


def enviar_atm(acta, usuario=None) -> Comunicacion:
    """Envía el acta firmada a Administración con copia a la ORI y al coordinador."""
    from django.utils import timezone

    from movilidad.models import EstadoActa, EstadoMovilidad
    from movilidad.plazos import recalcular_plazos

    if acta.estado != EstadoActa.FIRMADA:
        raise EnvioImposible("Solo se envía un acta firmada (y no enviada ya).")
    if not acta.pdf_firmado:
        raise EnvioImposible("Falta el PDF firmado del acta.")
    if not settings.CORREO_ADMINISTRACION:
        raise EnvioImposible("No está configurado CORREO_ADMINISTRACION.")
    asunto, cuerpo = componer_atm(acta)
    adjuntos = adjuntos_atm(acta)
    cc = [c for c in (settings.CORREO_ORI, settings.CORREO_COORDINADOR) if c]
    mensaje = EmailMessage(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, [settings.CORREO_ADMINISTRACION], cc=cc)
    for nombre, contenido in adjuntos:
        mensaje.attach(nombre, contenido, "application/pdf")
    enviada, error = True, ""
    try:
        mensaje.send()
    except Exception as e:  # queda registrado; el acta sigue firmada y se puede reintentar
        enviada, error = False, f"{type(e).__name__}: {e}"
    if enviada and not settings.CORREO_ORI:
        error = "Enviado sin copia a la ORI: no está configurado CORREO_ORI."
    comunicacion = Comunicacion.objects.create(
        movilidad=acta.movilidad, acta=acta, remitente=settings.DEFAULT_FROM_EMAIL,
        para=settings.CORREO_ADMINISTRACION, cc=", ".join(cc), asunto=asunto, cuerpo=cuerpo,
        adjuntos=", ".join(n for n, _ in adjuntos), enviada=enviada, error=error, usuario=usuario,
    )
    if enviada:
        acta.estado, acta.fecha_envio = EstadoActa.ENVIADA, timezone.now()
        acta.save()
        if acta.movilidad.estado == EstadoMovilidad.ACTA_FIRMADA:
            acta.movilidad.cambiar_estado(EstadoMovilidad.ENVIADA_ADMINISTRACION, usuario=usuario, nota=asunto)
        recalcular_plazos(acta.movilidad)
    return comunicacion
