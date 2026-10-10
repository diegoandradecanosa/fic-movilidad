"""Tests del bloque 2: versiones del LA, LA vigente, inmutabilidad y validaciones RN-01…RN-07.

Todo con datos sintéticos creados aquí (sin alumnos reales).
"""

from datetime import date
from decimal import Decimal

import pytest
from django.db import transaction

from movilidad.la_vigente import la_vigente
from movilidad.models import (
    AccionFila,
    AsignaturaDestino,
    AsignaturaUDC,
    Convocatoria,
    Cuatrimestre,
    EstadoMovilidad,
    EstadoVersionLA,
    Estudiante,
    EventoMovilidad,
    FilaCorrespondencia,
    FormatoLA,
    Institucion,
    ModificacionNoPermitida,
    Movilidad,
    Programa,
    TipoVersionLA,
    Titulacion,
    VersionLA,
)
from movilidad.validaciones_la import sumar_meses, validar

pytestmark = pytest.mark.django_db


# --- datos sintéticos -----------------------------------------------------------------------------

@pytest.fixture
def mov():
    inst = Institucion.objects.create(nombre="Universidade Ficticia", pais="PT")
    est = Estudiante.objects.create(nombre_completo="Proba Exemplo, Uxía", dni_hash="x" * 64, dni_mascara="00*****0T")
    return Movilidad.objects.create(
        estudiante=est, convocatoria=Convocatoria.objects.create(curso="2026/27", plazo=1),
        institucion=inst, programa=Programa.ERASMUS, semestre_inicio=1, duracion_semestres=1,
        estado=EstadoMovilidad.LA_PREPARACION,
    )


@pytest.fixture
def gei():
    return Titulacion.objects.create(nombre="Grao en Enxeñaría Informática (proba)", codigo_plan="10009")


def udc(gei, codigo, ects=6, cuatrimestre=Cuatrimestre.C1):
    return AsignaturaUDC.objects.create(
        curso_academico="2026/27", titulacion=gei, codigo=codigo, nombre=f"UDC {codigo}",
        ects=Decimal(ects), cuatrimestre=cuatrimestre,
    )


def dest(mov, nombre, ects=6, horas=None):
    return AsignaturaDestino.objects.create(
        institucion=mov.institucion, nombre=nombre, ects=Decimal(ects),
        horas_totales=Decimal(horas) if horas is not None else None,
    )


def version(mov, tipo=TipoVersionLA.BEFORE, filas=(), firmar=True, **kw):
    """filas: tuplas (accion, semestre, [destino], [udc]) o con un 5.º elemento sin_reconocimiento."""
    kw.setdefault("formato", FormatoLA.OLA_EWP)
    if firmar:
        kw.setdefault("fecha_firma_estudiante", date(2026, 7, 1))
        kw.setdefault("fecha_firma_udc", date(2026, 7, 2))
        kw.setdefault("fecha_firma_destino", date(2026, 7, 3))
    v = VersionLA.objects.create(movilidad=mov, tipo=tipo, **kw)
    for i, (accion, semestre, d, u, *resto) in enumerate(filas):
        f = FilaCorrespondencia.objects.create(
            version=v, orden=i, semestre=semestre, accion=accion, sin_reconocimiento=bool(resto and resto[0])
        )
        f.materias_destino.set(d)
        f.materias_udc.set(u)
    if firmar:
        v.firmar()
    return v


A, B = AccionFila.ALTA, AccionFila.BAJA


@pytest.fixture
def before(mov, gei):
    """Before semestral equilibrado: 3 × (6 destino → 6 UDC) = 18 / 18."""
    m = {f"d{i}": dest(mov, f"Destino {i}") for i in range(1, 4)}
    m.update({f"u{i}": udc(gei, f"20000{i}") for i in range(1, 4)})
    version(mov, filas=[(A, 1, [m[f"d{i}"]], [m[f"u{i}"]]) for i in range(1, 4)])
    return m


