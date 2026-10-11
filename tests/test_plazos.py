"""Tests del bloque 3: plazos automáticos, alertas, «Mis pendientes», resumen diario y envío del LA."""

from datetime import date, timedelta

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from movilidad.envios import enviar_la_a_administracion, nombre_natural
from movilidad.models import (
    Comunicacion,
    EstadoPlazo,
    EstadoVersionLA,
    ModificacionNoPermitida,
    OrigenPlazo,
    Plazo,
    TipoPlazo,
    TipoVersionLA,
    VersionLA,
)
from movilidad.plazos import Alerta, nivel_alerta, pendientes, resumen_diario
from tests.test_acuerdo_academico import gei, mov, version  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db

T = TipoPlazo


def plazos(mov, **filtro):
    return {p.tipo: p for p in mov.plazos.filter(**filtro)}


def pendiente(mov, tipo):
    return mov.plazos.get(tipo=tipo, estado=EstadoPlazo.PENDIENTE)


# --- plazos automáticos ----------------------------------------------------------------------------

def test_before_antes_de_la_llegada(mov):
    mov.fecha_llegada_prevista = date(2026, 9, 1)
    mov.save()
    p = pendiente(mov, T.FIRMAR_BEFORE)
    assert (p.fecha_limite, p.regla, p.origen) == (date(2026, 9, 1), "RN-06", OrigenPlazo.REGLA)
    version(mov)
    p.refresh_from_db()
    assert p.estado == EstadoPlazo.CUMPLIDO and p.cumplido_en


def test_ventanas_de_una_movilidad_semestral(mov):
    mov.fecha_llegada = date(2026, 9, 10)
    mov.save()
    p = plazos(mov)
    assert p[T.VENTANA_MOD_S1].fecha_limite == date(2026, 10, 10)
    assert p[T.VENTANA_AMPLIACION].fecha_limite == date(2026, 10, 10)
    assert p[T.PREFERENCIA_AMPLIACION].fecha_limite == date(2026, 11, 10)
    assert T.VENTANA_MOD_S2 not in p and T.VENTANA_REDUCCION not in p


def test_ventanas_de_una_movilidad_anual(mov):
    mov.duracion_semestres, mov.fecha_llegada, mov.fecha_inicio_s2 = 2, date(2026, 9, 10), date(2027, 2, 1)
    mov.save()
    p = plazos(mov)
    assert p[T.VENTANA_MOD_S2].fecha_limite == date(2027, 3, 1)
    assert p[T.VENTANA_REDUCCION].fecha_limite == date(2026, 10, 10)
    assert T.VENTANA_AMPLIACION not in p


def test_movilidad_del_segundo_semestre(mov):
    mov.semestre_inicio, mov.fecha_llegada = 2, date(2027, 1, 31)
    mov.save()
    p = plazos(mov)
    assert p[T.VENTANA_MOD_S2].fecha_limite == date(2027, 2, 28)
    assert T.VENTANA_MOD_S1 not in p
    assert T.PREFERENCIA_AMPLIACION not in p  # la llegada es posterior al 10/11


def test_recalcula_fechas_y_respeta_lo_cerrado_y_lo_manual(mov):
    mov.fecha_llegada = date(2026, 9, 10)
    mov.save()
    manual = Plazo.objects.create(movilidad=mov, descripcion="Límite de destino", fecha_limite=date(2026, 9, 30))
    pendiente(mov, T.VENTANA_AMPLIACION).cerrar(EstadoPlazo.DESCARTADO)
    mov.fecha_llegada = date(2026, 9, 20)
    mov.save()
    assert pendiente(mov, T.VENTANA_MOD_S1).fecha_limite == date(2026, 10, 20)
    assert not mov.plazos.filter(tipo=T.VENTANA_AMPLIACION, estado=EstadoPlazo.PENDIENTE).exists()
    assert mov.plazos.get(tipo=T.VENTANA_AMPLIACION).fecha_limite == date(2026, 10, 10)  # no se toca
    mov.fecha_llegada = None
    mov.save()
    assert not mov.plazos.filter(tipo=T.VENTANA_MOD_S1).exists()
    manual.refresh_from_db()
    assert manual.estado == EstadoPlazo.PENDIENTE


def test_tarea_revisar_borrador(mov):
    mov.fecha_llegada = date(2026, 9, 10)
    mov.save()
    v = version(mov, tipo=TipoVersionLA.DURING_S1, firmar=False)
    tarea = pendiente(mov, T.REVISAR_LA)
    assert tarea.version_la == v and tarea.fecha_limite == date(2026, 10, 10)
    v.firmar()
    tarea.refresh_from_db()
    assert tarea.estado == EstadoPlazo.CUMPLIDO
    assert plazos(mov)[T.VENTANA_MOD_S1].estado == EstadoPlazo.CUMPLIDO

    borrador = version(mov, tipo=TipoVersionLA.DURING_S2, firmar=False)
    assert mov.plazos.filter(version_la=borrador).exists()
    borrador.delete()
    assert not mov.plazos.filter(tipo=T.REVISAR_LA, estado=EstadoPlazo.PENDIENTE).exists()

    anulada = version(mov, tipo=TipoVersionLA.DURING_S2, firmar=False)
    anulada.estado, anulada.motivo_invalidacion = EstadoVersionLA.INVALIDADA, "Retirada por el alumno"
    anulada.save()
    assert mov.plazos.get(version_la=anulada).estado == EstadoPlazo.DESCARTADO


