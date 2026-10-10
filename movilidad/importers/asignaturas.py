"""Importa el catálogo público de asignaturas de la FIC (RF-04).

CSV con columnas: tipo, codigo_plan, titulacion, curso, codigo_materia, materia, creditos, periodo.
"""

import csv
import re
from decimal import Decimal
from pathlib import Path

from django.db import transaction

from movilidad.models import AsignaturaUDC, Cuatrimestre, Titulacion


def parse_cuatrimestre(texto: str) -> str:
    t = (texto or "").strip().lower()
    if m := re.match(r"([12])\.?\s*º", t):
        return Cuatrimestre.C1 if m.group(1) == "1" else Cuatrimestre.C2
    if t.startswith("diferentes"):
        return Cuatrimestre.VARIOS
    return Cuatrimestre.DESCONOCIDO


def curso_de_ruta(ruta) -> str | None:
    """'catalogo_asignaturas_FIC_2026-27.csv' -> '2026/27'."""
    m = re.search(r"(\d{4})-(\d{2})", Path(ruta).name)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def buscar_o_crear_titulacion(codigo_plan: str, nombre: str) -> tuple[Titulacion, bool]:
    t = Titulacion.objects.filter(codigo_plan=codigo_plan).first() if codigo_plan else None
    if t is None:
        t = Titulacion.objects.filter(nombre=nombre).first()
        if t is not None and not t.codigo_plan:
            t.codigo_plan = codigo_plan
            t.save(update_fields=["codigo_plan"])
    if t is not None:
        return t, False
    return Titulacion.objects.create(nombre=nombre, codigo_plan=codigo_plan), True


@transaction.atomic
def importar_asignaturas(ruta, curso_academico: str | None = None) -> dict:
    curso_academico = curso_academico or curso_de_ruta(ruta)
    if not curso_academico:
        raise ValueError("No se deduce el curso académico del nombre del fichero: indica --curso 2026/27")
    r = {"nuevas": 0, "actualizadas": 0, "titulaciones_nuevas": 0, "conflictos": []}
    with Path(ruta).open(encoding="utf-8", newline="") as fh:
        for fila in csv.DictReader(fh):
            titulacion, creada = buscar_o_crear_titulacion(fila["codigo_plan"].strip(), fila["titulacion"].strip())
            r["titulaciones_nuevas"] += creada
            datos = {
                "titulacion": titulacion,
                "tipo": fila["tipo"].strip(),
                "nombre": fila["materia"].strip(),
                "ects": Decimal(fila["creditos"].strip()),
                "curso": fila["curso"].strip(),
                "cuatrimestre": parse_cuatrimestre(fila["periodo"]),
            }
            asignatura = AsignaturaUDC.objects.filter(
                curso_academico=curso_academico, codigo=fila["codigo_materia"].strip()
            ).first()
            if asignatura is None:
                AsignaturaUDC.objects.create(curso_academico=curso_academico, codigo=fila["codigo_materia"].strip(), **datos)
                r["nuevas"] += 1
                continue
            if asignatura.ects != datos["ects"] and asignatura.usada_en_version_firmada():
                r["conflictos"].append(f"{asignatura}: el CSV dice {datos['ects']} ECTS; se mantienen los del LA firmado")
                datos.pop("ects")
            cambios = {k: v for k, v in datos.items() if getattr(asignatura, k) != v}
            if cambios:
                for k, v in cambios.items():
                    setattr(asignatura, k, v)
                asignatura.save()
                r["actualizadas"] += 1
    return r
