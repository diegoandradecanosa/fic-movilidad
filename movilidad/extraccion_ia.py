"""Extracción con la API de Claude de las notas de un ToR (RF-15) y de las tablas de un LA (RF-10),
adaptada de lib_extract.py.

Desactivada salvo que haya credenciales (ANTHROPIC_API_KEY). Envía el PDF a Anthropic, así que en
producción requiere el visto bueno del DPD. Lo extraído queda en BORRADOR (el ToR sin aceptar, la
versión del LA sin firmar): el coordinador lo revisa y decide (regla 4 de CLAUDE.md).
"""

import base64
import json

from django.conf import settings
from django.db import transaction

from movilidad.models import AccionFila, AsignaturaDestino, EstadoToR, FilaCorrespondencia, LineaToR, Superada

ESQUEMA_TOR = {
    "type": "object",
    "properties": {
        "nombre_alumno": {"type": "string", "description": "Nombre del estudiante tal como figura en el documento"},
        "descripcion_escala": {"type": "string", "description": "Descripción literal de la escala de calificación"},
        "nota_minima": {"type": "string", "description": "Nota mínima de aprobado, si se indica; si no, cadena vacía"},
        "nota_maxima": {"type": "string", "description": "Nota máxima, si se indica; si no, cadena vacía"},
        "materias": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "codigo": {"type": "string"},
                    "titulo": {"type": "string"},
                    "ects": {"type": "number"},
                    "nota": {"type": "string", "description": "Nota literal, con el separador decimal original"},
                    "superada": {"type": "string", "enum": ["SI", "NO", "DESCONOCIDO"]},
                },
                "required": ["codigo", "titulo", "ects", "nota", "superada"],
                "additionalProperties": False,
            },
        },
        "observaciones": {"type": "string", "description": "Lo que deba revisar una persona (cadena vacía si nada)"},
    },
    "required": ["nombre_alumno", "descripcion_escala", "nota_minima", "nota_maxima", "materias", "observaciones"],
    "additionalProperties": False,
}

INSTRUCCIONES = (
    "Este PDF es un Transcript of Records (certificado de notas) de una universidad extranjera; cada "
    "universidad usa un formato distinto. Extrae todas las materias cursadas con su código, título, "
    "créditos ECTS, la nota tal como aparece (mismo separador decimal; 'ABS', 'NE', '-' si es lo que "
    "figura) y si se superó. Si una materia tiene una nota numérica de suspenso en una convocatoria y "
    "'NE' en otra, la nota es la numérica. Copia literalmente la descripción de la escala de "
    "calificación del documento y, si se indican, la nota mínima de aprobado y la máxima. "
    "No inventes datos: si algo no figura, déjalo vacío y explícalo en observaciones."
)


class IANoDisponible(RuntimeError):
    pass


def disponible() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _cliente():
    if not disponible():
        raise IANoDisponible("La extracción con IA está desactivada: falta ANTHROPIC_API_KEY.")
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise IANoDisponible("Falta el paquete 'anthropic' (pip install -e '.[ia]').") from e
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def llamar_modelo(pdf: bytes, esquema=ESQUEMA_TOR, instrucciones=INSTRUCCIONES) -> dict:
    import anthropic

    cliente = _cliente()
    try:
        respuesta = cliente.beta.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": esquema}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                    "data": base64.standard_b64encode(pdf).decode()}},
                    {"type": "text", "text": instrucciones},
                ],
            }],
        )
    except anthropic.APIConnectionError as e:
        raise IANoDisponible(f"No se pudo conectar con la API de Claude: {e}") from e
    except anthropic.APIStatusError as e:
        raise IANoDisponible(f"La API de Claude devolvió un error {e.status_code}: {e.message}") from e
    if respuesta.stop_reason == "refusal":
        raise IANoDisponible("El modelo declinó procesar el documento; introduce los datos a mano.")
    if respuesta.stop_reason == "max_tokens":
        raise IANoDisponible("La respuesta se cortó (documento demasiado largo); introduce los datos a mano.")
    texto = next(b.text for b in respuesta.content if b.type == "text")
    return json.loads(texto)