def reglas(avisos):
    return [a.regla for a in avisos]


# --- versiones e inmutabilidad --------------------------------------------------------------------

def test_numero_de_version_correlativo(mov):
    v1 = version(mov, firmar=False)
    v2 = version(mov, tipo=TipoVersionLA.DURING_S1, firmar=False)
    assert (v1.numero, v2.numero) == (1, 2)


def test_version_firmada_es_inmutable(mov, before):
    v = mov.versiones_la.get()
    v.motivo = "otro"
    with pytest.raises(ModificacionNoPermitida):
        v.save()
    with pytest.raises(ModificacionNoPermitida):
        v.delete()
    fila = v.filas.first()
    with pytest.raises(ModificacionNoPermitida):
        fila.save()
    with pytest.raises(ModificacionNoPermitida):
        fila.delete()
    nova = dest(mov, "Nova")
    with pytest.raises(ModificacionNoPermitida), transaction.atomic():
        fila.materias_destino.add(nova)
    with pytest.raises(ModificacionNoPermitida), transaction.atomic():
        before["d1"].filas_destino.clear()
    assert list(fila.materias_destino.all()) == [before["d1"]]


def test_firmada_solo_puede_invalidarse(mov, before):
    v = mov.versiones_la.get()
    v.estado = EstadoVersionLA.BORRADOR
    with pytest.raises(ModificacionNoPermitida):
        v.save()
    v = VersionLA.objects.get(pk=v.pk)
    v.estado, v.motivo_invalidacion = EstadoVersionLA.INVALIDADA, "OLA aprobado tras iniciar la movilidad"
    v.save()
    v.estado = EstadoVersionLA.FIRMADA
    with pytest.raises(ModificacionNoPermitida):
        v.save()


def test_borrador_se_puede_editar_y_borrar(mov, before):
    v = version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(A, 1, [before["d1"]], [])], firmar=False)
    v.motivo = "cambio de horario"
    v.save()
    v.filas.first().materias_udc.add(before["u1"])
    v.delete()
    assert not VersionLA.objects.filter(pk=v.pk).exists()


def test_ects_de_asignatura_bloqueados_tras_firmar(mov, gei):
    d, u = dest(mov, "Redes"), udc(gei, "200999")
    d.ects = Decimal(5)
    d.save()  # todavía no está en ningún LA firmado
    version(mov, filas=[(A, 1, [d], [u])])
    d.ects = Decimal(6)
    with pytest.raises(ModificacionNoPermitida):
        d.save()
    u.ects = Decimal(4.5)
    with pytest.raises(ModificacionNoPermitida):
        u.save()
    d.ects = Decimal(5)
    d.nombre = "Redes de Computadores"
    d.save()  # otros campos sí se pueden corregir


# --- firmar avanza la movilidad -------------------------------------------------------------------

def test_firmar_before_pasa_a_la_firmado(mov):
    version(mov)
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.LA_FIRMADO
    ev = EventoMovilidad.objects.get(movilidad=mov)
    assert (ev.estado_anterior, ev.estado_nuevo) == (EstadoMovilidad.LA_PREPARACION, EstadoMovilidad.LA_FIRMADO)


def test_firmar_during_cierra_la_modificacion(mov):
    mov.estado = EstadoMovilidad.MODIFICACION_ABIERTA
    mov.save()
    version(mov, tipo=TipoVersionLA.DURING_S1)
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.EN_MOVILIDAD


def test_firmar_no_toca_otros_estados(mov):
    mov.estado = EstadoMovilidad.ACEPTADA
    mov.save()
    version(mov)
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.ACEPTADA
    assert not EventoMovilidad.objects.exists()


def test_solo_se_firma_un_borrador(mov):
    v = version(mov)
    with pytest.raises(ModificacionNoPermitida):
        v.firmar()


# --- LA vigente -----------------------------------------------------------------------------------

