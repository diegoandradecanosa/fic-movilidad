"""Tests del bloque 5: roles, panel de expedientes (RF-03), ficha y anotaciones (RF-30)."""

from datetime import date, timedelta

import pytest
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.base import ContentFile

from movilidad.anotaciones import cerrar, crear_anotacion, puede_cerrar, puede_responder, responder
from movilidad.models import (
    Anotacion,
    Convocatoria,
    DestinatarioAnotacion,
    EstadoAnotacion,
    EstadoMovilidad,
    EstadoPlazo,
    Estudiante,
    Institucion,
    Movilidad,
    Plazo,
    Programa,
    TipoPlazo,
    TipoVersionLA,
)
from movilidad.panel import filas
from movilidad.plazos import recalcular_plazos
from movilidad.roles import ADMINISTRACION, COORDINACION
from tests.test_acuerdo_academico import gei, mov, version  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db
HOY = date(2026, 10, 11)


@pytest.fixture
def usuarios(django_user_model):
    def crear(nombre, grupo=None, email=""):
        u = django_user_model.objects.create_user(nombre, email, "x")
        if grupo:
            u.groups.add(Group.objects.get(name=grupo))
        return u
    return {
        "coord": crear("coord", COORDINACION, "coordinacion@example.org"),
        "admin1": crear("admin1", ADMINISTRACION, "admin1@example.org"),
        "admin2": crear("admin2", ADMINISTRACION),
        "nadie": crear("nadie"),
    }


@pytest.fixture
def correo(settings):
    settings.CORREO_COORDINADOR = "coordinacion@example.org"
    settings.DEFAULT_FROM_EMAIL = "coordinacion@example.org"
    return settings


def otra_movilidad(nombre, pais="IT", programa=Programa.SICUE, estado=EstadoMovilidad.ACEPTADA):
    return Movilidad.objects.create(
        estudiante=Estudiante.objects.create(nombre_completo=nombre, dni_hash=nombre.ljust(64, "z")[:64], dni_mascara="1"),
        convocatoria=Convocatoria.objects.get_or_create(curso="2026/27", plazo=1)[0],
        institucion=Institucion.objects.create(nombre=f"Univ {nombre}", pais=pais),
        programa=programa, estado=estado, semestre_inicio=2,
    )


# --- roles ----------------------------------------------------------------------------------------

def test_grupos_creados_al_migrar():
    coord, admin = Group.objects.get(name=COORDINACION), Group.objects.get(name=ADMINISTRACION)
    assert coord.permissions.filter(codename="change_versionla").exists()
    codenames = set(admin.permissions.values_list("codename", flat=True))
    assert {"view_movilidad", "view_versionla", "view_acta", "view_tor", "add_anotacion"} <= codenames
    assert not codenames & {"change_versionla", "change_acta", "add_versionla", "change_movilidad", "delete_acta"}


# --- panel ----------------------------------------------------------------------------------------

def test_panel_exige_sesion_y_permiso(client, usuarios, mov):
    assert client.get("/").status_code == 302  # al login
    client.force_login(usuarios["nadie"])
    assert client.get("/").status_code == 403
    client.force_login(usuarios["admin1"])
    r = client.get("/")
    assert r.status_code == 200 and "Proba Exemplo, Uxía" in r.content.decode()


def test_panel_filtros(client, usuarios, mov):
    otra_movilidad("Outra, Persoa")
    client.force_login(usuarios["coord"])
    contenido = lambda q: client.get("/" + q).content.decode()  # noqa: E731
    assert "Outra, Persoa" in contenido("") and "Proba Exemplo" in contenido("")
    assert "Proba Exemplo" not in contenido("?programa=SICUE")
    assert "Outra, Persoa" not in contenido("?pais=PT")
    assert "Outra, Persoa" in contenido("?semestre=2") and "Proba Exemplo" not in contenido("?semestre=2")
    assert "Outra, Persoa" in contenido("?q=outra")
    assert "Proba Exemplo" in contenido("?estado=LA_PREPARACION")
    html = contenido("?curso=2019/20")  # filtro no válido: aviso y ningún resultado, nunca «todo»
    assert "No hay expedientes" in html and "no es válido" in html


def test_filas_del_panel_proximo_plazo_y_bloqueos(mov, usuarios):
    mov.fecha_llegada_prevista = HOY + timedelta(days=5)
    mov.save()
    Plazo.objects.create(movilidad=mov, descripcion="Algo vencido", fecha_limite=HOY - timedelta(days=1))
    Plazo.objects.create(movilidad=mov, tipo=TipoPlazo.ENVIAR_LA_ADMINISTRACION, descripcion="Enviar LA")
    crear_anotacion(mov, usuarios["admin1"], "Falta el LA modificado", DestinatarioAnotacion.COORDINADOR,
                    HOY + timedelta(days=3))
    version(mov, tipo=TipoVersionLA.DURING_S1, firmar=False)
    [fila] = filas(Movilidad.objects.all(), HOY)
    assert fila.proximo.descripcion == "Algo vencido" and fila.alerta == "VENCIDA"
    assert fila.peticiones == 1
    assert fila.la == "Sin firmar · borrador pendiente"
    assert "LA modificado sin enviar a Administración" in fila.bloqueos
    assert "Vencido: Algo vencido" in fila.bloqueos