@transaction.atomic
def extraer_tor(tor, llamar=llamar_modelo) -> int:
    """Rellena las líneas de un ToR en borrador a partir de su PDF. Devuelve cuántas líneas crea."""
    if tor.estado != EstadoToR.BORRADOR:
        raise ValueError("El ToR ya está aceptado: devuélvelo a borrador para volver a extraerlo.")
    if not tor.pdf:
        raise ValueError("El ToR no tiene PDF.")
    with tor.pdf.open("rb") as fh:
        datos = llamar(fh.read())
    tor.lineas.all().delete()
    for i, m in enumerate(datos["materias"]):
        LineaToR.objects.create(
            tor=tor, orden=i, codigo=m["codigo"][:50], titulo=m["titulo"][:255], ects=m["ects"] or 0,
            nota=m["nota"][:30], superada={"SI": Superada.SI, "NO": Superada.NO}.get(m["superada"], Superada.DESCONOCIDO),
        )
    tor.nombre_en_documento = datos["nombre_alumno"][:200]
    tor.descripcion_escala = datos["descripcion_escala"]
    tor.nota_minima, tor.nota_maxima = datos["nota_minima"][:20], datos["nota_maxima"][:20]
    tor.extraido_con_ia = True
    if datos["observaciones"]:
        tor.notas = f"{tor.notas}\nIA: {datos['observaciones']}".strip()
    tor.save()
    return len(datos["materias"])


# --- acuerdo académico (RF-10) ----------------------------------------------------------------------

def _materia(descripcion):
    return {
        "type": "object",
        "description": descripcion,
        "properties": {
            "codigo": {"type": "string", "description": "Código tal como figura (cadena vacía si no hay)"},
            "titulo": {"type": "string"},
            "ects": {"type": "number"},
            "semestre": {"type": "integer", "enum": [0, 1, 2], "description": "1 (otoño), 2 (primavera) o 0 si no consta"},
            "accion": {"type": "string", "enum": ["ALTA", "BAJA"], "description": "BAJA solo si el documento la elimina"},
        },
        "required": ["codigo", "titulo", "ects", "semestre", "accion"],
        "additionalProperties": False,
    }


ESQUEMA_LA = {
    "type": "object",
    "properties": {
        "nombre_alumno": {"type": "string"},
        "institucion_destino": {"type": "string"},
        "formato": {"type": "string", "enum": ["OLA_EWP", "PDF_ORI", "SICUE", "OTRO"]},
        "fecha_firma_estudiante": {"type": "string", "description": "AAAA-MM-DD o cadena vacía"},
        "fecha_firma_udc": {"type": "string", "description": "AAAA-MM-DD o cadena vacía (sending institution)"},
        "fecha_firma_destino": {"type": "string", "description": "AAAA-MM-DD o cadena vacía (receiving institution)"},
        "materias_destino": {"type": "array", "items": _materia("Materia de la institución de destino (tabla A/A2)")},
        "materias_udc": {"type": "array", "items": _materia("Materia de la UDC que se reconoce (tabla B/B2)")},
        "correspondencias": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "destino": {"type": "array", "items": {"type": "integer"}, "description": "Índices en materias_destino"},
                    "udc": {"type": "array", "items": {"type": "integer"}, "description": "Índices en materias_udc"},
                    "sin_reconocimiento": {"type": "boolean"},
                    "segun_documento": {"type": "boolean", "description": "true si el documento relaciona estas materias"},
                },
                "required": ["destino", "udc", "sin_reconocimiento", "segun_documento"],
                "additionalProperties": False,
            },
        },
        "observaciones": {"type": "string", "description": "Lo que deba revisar una persona (cadena vacía si nada)"},
    },
    "required": ["nombre_alumno", "institucion_destino", "formato", "fecha_firma_estudiante", "fecha_firma_udc",
                 "fecha_firma_destino", "materias_destino", "materias_udc", "correspondencias", "observaciones"],
    "additionalProperties": False,
}

