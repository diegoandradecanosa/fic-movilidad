from pathlib import Path

import pytest

from movilidad.importers.destinos import importar_destinos
from movilidad.models import Institucion, Programa, RelacionDestino

CSV = Path(__file__).resolve().parent.parent / "data" / "destinos_FIC_2026-27.csv"


@pytest.mark.django_db
def test_importa_catalogo_de_destinos():
    r = importar_destinos(CSV)
    assert r["nuevas"] == 85
    assert RelacionDestino.objects.count() == 85
    rennes = Institucion.objects.get(nombre="Université de Rennes")
    assert rennes.codigo_erasmus == "F RENNES01"
    assert rennes.relaciones.count() == 2
    assert RelacionDestino.objects.filter(programa=Programa.BILATERAL).count() == 13
    # idempotente
    assert importar_destinos(CSV)["nuevas"] == 0


@pytest.mark.django_db
def test_adjudicacion_reutiliza_institucion_del_catalogo(tmp_path):
    from movilidad.importers.adjudicaciones import importar_adjudicaciones
    from tests.test_importar_adjudicaciones import excel, fila

    importar_destinos(CSV)
    antes = Institucion.objects.count()
    filas = [
        fila("Rey Paz, Iria", "44444444A", 1, "Belgium", "Université de Liège", "Adxudicataria"),
        fila("Mon Vila, Xoán", "55555555B", 1, "Chile", "Universidad Diego Portales", "Adxudicatario",
             programa="Bilateral SM estudios"),
    ]
    importar_adjudicaciones(excel(tmp_path, filas))
    assert Institucion.objects.count() == antes
    assert Institucion.objects.get(nombre="Université de Liège").pais == "BE"
