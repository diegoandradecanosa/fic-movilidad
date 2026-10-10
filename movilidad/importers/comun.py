"""Utilidades compartidas por los importadores."""

import re
import unicodedata

from movilidad.models import Institucion
from movilidad.paises import codigo_pais


def normalizar_nombre(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"\([^)]*\)", " ", s)        # quita siglas entre paréntesis: "(UDP)"
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def buscar_o_crear_institucion(nombre: str, pais: str) -> Institucion:
    """Empareja por país ISO y nombre normalizado; si un nombre contiene al otro y solo hay un
    candidato en el país, se considera la misma institución ("Universidad Diego Portales" ≈
    "Universidad Diego Portales (UDP)")."""
    nombre = (nombre or "").strip()
    iso = codigo_pais(pais)
    clave = normalizar_nombre(nombre)
    candidatas = list(Institucion.objects.filter(pais=iso))
    exactas = [i for i in candidatas if normalizar_nombre(i.nombre) == clave]
    if exactas:
        return exactas[0]
    parecidas = [
        i for i in candidatas
        if clave and (clave in normalizar_nombre(i.nombre) or normalizar_nombre(i.nombre) in clave)
    ]
    if len(parecidas) == 1:
        return parecidas[0]
    return Institucion.objects.create(nombre=nombre, pais=iso)
