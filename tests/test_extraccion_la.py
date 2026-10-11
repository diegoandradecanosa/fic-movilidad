"""RF-10: propuesta de filas de una versión del LA a partir de su PDF con IA (respuestas simuladas)."""

from datetime import date
from decimal import Decimal

import pytest

from movilidad import extraccion_ia
from movilidad.catalogo_udc import buscar_asignatura_udc
from movilidad.la_vigente import la_vigente
from movilidad.models import (
    AccionFila,
    AsignaturaDestino,
    AsignaturaUDC,
    EstadoVersionLA,
    FormatoLA,
    TipoVersionLA,
    VersionLA,
)
from tests.test_acta import media, pdf  # noqa: F401 (fixture)
from tests.test_acuerdo_academico import A, dest, gei, mov, version  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db


def materia(titulo, codigo="", ects=6, semestre=1, accion="ALTA"):
    return {"codigo": codigo, "titulo": titulo, "ects": ects, "semestre": semestre, "accion": accion}


def respuesta(destino, udc, correspondencias, **kw):
    return {"nombre_alumno": "Uxía Proba Exemplo", "institucion_destino": "Universidade Ficticia",
            "formato": "OLA_EWP", "fecha_firma_estudiante": "2026-05-14", "fecha_firma_udc": "2026-05-15",
            "fecha_firma_destino": "", "materias_destino": destino, "materias_udc": udc,
            "correspondencias": correspondencias, "observaciones": "", **kw}


def corr(destino, udc, segun_documento=False, sin_reconocimiento=False):
    return {"destino": destino, "udc": udc, "segun_documento": segun_documento, "sin_reconocimiento": sin_reconocimiento}


@pytest.fixture
def catalogo(gei):
    def nueva(codigo, nombre):
        return AsignaturaUDC.objects.create(curso_academico="2026/27", titulacion=gei, codigo=codigo, nombre=nombre,
                                            ects=Decimal(6))
    return {"xp": nueva("202323", "Xestión de Proxectos"), "xi": nueva("200190", "Xestión de Infraestruturas"),
            "aa2": nueva("202001", "Aprendizaxe Automática II"), "aa3": nueva("202002", "Aprendizaxe Automática III")}


@pytest.fixture
def borrador(mov, gei, media):
    mov.estudiante.titulacion = gei
    mov.estudiante.save()
    return VersionLA.objects.create(movilidad=mov, tipo=TipoVersionLA.BEFORE, formato=FormatoLA.OLA_EWP,
                                    pdf=pdf("ola.pdf"))


def test_busca_materias_udc_por_codigo_codigo_antiguo_y_nombre(catalogo, gei):
    assert buscar_asignatura_udc("200190", "", gei)[0] == catalogo["xi"]
    catalogo["xp"].codigo_oficial = "614G01021"
    catalogo["xp"].save()
    assert buscar_asignatura_udc("614G01021", "", gei)[0] == catalogo["xp"]
    # en castellano y con código antiguo aún desconocido: por nombre, dentro del plan
    assert buscar_asignatura_udc("614G01025", "Gestión de Infraestructuras", None) == (catalogo["xi"], "nombre (1.00)")
    assert buscar_asignatura_udc("", "Aprendizaje Automático II", gei)[0] == catalogo["aa2"]  # no la III
    a, motivo = buscar_asignatura_udc("", "Robótica Submarina", gei)
    assert a is None and "no está en el catálogo" in motivo


def test_ola_before_propone_filas_en_borrador(borrador, catalogo):
    datos = respuesta(
        [materia("Cloud Computing", "FMITB21710"), materia("Project Management", "FMITB22705"),
         materia("Lithuanian A1", ects=3)],
        [materia("Gestión de Infraestructuras", "614G01025"), materia("Gestión de Proyectos", "614G01021")],
        [corr([0], [0]), corr([1], [1]), corr([2], [], sin_reconocimiento=True)],
    )
    r = extraccion_ia.extraer_la(borrador, llamar=lambda contenido: datos)
    assert r["filas"] == 3
    borrador.refresh_from_db()
    assert borrador.estado == EstadoVersionLA.BORRADOR and borrador.extraido_con_ia  # lo firma el coordinador
    assert (borrador.fecha_firma_estudiante, borrador.fecha_firma_destino) == (date(2026, 5, 14), None)
    filas = list(borrador.filas.all())
    assert [(list(f.materias_destino.values_list("nombre", flat=True)), list(f.materias_udc.all()))
            for f in filas] == [(["Cloud Computing"], [catalogo["xi"]]), (["Project Management"], [catalogo["xp"]]),
                                (["Lithuanian A1"], [])]
    assert filas[2].sin_reconocimiento
    assert all("propuesto por la IA" in f.observaciones for f in filas)  # el OLA no relaciona A y B
    # el catálogo de destino crece (RF-05) y el código antiguo se recupera para el acta
    assert AsignaturaDestino.objects.filter(institucion=borrador.movilidad.institucion).count() == 3
    catalogo["xi"].refresh_from_db()
    assert catalogo["xi"].codigo_oficial == "614G01025"
    assert len(la_vigente(borrador.movilidad, incluir=borrador).grupos) == 3