def test_la_vigente_aplica_altas_y_bajas_en_orden(mov, before):
    nueva = dest(mov, "Destino 4")
    version(mov, tipo=TipoVersionLA.DURING_S1, filas=[
        (B, 1, [before["d2"]], [before["u2"]]),
        (A, 1, [nueva], [before["u2"]]),
    ])
    la = la_vigente(mov)
    assert [(g.materias_destino, g.materias_udc) for g in la.grupos] == [
        ([before["d1"]], [before["u1"]]),
        ([before["d3"]], [before["u3"]]),
        ([nueva], [before["u2"]]),
    ]
    assert la.ects_udc() == la.ects_destino() == 18
    assert la.incidencias == []
    assert [b.asignatura for b in la.bajas] == [before["d2"], before["u2"]]


def test_borrador_e_invalidada_no_cuentan(mov, before):
    extra = dest(mov, "Extra")
    borrador = version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(A, 1, [extra], [])], firmar=False)
    assert len(la_vigente(mov).grupos) == 3
    assert len(la_vigente(mov, incluir=borrador).grupos) == 4  # vista previa para validar
    borrador.firmar()
    borrador.estado, borrador.motivo_invalidacion = EstadoVersionLA.INVALIDADA, "La ORI la anula"
    borrador.save()
    assert len(la_vigente(mov).grupos) == 3


def test_correccion_sustituye_a_la_version_corregida_en_su_lugar(mov, before):
    before_v = mov.versiones_la.get()
    during = version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(B, 1, [before["d3"]], [before["u3"]])])
    d4 = dest(mov, "Destino 4")
    corregido = version(mov, corrige=before_v, filas=[
        (A, 1, [before["d1"]], [before["u1"]]),
        (A, 1, [before["d3"]], [before["u3"]]),
        (A, 1, [d4], [before["u2"]]),
    ])
    la = la_vigente(mov)
    assert la.versiones == [corregido, during]
    assert [g.materias_destino for g in la.grupos] == [[before["d1"]], [d4]]
    assert before_v.estado == EstadoVersionLA.FIRMADA  # la original se conserva


def test_baja_parcial_de_un_dos_por_uno_avisa(mov, gei):
    a, b, x = dest(mov, "A", 3), dest(mov, "B", 3), udc(gei, "200500")
    version(mov, filas=[(A, 1, [a, b], [x])])
    version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(B, 1, [b], [])])
    la = la_vigente(mov)
    assert [(g.materias_destino, g.materias_udc) for g in la.grupos] == [([a], [x])]
    assert len(la.incidencias) == 1 and "solo una parte" in la.incidencias[0]


def test_baja_de_materia_que_no_esta_avisa(mov, before):
    ajena = dest(mov, "No estaba")
    version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(B, 1, [ajena], [])])
    assert "no está en el LA vigente" in la_vigente(mov).incidencias[0]


def test_baja_completa_elimina_el_grupo_sin_aviso(mov, before):
    version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(B, 1, [before["d1"]], [before["u1"]])])
    la = la_vigente(mov)
    assert len(la.grupos) == 2 and la.incidencias == []


# --- validaciones RN-01…RN-07 (avisos) ------------------------------------------------------------

def test_la_equilibrado_no_tiene_avisos(mov, before):
    assert validar(mov) == []


def test_rn01_minimo_ects(mov, gei):
    version(mov, filas=[(A, 1, [dest(mov, f"D{i}")], [udc(gei, f"20010{i}")]) for i in range(2)])
    avisos = validar(mov)
    assert "RN-01" in reglas(avisos)
    assert "TFG" in next(a.mensaje for a in avisos if a.regla == "RN-01")


def test_rn01_anual_exige_36(mov, before):
    mov.duracion_semestres = 2
    assert "RN-01" in reglas(validar(mov))


