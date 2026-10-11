"""Tests del importador de adjudicaciones con Excel sintéticos (sin datos reales de alumnos)."""

from decimal import Decimal

import openpyxl
import pytest

from movilidad.importers.adjudicaciones import (
    FormatoNoReconocido,
    importar_adjudicaciones,
    parse_estado,
)
from movilidad.models import (
    EstadoMovilidad,
    EstadoSolicitud,
    Estudiante,
    Listado,
    Movilidad,
    Programa,
    Solicitud,
    TransicionNoPermitida,
)

CABECERA = [
    "Centro", "NOME", "DNI", "Titulación", "Programa", "Orde de preferencia", "Ano acad.",
    "Inicio (semestre)", "Duración (semestres previstos)", "País", "Institución", "Observacións ORI",
    "Certificación  de idiomas", "Estado de solicitude - interno", "Nota media", "Plaza ofertada",
    "Prazo da Convocatoria", "Outros méritos (max. 5%)", "Motivos rexeitamento", "Condicións para adxudicación",
]
GEI = "Graduado o Graduada en Ingeniería Informática"


def fila(nombre, dni, orden, pais, inst, estado, nota=6.5, programa="Erasmus fines estudio (SMS)",
         inicio="1º semester 2025/26", duracion=1, plazo="Convocatoria1", condiciones=""):
    return ["Facultad de Informática", nombre, dni, GEI, programa, orden, "2025/26", inicio, duracion,
            pais, inst, "", "EN-CAM-B2", estado, nota, f"Erasmus+ SMS - {inst} - OUT", plazo, "", "", condiciones]


