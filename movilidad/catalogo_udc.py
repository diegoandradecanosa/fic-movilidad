"""Búsqueda de una materia UDC del catálogo a partir de lo que pone un LA o un acta.

Los LA traen el código vigente (200190), el antiguo de los OLA (614G01025) o solo el nombre, a
veces en castellano («Gestión de Proyectos») cuando el catálogo está en gallego («Xestión de
Proxectos»). Orden: código vigente, código antiguo ya conocido y, por último, nombre dentro del
plan de estudios del alumno.
"""

import re
from difflib import SequenceMatcher

from movilidad.importers.comun import normalizar_nombre
from movilidad.models import AsignaturaUDC

# Castellano → gallego: solo las palabras que cambian en los nombres de materias de la FIC.
CASTELLANO_GALLEGO = {
    "gestion": "xestion", "proyectos": "proxectos", "proyecto": "proxecto", "infraestructuras": "infraestruturas",
    "legislacion": "lexislacion", "seguridad": "seguridade", "hombre": "persoa", "persona": "persoa",
    "y": "e", "del": "do", "la": "a", "las": "as", "los": "os", "el": "o", "ingenieria": "enxenaria",
    "diseno": "deseno", "aprendizaje": "aprendizaxe", "automatico": "automatica", "inteligentes": "intelixentes",
    "inteligente": "intelixente", "inteligencia": "intelixencia", "lenguaje": "linguaxe", "lenguajes": "linguaxes",
    "procesamiento": "procesamento", "imagen": "imaxe", "estadisticos": "estatisticos", "estadistica": "estatistica",
    "estadistico": "estatistico", "desarrollo": "desenvolvemento", "calidad": "calidade", "pruebas": "probas",
    "estructuras": "estruturas", "algebra": "alxebra", "logica": "loxica", "tecnologias": "tecnoloxias",
    "tecnologia": "tecnoloxia", "almacenamiento": "almacenamento", "conocimiento": "conecemento",
    "razonamiento": "razoamento", "probabilidad": "probabilidade", "analisis": "analise", "sociedad": "sociedade",
    "juegos": "xogos", "aplicaciones": "aplicacions", "moviles": "mobiles", "comunicaciones": "comunicacions",
    "tiempo": "tempo", "metodologias": "metodoloxias", "integracion": "integracion", "aseguramiento": "aseguramento",
    "requisitos": "requisitos", "herramientas": "ferramentas", "despliegue": "despregamento",
    "dependencia": "dependencia", "empresa": "empresa", "emprendimiento": "emprendemento", "servicios": "servizos",
    "hogar": "fogar", "edificios": "edificios", "ciudades": "cidades", "vision": "vision", "computador": "computador",
}
ROMANOS = {"i", "ii", "iii", "iv", "1", "2", "3", "4"}
# Prefijo del código antiguo de los OLA → código de plan del catálogo.
PLAN_POR_CODIGO_ANTIGUO = {"614G01": "10009", "614G02": "10011", "614G03": "10084"}
PLAN_POR_TITULACION = {"informatica": "10009", "gei": "10009", "datos": "10011", "gced": "10011",
                       "artificial": "10084", "gia": "10084"}
UMBRAL = 0.75
MARGEN = 0.03


def a_gallego(nombre: str) -> str:
    return " ".join(CASTELLANO_GALLEGO.get(p, p) for p in normalizar_nombre(nombre).split())


def parecido(a: str, b: str) -> float:
    x, y = a_gallego(a), a_gallego(b)
    r = SequenceMatcher(None, x, y).ratio()
    if set(x.split()) & ROMANOS != set(y.split()) & ROMANOS:
        r -= 0.2  # «Aprendizaxe Automática II» no es la III
    return r


def limpiar_codigo(codigo: str) -> str:
    return re.sub(r"\s+", "", codigo or "").upper()


def plan_de(titulacion, codigo: str = "") -> str | None:
    codigo = limpiar_codigo(codigo)
    for prefijo, plan in PLAN_POR_CODIGO_ANTIGUO.items():
        if codigo.startswith(prefijo):
            return plan
    if titulacion is None:
        return None
    if titulacion.codigo_plan:
        return titulacion.codigo_plan
    palabras = normalizar_nombre(titulacion.nombre).split()
    return next((plan for clave, plan in PLAN_POR_TITULACION.items() if clave in palabras), None)


def _preferir_plan(candidatas, plan):
    if plan:
        del_plan = [a for a in candidatas if a.titulacion and a.titulacion.codigo_plan == plan]
        candidatas = del_plan or candidatas
    return sorted(candidatas, key=lambda a: a.curso_academico, reverse=True)[0]


def buscar_asignatura_udc(codigo: str, nombre: str, titulacion=None):
    """(AsignaturaUDC o None, cómo se encontró / por qué no)."""
    codigo = limpiar_codigo(codigo)
    plan = plan_de(titulacion, codigo)
    q = AsignaturaUDC.objects.select_related("titulacion")
    if codigo:
        por_codigo = list(q.filter(codigo=codigo)) or list(q.filter(codigo_oficial=codigo))
        if por_codigo:
            return _preferir_plan(por_codigo, plan), "código"
    if not nombre:
        return None, "sin código conocido ni nombre"
    candidatas = list(q.filter(titulacion__codigo_plan=plan)) if plan else list(q)
    puntuadas = sorted(((parecido(nombre, a.nombre), a) for a in candidatas), key=lambda t: -t[0])
    # El mismo nombre en varios cursos académicos del catálogo no es ambigüedad.
    distintas = []
    for p, a in puntuadas:
        if not any(normalizar_nombre(a.nombre) == normalizar_nombre(b.nombre) for _, b in distintas):
            distintas.append((p, a))
    if not distintas or distintas[0][0] < UMBRAL:
        mejor = f" (la más parecida: «{distintas[0][1].nombre}»)" if distintas else ""
        return None, f"no está en el catálogo{mejor}"
    if len(distintas) > 1 and distintas[0][0] - distintas[1][0] < MARGEN:
        return None, f"ambigua entre «{distintas[0][1].nombre}» y «{distintas[1][1].nombre}»"
    elegida = _preferir_plan([a for p, a in puntuadas if normalizar_nombre(a.nombre)
                              == normalizar_nombre(distintas[0][1].nombre)], plan)
    return elegida, f"nombre ({distintas[0][0]:.2f})"