# --- alertas, bandeja y resumen --------------------------------------------------------------------

HOY = date(2026, 10, 11)


def plazo(mov, dias, tipo=T.OTRO, **kw):
    return Plazo.objects.create(movilidad=mov, tipo=tipo, fecha_limite=HOY + timedelta(days=dias), **kw)


def test_niveles_de_alerta(mov):
    assert nivel_alerta(plazo(mov, -1), HOY) == Alerta.VENCIDA
    assert nivel_alerta(plazo(mov, -1, T.VENTANA_MOD_S1), HOY) == Alerta.CADUCADA
    assert nivel_alerta(plazo(mov, 0), HOY) == Alerta.URGENTE
    assert nivel_alerta(plazo(mov, 2), HOY) == Alerta.URGENTE
    assert nivel_alerta(plazo(mov, 7), HOY) == Alerta.PROXIMA
    assert nivel_alerta(plazo(mov, 8), HOY) is None
    assert nivel_alerta(plazo(mov, 1, estado=EstadoPlazo.CUMPLIDO), HOY) is None


def test_mis_pendientes(mov, django_user_model):
    yo, otro = django_user_model.objects.create(username="yo"), django_user_model.objects.create(username="otro")
    mio, libre = plazo(mov, 3, responsable=yo), plazo(mov, 1)
    plazo(mov, 1, responsable=otro)
    plazo(mov, -1, T.VENTANA_MOD_S1)  # ventana caducada: fuera
    vencida = plazo(mov, -5)
    plazo(mov, 1, estado=EstadoPlazo.CUMPLIDO)
    assert list(pendientes(HOY, yo)) == [vencida, libre, mio]


def test_resumen_diario(mov, settings, django_user_model):
    settings.CORREO_COORDINADOR = "coordinacion@example.org"
    tutora = django_user_model.objects.create(username="tutora", email="tutora@example.org")
    a7, a2, vencida = plazo(mov, 7), plazo(mov, 2), plazo(mov, -3)
    suya = plazo(mov, 7, responsable=tutora)
    plazo(mov, 5), plazo(mov, -3, T.VENTANA_MOD_S1), plazo(mov, 2, estado=EstadoPlazo.DESCARTADO)
    assert resumen_diario(HOY) == {"coordinacion@example.org": [vencida, a2, a7], "tutora@example.org": [suya]}


def test_comando_avisar_plazos(mov, settings, capsys):
    settings.CORREO_COORDINADOR = "coordinacion@example.org"
    plazo(mov, 2, descripcion="Firmar algo")
    call_command("avisar_plazos", "--fecha", HOY.isoformat(), "--simular")
    assert "vence en 2 días" in capsys.readouterr().out and not mail.outbox
    call_command("avisar_plazos", "--fecha", HOY.isoformat())
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["coordinacion@example.org"]
    assert "Firmar algo" in mail.outbox[0].body and "Proba Exemplo, Uxía" in mail.outbox[0].body
    call_command("avisar_plazos", "--fecha", (HOY + timedelta(days=1)).isoformat())
    assert len(mail.outbox) == 1  # al día siguiente (1 día) no se repite el aviso


# --- envío del LA modificado a Administración -----------------------------------------------------

