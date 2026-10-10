from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command

from movilidad.importers.asignaturas import curso_de_ruta, importar_asignaturas, parse_cuatrimestre
from movilidad.models import AsignaturaUDC, Cuatrimestre, Titulacion

CSV = Path(__file__).resolve().parent.parent / "data" / "catalogo_asignaturas_FIC_2026-27.csv"
CABECERA = "tipo,codigo_plan,titulacion,curso,codigo_materia,materia,creditos,periodo\n"


def test_parse_cuatrimestre():
    assert parse_cuatrimestre("1.º cuadrimestre") == Cuatrimestre.C1
    assert parse_cuatrimestre("2.º cuadrimestre") == Cuatrimestre.C2
    assert parse_cuatrimestre("Diferentes períodos") == Cuatrimestre.VARIOS
    assert parse_cuatrimestre("sin especificar en el PDF") == Cuatrimestre.DESCONOCIDO


def test_curso_de_ruta():
    assert curso_de_ruta(CSV) == "2026/27"
    assert curso_de_ruta("asignaturas.csv") is None


@pytest.mark.django_db
def test_importa_catalogo_de_la_fic():
    r = importar_asignaturas(CSV)
    assert r["nuevas"] == 461 and r["titulaciones_nuevas"] == 14
    assert AsignaturaUDC.objects.filter(curso_academico="2026/27").count() == 461
    assert AsignaturaUDC.objects.filter(cuatrimestre=Cuatrimestre.C1).count() == 208
    assert Titulacion.objects.get(codigo_plan="10009").asignaturas.count() == 85
    tfg = AsignaturaUDC.objects.get(codigo="200245")
    assert (tfg.ects, tfg.curso, tfg.tipo) == (Decimal("12.0"), "4", "Grao")
    # idempotente
    assert importar_asignaturas(CSV) == {"nuevas": 0, "actualizadas": 0, "titulaciones_nuevas": 0, "conflictos": []}


@pytest.mark.django_db
def test_reutiliza_titulacion_por_nombre_y_le_pone_el_codigo(tmp_path):
    t = Titulacion.objects.create(nombre="Grao en Proba")
    ruta = tmp_path / "cat.csv"
    ruta.write_text(CABECERA + "Grao,19999,Grao en Proba,1,299001,Materia de proba,6.0,1.º cuadrimestre\n",
                    encoding="utf-8")
    r = importar_asignaturas(ruta, "2027/28")
    t.refresh_from_db()
    assert r["titulaciones_nuevas"] == 0 and t.codigo_plan == "19999"
    assert AsignaturaUDC.objects.get().curso_academico == "2027/28"


@pytest.mark.django_db
def test_no_cambia_ects_de_una_asignatura_en_un_la_firmado(tmp_path):
    from tests.test_acuerdo_academico import A, dest, version
    from movilidad.models import (Convocatoria, EstadoMovilidad, Estudiante, Institucion, Movilidad, Programa)

    ruta = tmp_path / "cat_2026-27.csv"
    ruta.write_text(CABECERA + "Grao,19999,Grao en Proba,1,299001,Materia,6.0,1.º cuadrimestre\n", encoding="utf-8")
    importar_asignaturas(ruta)
    mov = Movilidad.objects.create(
        estudiante=Estudiante.objects.create(nombre_completo="P, Q", dni_hash="y" * 64, dni_mascara="1"),
        convocatoria=Convocatoria.objects.create(curso="2026/27", plazo=1),
        institucion=Institucion.objects.create(nombre="U", pais="IT"),
        programa=Programa.ERASMUS, estado=EstadoMovilidad.LA_PREPARACION,
    )
    version(mov, filas=[(A, 1, [dest(mov, "X")], [AsignaturaUDC.objects.get()])], fecha_firma_destino=date(2026, 7, 1))
    ruta.write_text(CABECERA + "Grao,19999,Grao en Proba,1,299001,Materia (nome novo),4.5,1.º cuadrimestre\n",
                    encoding="utf-8")
    r = importar_asignaturas(ruta)
    asignatura = AsignaturaUDC.objects.get()
    assert len(r["conflictos"]) == 1 and r["actualizadas"] == 1
    assert (asignatura.ects, asignatura.nombre) == (Decimal(6), "Materia (nome novo)")


@pytest.mark.django_db
def test_comando(capsys):
    call_command("importar_asignaturas", str(CSV))
    assert "Asignaturas nuevas: 461" in capsys.readouterr().out