def test_during_con_bajas_reutiliza_las_materias_del_before(mov, gei, media, catalogo):
    mov.estudiante.titulacion = gei
    mov.estudiante.save()
    iss = dest(mov, "Information System Security")
    iss.codigo = "FMISM22302"
    iss.save()
    version(mov, filas=[(A, 1, [iss], [catalogo["xi"]])])
    during = VersionLA.objects.create(movilidad=mov, tipo=TipoVersionLA.DURING_S1, formato=FormatoLA.OLA_EWP,
                                      pdf=pdf("during.pdf"))
    datos = respuesta(
        [materia("Information Systems Security", "FMISM-22302", accion="BAJA"),
         materia("Cryptography and Information Security", "FMISB18500")],
        [materia("Xestión de Infraestruturas", "200190", accion="BAJA"), materia("Xestión de Infraestruturas", "200190")],
        [corr([0], [0], segun_documento=True), corr([1], [1], segun_documento=True)],
    )
    extraccion_ia.extraer_la(during, llamar=lambda contenido: datos)
    baja, alta = during.filas.all()
    assert (baja.accion, alta.accion) == (AccionFila.BAJA, AccionFila.ALTA)
    assert list(baja.materias_destino.all()) == [iss]  # mismo código (sin guion): no se duplica
    assert baja.observaciones == ""  # el documento relaciona las materias
    during.firmar()
    grupos = la_vigente(mov).grupos
    assert [[d.nombre for d in g.materias_destino] for g in grupos] == [["Cryptography and Information Security"]]


def test_avisos_de_nombre_institucion_y_catalogo(borrador, catalogo):
    datos = respuesta([materia("Underwater Robotics"), materia("Surfing")],
                      [materia("Robótica Submarina", "614G01999")], [corr([0], [0])],
                      nombre_alumno="Outra Persoa", institucion_destino="Technische Hochschule Beispiel",
                      observaciones="La tabla B está cortada.")
    r = extraccion_ia.extraer_la(borrador, llamar=lambda contenido: datos)
    avisos = " | ".join(r["avisos"])
    assert "no coincide con el del alumno/a" in avisos
    assert "no parece Universidade Ficticia" in avisos
    assert "Materias sin correspondencia: Surfing" in avisos
    assert "sin pareja en el catálogo" in avisos and "IA: La tabla B está cortada." in avisos
    fila = borrador.filas.get()
    assert fila.materias_udc.count() == 0 and "Robótica Submarina" in fila.observaciones
    borrador.refresh_from_db()
    assert "La tabla B está cortada" in borrador.notas


def test_no_toca_una_version_firmada_ni_funciona_sin_clave(mov, borrador, settings):
    firmada = version(mov, filas=[], pdf=pdf("firmado.pdf"))
    with pytest.raises(ValueError, match="firmada"):
        extraccion_ia.extraer_la(firmada, llamar=lambda contenido: {})
    settings.ANTHROPIC_API_KEY = ""
    with pytest.raises(extraccion_ia.IANoDisponible, match="ANTHROPIC_API_KEY"):
        extraccion_ia.extraer_la(borrador)


def test_esquema_valido_para_structured_outputs():
    def revisar(nodo):
        if nodo.get("type") == "object":
            assert nodo["additionalProperties"] is False and set(nodo["required"]) == set(nodo["properties"])
            for hijo in nodo["properties"].values():
                revisar(hijo)
        if nodo.get("type") == "array":
            revisar(nodo["items"])
    revisar(extraccion_ia.ESQUEMA_LA)


def test_accion_del_admin(admin_client, borrador, catalogo, monkeypatch):
    datos = respuesta([materia("Cloud Computing")], [materia("Xestión de Infraestruturas", "200190")], [corr([0], [0])])
    monkeypatch.setattr(extraccion_ia, "llamar_modelo_la", lambda contenido: datos)
    r = admin_client.post("/admin/movilidad/versionla/", {"action": "extraer_con_ia", "_selected_action": [borrador.pk]},
                          follow=True)
    assert "1 filas propuestas" in r.content.decode()
    assert borrador.filas.count() == 1