def test_rn02_maximo(mov, gei):
    # 36 UDC con 36 en destino: supera 30 pero destino también → sin aviso RN-02
    version(mov, filas=[(A, 1, [dest(mov, f"D{i}")], [udc(gei, f"20020{i}")]) for i in range(6)])
    assert "RN-02" not in reglas(validar(mov))
    # 42 UDC: por encima de 39
    version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(A, 1, [dest(mov, "D6")], [udc(gei, "200206")])])
    assert "RN-02" in reglas(validar(mov))


def test_rn02_supera_30_con_destino_de_30(mov, gei):
    version(mov, filas=[(A, 1, [dest(mov, f"D{i}")], [udc(gei, f"20030{i}")]) for i in range(5)]
            + [(A, 1, [], [udc(gei, "200399"), udc(gei, "200398", 3)])])
    assert "RN-02" in reglas(validar(mov))


def test_rn03_diferencia_de_creditos(mov, gei):
    version(mov, filas=[(A, 1, [dest(mov, "D", 15)], [udc(gei, "200401", 18)]),
                        (A, 1, [dest(mov, "Curso de idioma", 6)], [], True)])
    avisos = validar(mov)
    assert "RN-03" in reglas(avisos)  # 18 UDC vs 15 destino: el curso sin reconocimiento no cuenta
    assert "diferencia de 3 ECTS" in next(a.mensaje for a in avisos if a.regla == "RN-03")


def test_rn04_horas_por_credito(mov, gei):
    ok = dest(mov, "Por horas bien", 6, horas=51)
    mal = dest(mov, "Por horas mal", 6, horas=85)
    version(mov, filas=[(A, 1, [ok], [udc(gei, "200501")]), (A, 1, [mal], [udc(gei, "200502")]),
                        (A, 1, [dest(mov, "D")], [udc(gei, "200503")])])
    avisos = [a for a in validar(mov) if a.regla == "RN-04"]
    assert len(avisos) == 1 and "10 ECTS" in avisos[0].mensaje


def test_rn05_plazo_y_numero_de_modificaciones(mov, before):
    mov.fecha_llegada = date(2026, 9, 10)
    mov.save()
    version(mov, tipo=TipoVersionLA.DURING_S1, fecha_solicitud=date(2026, 10, 10))
    assert "RN-05" not in reglas(validar(mov))
    version(mov, tipo=TipoVersionLA.DURING_S1, fecha_solicitud=date(2026, 10, 11))
    mensajes = [a.mensaje for a in validar(mov) if a.regla == "RN-05"]
    assert any("2 modificaciones" in m for m in mensajes)
    assert any("más de un mes" in m for m in mensajes)


def test_rn05_segundo_semestre_usa_inicio_s2(mov, before):
    mov.duracion_semestres, mov.fecha_llegada, mov.fecha_inicio_s2 = 2, date(2026, 9, 1), date(2027, 2, 1)
    version(mov, tipo=TipoVersionLA.DURING_S2, fecha_solicitud=date(2027, 2, 20))
    assert "RN-05" not in reglas(validar(mov))
    mov.fecha_inicio_s2 = None
    assert any("no se puede comprobar" in a.mensaje for a in validar(mov) if a.regla == "RN-05")


def test_rn06_firma_de_destino_antes_de_la_llegada(mov, before):
    mov.fecha_llegada = date(2026, 9, 1)
    assert "RN-06" not in reglas(validar(mov))  # destino firmó el 03/07
    mov.fecha_llegada = date(2026, 7, 3)
    assert "RN-06" in reglas(validar(mov))


def test_rn07_materia_de_primer_cuatrimestre_en_el_segundo(mov, gei):
    c1, c2 = udc(gei, "200701", cuatrimestre=Cuatrimestre.C1), udc(gei, "200702", cuatrimestre=Cuatrimestre.C2)
    mov.semestre_inicio = 2
    version(mov, filas=[(A, 2, [dest(mov, "X")], [c1]), (A, 2, [dest(mov, "Y")], [c2])])
    avisos = [a for a in validar(mov) if a.regla == "RN-07"]
    assert len(avisos) == 1 and c1.nombre in avisos[0].mensaje