INSTRUCCIONES_LA = (
    "Este PDF es un acuerdo académico (Learning Agreement) de un estudiante de la Facultade de Informática "
    "de la Universidade da Coruña (UDC), institución de origen. Puede ser un Online Learning Agreement de "
    "EWP («Erasmus+ Digital Learning Agreement»), el modelo PDF/Word de la ORI de la UDC, un formato de la "
    "universidad de destino o un preacuerdo SICUE. Extrae:\n"
    "- materias_destino: las de la institución de destino (tabla A; en una modificación «During», tabla A2 "
    "con las añadidas y las eliminadas).\n"
    "- materias_udc: las de la UDC que se reconocen (tabla B o B2), con su código tal como figura "
    "(p. ej. 614G01025 o 200190).\n"
    "- accion: ALTA, salvo las materias que el documento marca como eliminadas (deleted, removed, «-»), "
    "que son BAJA.\n"
    "- semestre: 1 (1st semester, autumn, fall, winter) o 2 (2nd semester, spring, summer); 0 si no consta.\n"
    "- correspondencias: qué materias de destino reconocen qué materias de la UDC, con índices desde 0 en "
    "las dos listas. Si el documento las relaciona (misma fila, numeración, flechas), segun_documento=true. "
    "Si no (el OLA de EWP lista las tablas A y B por separado), propón la correspondencia más probable por "
    "contenido, créditos, semestre y orden, con segun_documento=false. Una materia de destino que no "
    "reconoce ninguna de la UDC (p. ej. un curso de idioma marcado sin reconocimiento) va sola, con udc "
    "vacío y sin_reconocimiento=true. Cada materia aparece en una sola correspondencia y no se mezclan "
    "altas con bajas.\n"
    "- formato y fechas de firma del estudiante, de la UDC (sending) y del destino (receiving) en "
    "AAAA-MM-DD.\n"
    "No inventes datos: lo que no figure, vacío; lo dudoso, en observaciones."
)


def llamar_modelo_la(pdf: bytes) -> dict:
    return llamar_modelo(pdf, esquema=ESQUEMA_LA, instrucciones=INSTRUCCIONES_LA)


def _fecha(texto):
    from datetime import date

    try:
        return date.fromisoformat(texto) if texto else None
    except ValueError:
        return None


def _asignatura_destino(institucion, m, semestre_defecto):
    """La de la institución con el mismo código o nombre; si no hay, se crea (RF-05)."""
    from movilidad.acta import mismo_codigo
    from movilidad.importers.comun import normalizar_nombre

    existentes = list(institucion.asignaturas.all())
    a = next((x for x in existentes if m["codigo"] and mismo_codigo(x.codigo, m["codigo"])), None) or next(
        (x for x in existentes if normalizar_nombre(x.nombre) == normalizar_nombre(m["titulo"])), None)
    if a is None:
        a = AsignaturaDestino.objects.create(
            institucion=institucion, codigo=m["codigo"][:50], nombre=m["titulo"][:255], ects=m["ects"] or 0,
            semestre=m["semestre"] or semestre_defecto)
    return a


