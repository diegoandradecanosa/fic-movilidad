"""Extracción de las líneas de un ToR con la API de Claude (RF-15), adaptada de lib_extract.py.

Desactivada salvo que haya credenciales (ANTHROPIC_API_KEY). Envía el PDF del ToR a Anthropic,
así que en producción requiere el visto bueno del DPD. Lo extraído se guarda en el ToR en
BORRADOR: el coordinador lo revisa y lo acepta (regla 4 de CLAUDE.md).
"""

import base64
import json

from django.conf import settings
from django.db import transaction

from movilidad.models import EstadoToR, LineaToR, Superada

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


def llamar_modelo(pdf: bytes) -> dict:
    import anthropic

    cliente = _cliente()
    try:
        respuesta = cliente.beta.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": ESQUEMA_TOR}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                    "data": base64.standard_b64encode(pdf).decode()}},
                    {"type": "text", "text": INSTRUCCIONES},
                ],
            }],
        )
    except anthropic.APIConnectionError as e:
        raise IANoDisponible(f"No se pudo conectar con la API de Claude: {e}") from e
    except anthropic.APIStatusError as e:
        raise IANoDisponible(f"La API de Claude devolvió un error {e.status_code}: {e.message}") from e
    if respuesta.stop_reason == "refusal":
        raise IANoDisponible("El modelo declinó procesar el documento; introduce las notas a mano.")
    if respuesta.stop_reason == "max_tokens":
        raise IANoDisponible("La respuesta se cortó (ToR demasiado largo); introduce las notas a mano.")
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