@pytest.fixture
def correo(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.CORREO_ADMINISTRACION = "administracion@example.org"
    settings.CORREO_COORDINADOR = "coordinacion@example.org"
    settings.DEFAULT_FROM_EMAIL = "coordinacion@example.org"
    settings.ENVIO_AUTOMATICO_LA = True
    return settings


def pdf():
    return SimpleUploadedFile("LA_during.pdf", b"%PDF-1.4 sintetico", content_type="application/pdf")


def test_nombre_natural():
    assert nombre_natural("Proba Exemplo, Uxía") == "Uxía Proba Exemplo"
    assert nombre_natural("Sin Coma") == "Sin Coma"


def test_firmar_during_envia_el_la_a_administracion(mov, correo, django_capture_on_commit_callbacks):
    mov.estudiante.email = "uxia.proba@example.org"
    mov.estudiante.save()
    with django_capture_on_commit_callbacks(execute=True):
        v = version(mov, tipo=TipoVersionLA.DURING_S1, pdf=pdf(), motivo="Materia no ofertada")
    assert len(mail.outbox) == 1
    m = mail.outbox[0]
    assert m.to == ["administracion@example.org"]
    assert m.cc == ["coordinacion@example.org", "uxia.proba@example.org"]
    assert m.subject.startswith("LA modificado de Uxía Proba Exemplo")
    assert "Materia no ofertada" in m.body
    assert m.attachments[0][0].startswith("LA_during") and m.attachments[0][1] == b"%PDF-1.4 sintetico"
    c = Comunicacion.objects.get()
    assert (c.version_la, c.enviada, c.error) == (v, True, "")
    assert not mov.plazos.filter(tipo=T.ENVIAR_LA_ADMINISTRACION).exists()


def test_before_no_se_envia(mov, correo, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        version(mov, pdf=pdf())
    assert not mail.outbox


def test_envio_automatico_desactivado(mov, correo, django_capture_on_commit_callbacks):
    correo.ENVIO_AUTOMATICO_LA = False
    with django_capture_on_commit_callbacks(execute=True):
        version(mov, tipo=TipoVersionLA.DURING_S1, pdf=pdf())
    assert not mail.outbox and not Comunicacion.objects.exists()


def test_sin_pdf_queda_tarea_y_se_envia_al_adjuntarlo(mov, correo, django_capture_on_commit_callbacks, admin_client):
    with django_capture_on_commit_callbacks(execute=True):
        v = version(mov, tipo=TipoVersionLA.DURING_S1)
    tarea = pendiente(mov, T.ENVIAR_LA_ADMINISTRACION)
    assert "falta el PDF" in tarea.nota and not mail.outbox
    # adjuntar el PDF que faltaba en la versión firmada
    r = admin_client.post(f"/admin/movilidad/versionla/{v.pk}/change/", {
        "estado": EstadoVersionLA.FIRMADA, "motivo_invalidacion": "", "pdf": pdf(),
        "filas-TOTAL_FORMS": "0", "filas-INITIAL_FORMS": "0",
    })
    assert r.status_code == 302
    assert len(mail.outbox) == 1
    tarea.refresh_from_db()
    assert tarea.estado == EstadoPlazo.CUMPLIDO
    # y ya no se puede sustituir
    v = VersionLA.objects.get(pk=v.pk)
    v.pdf = "la/otro.pdf"
    with pytest.raises(ModificacionNoPermitida):
        v.save()


def test_sin_correo_de_administracion_queda_tarea(mov, correo, django_capture_on_commit_callbacks):
    correo.CORREO_ADMINISTRACION = ""
    with django_capture_on_commit_callbacks(execute=True):
        version(mov, tipo=TipoVersionLA.DURING_S2, pdf=pdf())
    assert "CORREO_ADMINISTRACION" in pendiente(mov, T.ENVIAR_LA_ADMINISTRACION).nota


def test_fallo_de_envio_queda_registrado(mov, correo, monkeypatch):
    v = version(mov, tipo=TipoVersionLA.AMPLIACION, pdf=pdf())

    def falla(self, *a, **kw):
        raise ConnectionRefusedError("SMTP no disponible")

    monkeypatch.setattr("django.core.mail.EmailMessage.send", falla)
    c = enviar_la_a_administracion(v)
    assert not c.enviada and "SMTP no disponible" in c.error
    assert "Falló el envío" in pendiente(mov, T.ENVIAR_LA_ADMINISTRACION).nota


def test_alumno_sin_correo_se_envia_igual_y_se_anota(mov, correo):
    v = version(mov, tipo=TipoVersionLA.DURING_S1, pdf=pdf())
    c = enviar_la_a_administracion(v)
    assert c.enviada and "sin copia al alumno" in c.error
    assert mail.outbox[0].cc == ["coordinacion@example.org"]


# --- admin ----------------------------------------------------------------------------------------

def test_admin_mis_pendientes(admin_client, mov):
    p = plazo(mov, 1, descripcion="Revisar cambio de horario")
    r = admin_client.get("/admin/movilidad/plazo/pendientes/")
    assert r.status_code == 200 and "Revisar cambio de horario" in r.content.decode()
    r = admin_client.post("/admin/movilidad/plazo/pendientes/", {"plazo": p.pk, "accion": "cumplido"})
    assert r.status_code == 302
    p.refresh_from_db()
    assert p.estado == EstadoPlazo.CUMPLIDO and "admin" in p.nota
    assert admin_client.get("/admin/movilidad/plazo/").status_code == 200
    assert admin_client.get(f"/admin/movilidad/movilidad/{mov.pk}/change/").status_code == 200


def test_admin_accion_enviar(admin_client, mov, correo):
    v = version(mov, tipo=TipoVersionLA.DURING_S1, pdf=pdf())
    admin_client.post("/admin/movilidad/versionla/", {"action": "enviar_administracion", "_selected_action": [v.pk]})
    assert len(mail.outbox) == 1
    assert admin_client.get(f"/admin/movilidad/comunicacion/{Comunicacion.objects.get().pk}/change/").status_code == 200