@transaction.atomic
def extraer_la(version, llamar=None) -> dict:
    """Propone las filas de una versión del LA en borrador a partir de su PDF.

    Sustituye las filas que tuviera. Devuelve {"filas": n, "avisos": [...]}; los avisos quedan
    también en version.notas y en las observaciones de cada fila.
    """
    from movilidad.acta import nombre_coincide
    from movilidad.catalogo_udc import buscar_asignatura_udc, limpiar_codigo
    from movilidad.importers.comun import normalizar_nombre

    if not version.editable:
        raise ValueError("La versión ya está firmada: para cambiarla, crea una corrección.")
    if not version.pdf:
        raise ValueError("La versión no tiene PDF.")
    with version.pdf.open("rb") as fh:
        datos = (llamar or llamar_modelo_la)(fh.read())

    mov = version.movilidad
    avisos = []
    if datos["nombre_alumno"] and not nombre_coincide(datos["nombre_alumno"], mov.estudiante.nombre_completo):
        avisos.append(f"El nombre del documento («{datos['nombre_alumno']}») no coincide con el del alumno/a.")
    genericas = {"university", "universidad", "universidade", "universita", "universite", "of", "de", "di", "the"}
    en_doc = set(normalizar_nombre(datos["institucion_destino"]).split()) - genericas
    if en_doc and not en_doc & set(normalizar_nombre(mov.institucion.nombre).split()):
        avisos.append(f"La institución del documento («{datos['institucion_destino']}») no parece "
                      f"{mov.institucion.nombre}.")

    semestre_defecto = mov.semestre_inicio if mov.duracion_semestres == 1 else None
    destino = [_asignatura_destino(mov.institucion, m, semestre_defecto) for m in datos["materias_destino"]]
    udc, sin_catalogo = [], {}
    for i, m in enumerate(datos["materias_udc"]):
        a, como = buscar_asignatura_udc(m["codigo"], m["titulo"], mov.estudiante.titulacion)
        udc.append(a)
        if a is None:
            etiqueta = f"{m['codigo']} {m['titulo']}".strip()
            sin_catalogo[i] = f"UDC «{etiqueta}»: {como}"
        elif como.startswith("nombre") and limpiar_codigo(m["codigo"]).startswith("614G") and not a.codigo_oficial:
            # Código antiguo de los OLA: se recupera para el catálogo a medida que aparece.
            a.codigo_oficial = limpiar_codigo(m["codigo"])[:12]
            a.save(update_fields=["codigo_oficial"])

    version.filas.all().delete()
    usados_d, usados_u = set(), set()
    for orden, c in enumerate(datos["correspondencias"]):
        idx_d = [i for i in c["destino"] if 0 <= i < len(destino) and i not in usados_d]
        idx_u = [i for i in c["udc"] if 0 <= i < len(udc) and i not in usados_u]
        if not idx_d and not idx_u:
            continue
        usados_d.update(idx_d)
        usados_u.update(idx_u)
        materias = [datos["materias_destino"][i] for i in idx_d] + [datos["materias_udc"][i] for i in idx_u]
        bajas = [m["accion"] == "BAJA" for m in materias]
        notas = []
        if any(bajas) and not all(bajas):
            notas.append("mezcla altas y bajas: revísala")
        if not c["segun_documento"]:
            notas.append("emparejamiento propuesto por la IA (el documento no lo indica)")
        notas += [sin_catalogo[i] for i in idx_u if i in sin_catalogo]
        semestre = next((m["semestre"] for m in materias if m["semestre"]), None) or mov.semestre_inicio or 1
        fila = FilaCorrespondencia.objects.create(
            version=version, orden=orden, semestre=semestre,
            accion=AccionFila.BAJA if all(bajas) else AccionFila.ALTA,
            sin_reconocimiento=c["sin_reconocimiento"] and not idx_u, observaciones="; ".join(notas)[:300],
        )
        fila.materias_destino.set([destino[i] for i in idx_d])
        fila.materias_udc.set([udc[i] for i in idx_u if udc[i] is not None])

    sueltas = [m["titulo"] for i, m in enumerate(datos["materias_destino"]) if i not in usados_d]
    sueltas += [m["titulo"] for i, m in enumerate(datos["materias_udc"]) if i not in usados_u]
    if sueltas:
        avisos.append(f"Materias sin correspondencia: {', '.join(sueltas)}.")
    if sin_catalogo:
        avisos.append(f"{len(sin_catalogo)} materia(s) UDC sin pareja en el catálogo: complétalas en sus filas.")
    if datos["observaciones"]:
        avisos.append(f"IA: {datos['observaciones']}")

    for campo in ("fecha_firma_estudiante", "fecha_firma_udc", "fecha_firma_destino"):
        if getattr(version, campo) is None:
            setattr(version, campo, _fecha(datos[campo]))
    version.extraido_con_ia = True
    version.notas = "\n".join(avisos)
    version.save()
    return {"filas": version.filas.count(), "avisos": avisos}