def test_rn07_baja_en_s1_y_alta_en_s2(mov, gei):
    mov.duracion_semestres = 2
    c1 = udc(gei, "200801", cuatrimestre=Cuatrimestre.C1)
    d1, d2 = dest(mov, "X"), dest(mov, "Y")
    version(mov, filas=[(A, 1, [d1], [c1])])
    assert "RN-07" not in reglas(validar(mov))
    version(mov, tipo=TipoVersionLA.DURING_S1, filas=[(B, 1, [d1], [c1])])
    version(mov, tipo=TipoVersionLA.DURING_S2, filas=[(A, 2, [d2], [c1])])
    assert "RN-07" in reglas(validar(mov))


def test_avisos_de_un_borrador_antes_de_firmar(mov, before):
    borrador = version(mov, tipo=TipoVersionLA.DURING_S1, firmar=False,
                       filas=[(B, 1, [before["d1"]], [before["u1"]])])
    assert validar(mov) == []
    assert "RN-01" in reglas(validar(mov, version=borrador))  # quedaría en 12 ECTS


def test_falta_fecha_de_firma(mov):
    version(mov, fecha_firma_destino=None)
    assert any("destino" in a.mensaje for a in validar(mov) if a.regla == "FIRMAS")


def test_sumar_meses_fin_de_mes():
    assert sumar_meses(date(2027, 1, 31)) == date(2027, 2, 28)
    assert sumar_meses(date(2026, 12, 15)) == date(2027, 1, 15)


# --- admin ----------------------------------------------------------------------------------------

@pytest.fixture
def cliente(client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser("coord", "coord@example.org", "x"))
    return client


def test_admin_vista_la_vigente(cliente, mov, before):
    r = cliente.get(f"/admin/movilidad/movilidad/{mov.pk}/la-vigente/")
    assert r.status_code == 200
    assert "Destino 1" in r.content.decode() and "Sin avisos" in r.content.decode()
    assert "<form" not in r.content.decode().split('id="content"')[1]


def test_admin_paginas_de_movilidad_y_version(cliente, mov, before):
    v = mov.versiones_la.get()
    assert cliente.get(f"/admin/movilidad/movilidad/{mov.pk}/change/").status_code == 200
    r = cliente.get(f"/admin/movilidad/versionla/{v.pk}/change/")
    assert r.status_code == 200
    assert 'name="motivo"' not in r.content.decode()  # firmada: solo lectura
    assert cliente.get("/admin/movilidad/asignaturaudc/").status_code == 200
    assert cliente.get("/admin/movilidad/asignaturadestino/").status_code == 200


def test_admin_crear_version_firmada_avanza_la_movilidad(cliente, mov):
    datos = {
        "movilidad": mov.pk, "tipo": TipoVersionLA.BEFORE, "formato": FormatoLA.PDF_ORI,
        "estado": EstadoVersionLA.FIRMADA, "fecha_firma_udc": "2026-07-02",
        "filas-TOTAL_FORMS": "0", "filas-INITIAL_FORMS": "0",
    }
    r = cliente.post("/admin/movilidad/versionla/add/", datos)
    assert r.status_code == 302, r.content.decode()[:2000]
    v = VersionLA.objects.get()
    assert (v.numero, v.estado) == (1, EstadoVersionLA.FIRMADA)
    mov.refresh_from_db()
    assert mov.estado == EstadoMovilidad.LA_FIRMADO


def test_admin_no_deja_volver_a_borrador(cliente, mov, before):
    v = mov.versiones_la.get()
    r = cliente.post(f"/admin/movilidad/versionla/{v.pk}/change/", {
        "estado": EstadoVersionLA.BORRADOR, "motivo_invalidacion": "",
        "filas-TOTAL_FORMS": "0", "filas-INITIAL_FORMS": "0",
    })
    assert r.status_code == 200 and "no puede pasar" in r.content.decode()
    assert VersionLA.objects.get(pk=v.pk).estado == EstadoVersionLA.FIRMADA
