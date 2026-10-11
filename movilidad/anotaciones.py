"""Anotaciones y peticiones sobre un expediente (RF-30).

- Petición al coordinador: crea una tarea en «Mis pendientes» (con el plazo pedido) y le avisa
  por correo.
- Petición al alumno: le llega por correo (con respuesta a quien la creó). Quien la creó la marca
  como respondida cuando el alumno contesta.
- Al responder, se avisa al autor; al cerrar, se cierra la tarea del coordinador.
Todos los correos quedan en Comunicacion.
"""

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from movilidad.envios import nombre_natural
from movilidad.models import (
    Anotacion,
    Comunicacion,
    DestinatarioAnotacion,
    EstadoAnotacion,
    EstadoPlazo,
    OrigenPlazo,
    Plazo,
    TipoPlazo,
)


def _enviar(anotacion, para, asunto, cuerpo, usuario, reply_to=None):
    para = [p for p in para if p]
    if not para:
        return Comunicacion.objects.create(
            movilidad=anotacion.movilidad, anotacion=anotacion, asunto=asunto, cuerpo=cuerpo, enviada=False,
            error="Sin dirección de correo del destinatario.", usuario=usuario,
        )
    enviada, error = True, ""
    try:
        EmailMessage(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, para, reply_to=reply_to or None).send()
    except Exception as e:  # queda registrado; la anotación existe igualmente
        enviada, error = False, f"{type(e).__name__}: {e}"
    return Comunicacion.objects.create(
        movilidad=anotacion.movilidad, anotacion=anotacion, remitente=settings.DEFAULT_FROM_EMAIL,
        para=", ".join(para), asunto=asunto, cuerpo=cuerpo, enviada=enviada, error=error, usuario=usuario,
    )


def _plazo_texto(anotacion):
    return f"Plazo: {anotacion.fecha_limite:%d/%m/%Y}." if anotacion.fecha_limite else ""


@transaction.atomic
def crear_anotacion(movilidad, autor, texto, destinatario=DestinatarioAnotacion.NINGUNO, fecha_limite=None):
    a = Anotacion.objects.create(movilidad=movilidad, autor=autor, texto=texto, destinatario=destinatario,
                                 fecha_limite=fecha_limite)
    alumno = nombre_natural(movilidad.estudiante.nombre_completo)
    quien = autor.get_full_name() or autor.get_username()
    if destinatario == DestinatarioAnotacion.COORDINADOR:
        Plazo.objects.create(
            movilidad=movilidad, anotacion=a, tipo=TipoPlazo.PETICION, origen=OrigenPlazo.REGLA,
            fecha_limite=fecha_limite, descripcion=f"Petición de {quien}: {texto[:200]}",
        )
        _enviar(a, [settings.CORREO_COORDINADOR], f"Petición sobre el expediente de {alumno}",
                f"{quien} ha dejado una petición en el expediente de movilidad de {alumno} "
                f"({movilidad.institucion.nombre}):\n\n{texto}\n\n{_plazo_texto(a)}", autor,
                reply_to=[autor.email] if autor.email else None)
    elif destinatario == DestinatarioAnotacion.ALUMNO:
        _enviar(a, [movilidad.estudiante.email], "Movilidad FIC: petición sobre tu expediente",
                f"Hola:\n\nSobre tu movilidad en {movilidad.institucion.nombre} ({movilidad.convocatoria.curso}):\n\n"
                f"{texto}\n\n{_plazo_texto(a)}\n\nPuedes responder a este correo.\n\n"
                "Facultade de Informática · Universidade da Coruña", autor,
                reply_to=[autor.email] if autor.email else None)
    return a


@transaction.atomic
def responder(anotacion, usuario, respuesta):
    anotacion.respuesta = respuesta
    anotacion.respondida_por, anotacion.respondida_en = usuario, timezone.now()
    anotacion.estado = EstadoAnotacion.RESPONDIDA
    anotacion.save()
    for p in anotacion.plazos.filter(estado=EstadoPlazo.PENDIENTE):
        p.cerrar(EstadoPlazo.CUMPLIDO, "Petición respondida")
    autor = anotacion.autor
    if autor and autor != usuario:
        alumno = nombre_natural(anotacion.movilidad.estudiante.nombre_completo)
        _enviar(anotacion, [autor.email], f"Respuesta a tu petición sobre {alumno}",
                f"Tu petición:\n{anotacion.texto}\n\nRespuesta de {usuario.get_full_name() or usuario.get_username()}:\n"
                f"{respuesta}", usuario)


@transaction.atomic
def cerrar(anotacion, usuario):
    anotacion.estado, anotacion.cerrada_en = EstadoAnotacion.CERRADA, timezone.now()
    anotacion.save()
    for p in anotacion.plazos.filter(estado=EstadoPlazo.PENDIENTE):
        p.cerrar(EstadoPlazo.CUMPLIDO, f"Petición cerrada por {usuario}")


def puede_responder(usuario, anotacion) -> bool:
    """Las peticiones al coordinador las responde Coordinación; las del alumno, quien las creó
    (cuando el alumno contesta por correo) o Coordinación."""
    from movilidad.roles import es_coordinacion

    if anotacion.estado != EstadoAnotacion.ABIERTA or not anotacion.es_peticion:
        return False
    if anotacion.destinatario == DestinatarioAnotacion.COORDINADOR:
        return es_coordinacion(usuario)
    return es_coordinacion(usuario) or anotacion.autor_id == usuario.pk


def puede_cerrar(usuario, anotacion) -> bool:
    from movilidad.roles import es_coordinacion

    return anotacion.estado != EstadoAnotacion.CERRADA and (
        es_coordinacion(usuario) or anotacion.autor_id == usuario.pk)