def test_panel_solo_con_bloqueos(client, usuarios, mov):
    otra_movilidad("Sen, Bloqueos")
    Plazo.objects.create(movilidad=mov, descripcion="Vencido", fecha_limite=date(2020, 1, 1))
    client.force_login(usuarios["coord"])
    html = client.get("/?bloqueos=on").content.decode()
    assert "Proba Exemplo" in html and "Sen, Bloqueos" not in html


# --- ficha y documentos ---------------------------------------------------------------------------

def test_ficha_de_solo_lectura_para_administracion(client, usuarios, mov, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    v = version(mov)
    v.pdf.save("la.pdf", ContentFile(b"%PDF la"))
    client.force_login(usuarios["admin1"])
    html = client.get(f"/expediente/{mov.pk}/").content.decode()
    assert "Acuerdo académico vigente" in html and "Editar en el admin" not in html
    assert client.get(v.pdf.url).status_code == 200  # Administración puede abrir los PDF
    client.force_login(usuarios["nadie"])
    assert client.get(v.pdf.url).status_code == 403
    assert client.get(f"/expediente/{mov.pk}/").status_code == 403


def test_administracion_no_edita_en_el_admin(client, usuarios, mov):
    usuarios["admin1"].is_staff = True
    usuarios["admin1"].save()
    v = version(mov, firmar=False)
    client.force_login(usuarios["admin1"])
    assert client.get(f"/admin/movilidad/versionla/{v.pk}/change/").status_code == 200  # lo ve
    r = client.post(f"/admin/movilidad/versionla/{v.pk}/change/", {"motivo": "x"})
    assert r.status_code == 403  # pero no lo cambia


# --- anotaciones ----------------------------------------------------------------------------------

def test_peticion_al_coordinador(mov, usuarios, correo):
    a = crear_anotacion(mov, usuarios["admin1"], "Falta el LA modificado para cambiar la matrícula",
                        DestinatarioAnotacion.COORDINADOR, HOY + timedelta(days=7))
    tarea = mov.plazos.get(tipo=TipoPlazo.PETICION)
    assert (tarea.anotacion, tarea.fecha_limite, tarea.estado) == (a, HOY + timedelta(days=7), EstadoPlazo.PENDIENTE)
    assert mail.outbox[0].to == ["coordinacion@example.org"] and mail.outbox[0].reply_to == ["admin1@example.org"]
    recalcular_plazos(mov)  # el recálculo de plazos automáticos no la borra
    assert Plazo.objects.filter(pk=tarea.pk, estado=EstadoPlazo.PENDIENTE).exists()
    assert not puede_responder(usuarios["admin2"], a) and puede_responder(usuarios["coord"], a)
    responder(a, usuarios["coord"], "Enviado hoy")
    tarea.refresh_from_db()
    assert tarea.estado == EstadoPlazo.CUMPLIDO and a.estado == EstadoAnotacion.RESPONDIDA
    assert mail.outbox[1].to == ["admin1@example.org"] and "Enviado hoy" in mail.outbox[1].body
    assert puede_cerrar(usuarios["admin1"], a) and not puede_cerrar(usuarios["admin2"], a)
    cerrar(a, usuarios["admin1"])
    assert a.estado == EstadoAnotacion.CERRADA


def test_peticion_al_alumno(mov, usuarios, correo):
    mov.estudiante.email = "uxia@example.org"
    mov.estudiante.save()
    a = crear_anotacion(mov, usuarios["admin1"], "Envía el ToR del segundo semestre", DestinatarioAnotacion.ALUMNO)
    assert mail.outbox[0].to == ["uxia@example.org"] and mail.outbox[0].reply_to == ["admin1@example.org"]
    assert not mov.plazos.filter(tipo=TipoPlazo.PETICION).exists()
    assert puede_responder(usuarios["admin1"], a) and not puede_responder(usuarios["admin2"], a)
    assert a.comunicaciones.get().enviada


def test_peticion_al_alumno_sin_correo(mov, usuarios, correo):
    a = crear_anotacion(mov, usuarios["admin1"], "¿Tienes el certificado de llegada?", DestinatarioAnotacion.ALUMNO)
    c = a.comunicaciones.get()
    assert not c.enviada and "Sin dirección" in c.error and not mail.outbox


def test_nota_interna_sin_avisos(mov, usuarios, correo):
    a = crear_anotacion(mov, usuarios["admin1"], "Matrícula revisada")
    assert not mail.outbox and not a.comunicaciones.exists() and not puede_responder(usuarios["coord"], a)


def test_anotar_desde_la_ficha(client, usuarios, mov, correo):
    client.force_login(usuarios["admin1"])
    r = client.post(f"/expediente/{mov.pk}/anotar/", {
        "texto": "Falta Xestión de Infraestruturas en el acta", "destinatario": "COORDINADOR",
        "fecha_limite": (date.today() + timedelta(days=5)).isoformat()})
    assert r.status_code == 302
    a = Anotacion.objects.get()
    assert a.autor == usuarios["admin1"] and len(mail.outbox) == 1
    # Administración no puede responder una petición al coordinador
    client.post(f"/anotacion/{a.pk}/responder/", {"respuesta": "Hecho"})
    a.refresh_from_db()
    assert a.estado == EstadoAnotacion.ABIERTA
    client.force_login(usuarios["coord"])
    client.post(f"/anotacion/{a.pk}/responder/", {"respuesta": "Hecho"})
    a.refresh_from_db()
    assert a.estado == EstadoAnotacion.RESPONDIDA
    html = client.get(f"/expediente/{mov.pk}/").content.decode()
    assert "Falta Xestión de Infraestruturas" in html and "Hecho" in html


def test_sin_permiso_no_anota(client, usuarios, mov):
    client.force_login(usuarios["nadie"])
    assert client.post(f"/expediente/{mov.pk}/anotar/", {"texto": "x", "destinatario": "NINGUNO"}).status_code == 403


def test_plazo_en_el_pasado_no_se_admite(client, usuarios, mov):
    client.force_login(usuarios["admin1"])
    client.post(f"/expediente/{mov.pk}/anotar/", {"texto": "x", "destinatario": "NINGUNO", "fecha_limite": "2020-01-01"})
    assert not Anotacion.objects.exists()


def test_login(client, usuarios):
    assert client.get("/cuentas/login/").status_code == 200
    r = client.post("/cuentas/login/", {"username": "admin1", "password": "x"})
    assert r.status_code == 302 and r.url == "/"


# --- acción «Cambiar estado» del admin ------------------------------------------------------------

def _post_estado(client, movilidades, **datos):
    return client.post("/admin/movilidad/movilidad/", {
        "action": "cambiar_estado", "_selected_action": [x.pk for x in movilidades], **datos})


def test_cambiar_estado_desde_el_admin(admin_client, mov):
    r = _post_estado(admin_client, [mov])
    assert r.status_code == 200 and "LA firmado" in r.content.decode()  # página intermedia con lo permitido
    r = _post_estado(admin_client, [mov], estado="LA_FIRMADO", nota="LA en papel recibido")
    assert r.status_code == 302
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.LA_FIRMADO
    evento = mov.eventos.first()
    assert (evento.estado_anterior, evento.estado_nuevo, evento.nota) == ("LA_PREPARACION", "LA_FIRMADO", "LA en papel recibido")
    assert evento.usuario.username == "admin"


def test_cambiar_estado_no_permitido_sin_forzar(admin_client, mov):
    r = _post_estado(admin_client, [mov], estado="FINALIZADA", nota="")
    assert r.status_code == 200 and "Transición no permitida" in r.content.decode()
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.LA_PREPARACION


def test_forzar_exige_motivo_y_queda_marcado(admin_client, mov):
    r = _post_estado(admin_client, [mov], estado="FINALIZADA", forzar="on", nota="")
    assert "hay que indicar el motivo" in r.content.decode()
    _post_estado(admin_client, [mov], estado="FINALIZADA", forzar="on", nota="Caso de 2025/26 ya terminado")
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.FINALIZADA
    assert mov.eventos.first().nota == "Caso de 2025/26 ya terminado [forzado]"


def test_varias_movilidades_a_la_vez(admin_client, mov):
    otra = otra_movilidad("Outra, Persoa", estado=EstadoMovilidad.ADJUDICADA)
    mov.estado = EstadoMovilidad.ADJUDICADA
    mov.save()
    _post_estado(admin_client, [mov, otra], estado="ACEPTADA", nota="Aceptación ante la ORI")
    assert set(Movilidad.objects.values_list("estado", flat=True)) == {EstadoMovilidad.ACEPTADA}


def test_administracion_no_cambia_estados(client, usuarios, mov):
    usuarios["admin1"].is_staff = True
    usuarios["admin1"].save()
    client.force_login(usuarios["admin1"])
    _post_estado(client, [mov], estado="LA_FIRMADO", nota="x")
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.LA_PREPARACION