def excel(tmp_path, filas, nombre="adj.xlsx", cabecera=CABECERA, prefijo=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in prefijo:
        ws.append(r)
    ws.append(cabecera)
    for f in filas:
        ws.append(f)
    ruta = tmp_path / nombre
    wb.save(ruta)
    return ruta


FILAS_BASE = [
    fila("Pérez López, Ana", "11111111H", 1, "Italy", "Politecnico di Milano", "Adxudicataria", 7.9),
    fila("Pérez López, Ana", "11111111H", 2, "Portugal", "Universidade de Lisboa", "Rexeitada", 7.9),
    fila("Souto Rey, Brais", "22222222J", 1, "Italy", "Politecnico di Milano", "Suplente 1", "6,25"),
    fila("Souto Rey, Brais", "22222222J", 2, "Romania", "Universitatea din Bucuresti", "Adxudicatario", "6,25",
         duracion=2, condiciones="condicionado a contrato de estudios íntegramente en inglés"),
    fila("Vidal Mato, Carla", "33333333P", 1, "Chile", "Universidad de Talca", "lista de agarda", 5.1,
         programa="Bilateral SM estudios"),
]


@pytest.mark.parametrize("texto,esperado", [
    ("Adxudicatario", (EstadoSolicitud.ADJUDICADA, None)),
    ("adxudicataria", (EstadoSolicitud.ADJUDICADA, None)),
    ("Suplente nº 2", (EstadoSolicitud.SUPLENTE, 2)),
    ("Suplente", (EstadoSolicitud.SUPLENTE, None)),
    ("Rexeitada", (EstadoSolicitud.RECHAZADA, None)),
    ("lista de agarda", (EstadoSolicitud.LISTA_ESPERA, None)),
    ("Non se oferta praza", (EstadoSolicitud.NO_OFERTADA, None)),
    ("algo raro", (EstadoSolicitud.DESCONOCIDO, None)),
])
def test_parse_estado(texto, esperado):
    assert parse_estado(texto) == esperado


@pytest.mark.django_db
def test_importa_solicitudes_y_crea_movilidades(tmp_path):
    informe = importar_adjudicaciones(excel(tmp_path, FILAS_BASE), listado=Listado.PROVISIONAL)

    assert informe.filas == 5
    assert informe.solicitudes_nuevas == 5
    assert informe.movilidades_nuevas == 2
    assert Estudiante.objects.count() == 3

    ana = Movilidad.objects.get(estudiante__nombre_completo="Pérez López, Ana")
    assert ana.institucion.nombre == "Politecnico di Milano"
    assert ana.estado == EstadoMovilidad.ADJUDICADA
    assert ana.convocatoria.curso == "2025/26" and ana.convocatoria.plazo == 1
    assert ana.eventos.count() == 1

    brais = Movilidad.objects.get(estudiante__nombre_completo="Souto Rey, Brais")
    assert brais.duracion_semestres == 2
    assert "inglés" in brais.condiciones

    suplencia = Solicitud.objects.get(estudiante=brais.estudiante, orden_preferencia=1)
    assert suplencia.estado == EstadoSolicitud.SUPLENTE and suplencia.numero_suplente == 1
    assert suplencia.nota_media == Decimal("6.2500")

    carla = Solicitud.objects.get(estudiante__nombre_completo="Vidal Mato, Carla")
    assert carla.programa == Programa.BILATERAL
    assert not Movilidad.objects.filter(estudiante=carla.estudiante).exists()


@pytest.mark.django_db
def test_no_guarda_dni_en_claro(tmp_path):
    importar_adjudicaciones(excel(tmp_path, FILAS_BASE))
    est = Estudiante.objects.get(nombre_completo="Pérez López, Ana")
    assert est.dni_mascara == "11*****1H"
    assert "11111111H" not in est.dni_hash
    for e in Estudiante.objects.all():
        assert len(e.dni_hash) == 64


@pytest.mark.django_db
def test_reimportar_es_idempotente(tmp_path):
    ruta = excel(tmp_path, FILAS_BASE)
    importar_adjudicaciones(ruta)
    informe = importar_adjudicaciones(ruta)
    assert informe.solicitudes_nuevas == 0
    assert informe.solicitudes_actualizadas == 0
    assert informe.movilidades_nuevas == 0
    assert informe.movilidades_actualizadas == 0
    assert Movilidad.objects.count() == 2


@pytest.mark.django_db
def test_definitiva_cambia_destino_y_retira_adjudicaciones(tmp_path):
    importar_adjudicaciones(excel(tmp_path, FILAS_BASE, "prov.xlsx"), listado=Listado.PROVISIONAL)

    # En la definitiva: Brais pasa a Milano (sube la suplencia) y Ana pierde la adjudicación.
    definitiva = [
        fila("Pérez López, Ana", "11111111H", 1, "Italy", "Politecnico di Milano", "Rexeitada", 7.9),
        fila("Pérez López, Ana", "11111111H", 2, "Portugal", "Universidade de Lisboa", "Rexeitada", 7.9),
        fila("Souto Rey, Brais", "22222222J", 1, "Italy", "Politecnico di Milano", "Adxudicatario", 6.25),
        fila("Souto Rey, Brais", "22222222J", 2, "Romania", "Universitatea din Bucuresti", "Rexeitado", 6.25),
        fila("Vidal Mato, Carla", "33333333P", 1, "Chile", "Universidad de Talca", "lista de agarda", 5.1,
             programa="Bilateral SM estudios"),
    ]
    informe = importar_adjudicaciones(excel(tmp_path, definitiva, "def.xlsx"), listado=Listado.DEFINITIVA)

    brais = Movilidad.objects.get(estudiante__nombre_completo="Souto Rey, Brais")
    assert brais.institucion.nombre == "Politecnico di Milano"
    assert brais.solicitud.listado == Listado.DEFINITIVA

    ana = Movilidad.objects.get(estudiante__nombre_completo="Pérez López, Ana")
    assert ana.estado == EstadoMovilidad.ADJUDICACION_RETIRADA
    assert any("retirada" in a for a in informe.avisos)


@pytest.mark.django_db
def test_no_cambia_destino_si_la_movilidad_ya_avanzo(tmp_path):
    importar_adjudicaciones(excel(tmp_path, FILAS_BASE, "a.xlsx"))
    brais = Movilidad.objects.get(estudiante__nombre_completo="Souto Rey, Brais")
    brais.cambiar_estado(EstadoMovilidad.ACEPTADA)

    cambiada = [f[:] for f in FILAS_BASE]
    cambiada[2][13] = "Adxudicatario"   # Milano adjudicada
    cambiada[3][13] = "Rexeitado"       # Bucarest ya no
    informe = importar_adjudicaciones(excel(tmp_path, cambiada, "b.xlsx"))

    brais.refresh_from_db()
    assert brais.institucion.nombre == "Universitatea din Bucuresti"
    assert any("revísalo" in a for a in informe.avisos)


@pytest.mark.django_db
def test_cabecera_con_columnas_extra_y_filas_previas(tmp_path):
    cab = ["Form", "Relation: Direction"] + CABECERA
    filas = [["Conv1", "Outgoing"] + f for f in FILAS_BASE[:2]]
    ruta = excel(tmp_path, filas, cabecera=cab, prefijo=[["LISTAXE PROVISORIA"], []])
    informe = importar_adjudicaciones(ruta)
    assert informe.filas == 2
    assert Movilidad.objects.count() == 1


@pytest.mark.django_db
def test_formato_no_reconocido(tmp_path):
    ruta = excel(tmp_path, [["x"]], cabecera=["NOME", "Otra cosa"])
    with pytest.raises(FormatoNoReconocido):
        importar_adjudicaciones(ruta)


@pytest.mark.django_db
def test_transiciones_de_estado(tmp_path):
    importar_adjudicaciones(excel(tmp_path, FILAS_BASE))
    ana = Movilidad.objects.get(estudiante__nombre_completo="Pérez López, Ana")
    with pytest.raises(TransicionNoPermitida):
        ana.cambiar_estado(EstadoMovilidad.ACTA_FIRMADA)
    ana.cambiar_estado(EstadoMovilidad.ACEPTADA, nota="Anexo IV recibido")
    ana.cambiar_estado(EstadoMovilidad.LA_PREPARACION)
    assert ana.eventos.count() == 3
    assert ana.eventos.first().estado_nuevo == EstadoMovilidad.LA_PREPARACION


# --- formato de 2026/27 (sin «Programa» ni «Ano acad.», columnas renombradas) ----------------------

CABECERA_2026 = [
    "Centro UDC", "NOME", "DNI", "Titulación UDC", "Inicio periodo", "Nº de sem.", "Orde prioridade",
    "País destino", "Institución destino", "Oferta de praza", "Observacións ORI", "Certificación idiomas",
    "Condicións para adxudicación", "Estado de solicitude-interno", "Nota media", "Convocatoria",
    "Outros méritos", "Motivos rexeitamento",
]


def fila_2026(nombre, dni, orden, pais, inst, estado, oferta=None, inicio="1º semester 2026/27", sem=2):
    oferta = oferta or f"Erasmus+ SMS - {inst} - OUT - F. Informática - Grao en Enxeñaría Informática (longo)"
    return ["Facultade de Informática", nombre, dni, GEI, inicio, sem, orden, pais, inst, oferta, "", "B2", "",
            estado, "7,1", "Convocatoria1", "", ""]


@pytest.mark.django_db
def test_formato_2026_27(tmp_path):
    filas = [
        fila_2026("Mera Sar, Lois", "66666666Q", 1, "Poland", "Uniwersytet Ficticio", "adxudicatario"),
        fila_2026("Mera Sar, Lois", "66666666Q", 2, "Norway", "Universitetet Ficticio", "rexeitado"),
        fila_2026("Pena Gil, Sabela", "77777777B", 1, "Poland", "Uniwersytet Ficticio", "listaxe de agarda 1"),
        fila_2026("Rial Vaz, Iago", "88888888Y", 1, "Chile", "Universidad Ficticia", "Suplente1",
                  oferta="Bilateral SM estudios - Universidad Ficticia - OUT", inicio="2º semester 2026/27", sem=1),
    ]
    # la hoja real del 1.er plazo arrastra muchísimas filas vacías al final
    ruta = excel(tmp_path, filas + [[None] * len(CABECERA_2026)] * 300 + [fila_2026("Fantasma, X", "1X", 1, "Peru", "U", "x")],
                 cabecera=CABECERA_2026)
    informe = importar_adjudicaciones(ruta)
    assert informe.filas == 4  # se deja de leer tras 200 filas vacías seguidas
    lois = Solicitud.objects.get(estudiante__nombre_completo="Mera Sar, Lois", orden_preferencia=1)
    assert (lois.convocatoria.curso, lois.convocatoria.plazo) == ("2026/27", 1)
    assert lois.programa == Programa.ERASMUS and len(lois.programa_texto) <= 100
    assert (lois.semestre_inicio, lois.duracion_semestres, lois.estado) == (1, 2, EstadoSolicitud.ADJUDICADA)
    assert Solicitud.objects.get(estudiante__nombre_completo="Pena Gil, Sabela").estado == EstadoSolicitud.LISTA_ESPERA
    iago = Solicitud.objects.get(estudiante__nombre_completo="Rial Vaz, Iago")
    assert (iago.programa, iago.estado, iago.numero_suplente, iago.semestre_inicio) == (
        Programa.BILATERAL, EstadoSolicitud.SUPLENTE, 1, 2)
    assert Movilidad.objects.get().estudiante.nombre_completo == "Mera Sar, Lois"


@pytest.mark.django_db
def test_sin_programa_ni_plaza_no_se_reconoce(tmp_path):
    cabecera = [c for c in CABECERA_2026 if c != "Oferta de praza"]
    fila = fila_2026("A, B", "11111111H", 1, "Poland", "U", "adxudicatario")
    del fila[CABECERA_2026.index("Oferta de praza")]
    with pytest.raises(FormatoNoReconocido, match="programa"):
        importar_adjudicaciones(excel(tmp_path, [fila], cabecera=cabecera))
