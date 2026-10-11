"""Conversión de notas a la escala española (RN-12, RN-13, RF-16).

Port de lib_grades.py (skill acta-reconocimiento) sobre el modelo EscalaNotas, con sus reglas:

- La escala de un país se elige por cobertura de las notas observadas en el ToR; a igualdad, la
  más sencilla (menos valores). Si el ToR declara mínimo/máximo, se premia la escala que coincide.
  Si otra escala compatible daría un resultado distinto, se avisa (escala ambigua).
- Una nota que la escala no contiene es «Suspenso» (las tablas oficiales solo listan aprobados),
  salvo las marcas de ausencia (ABS, NE, «-», EXC, vacía), que son «No presentado».
- «2 por 1»: media ponderada por ECTS. Con la misma escala se pondera la nota original y se
  convierte una vez; si no se puede (letras, escalas distintas), se convierte cada una y se pondera.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

SUSPENSO = "Suspenso"
NO_PRESENTADO = "No presentado"


def parse_numero(valor):
    if valor is None:
        return None
    if isinstance(valor, (int, float, Decimal)):
        return float(valor)
    try:
        return float(str(valor).strip().replace(",", "."))
    except ValueError:
        return None


def _valores_numericos(escala) -> set:
    return {round(v, 2) for v in (parse_numero(k) for k in escala.calificaciones) if v is not None}


def convertir(escala, nota):
    """Equivalencia oficial de `nota` en `escala` ({equivalente_5_10, …}) o None si no está."""
    if escala is None or nota is None:
        return None
    texto = str(nota).strip().lower()
    if not texto:
        return None
    # Coincidencia exacta de texto primero: «30L» (Italia, con lode) no es el «30» liso.
    for clave, valor in escala.calificaciones.items():
        if str(clave).strip().lower() == texto:
            return valor
    numero = parse_numero(nota)
    if numero is None:
        return None
    for clave, valor in escala.calificaciones.items():
        k = parse_numero(clave)
        if k is not None and abs(k - numero) < 0.005:
            return valor
    return None


def formatear(valor) -> str:
    """8.0 -> '8'; 8.5 -> '8,5'; 6.234 -> '6,23'."""
    d = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    return f"{d:f}".replace(".", ",")


@dataclass
class EleccionEscala:
    escala: object
    ambigua: bool
    alternativas: list = field(default_factory=list)
    motivo: str = ""


def _cobertura(escala, notas):
    if not notas:
        return 0.0
    return sum(1 for n in notas if convertir(escala, n) is not None) / len(notas)


def elegir_escala(escalas, notas, minima=None, maxima=None) -> EleccionEscala | None:
    """Escala más probable para las notas observadas (las de las materias superadas)."""
    escalas = list(escalas)
    if not escalas:
        return None
    minima_n, maxima_n = parse_numero(minima), parse_numero(maxima)

    def puntuacion(e):
        p = _cobertura(e, notas) * 10
        e_min, e_max = parse_numero(e.nota_minima_aprobatoria), parse_numero(e.nota_maxima)
        if minima_n is not None and e_min is not None and abs(minima_n - e_min) < 0.01:
            p += 3
        if maxima_n is not None and e_max is not None and abs(maxima_n - e_max) < 0.01:
            p += 3
        return p - min(e.num_calificaciones or len(e.calificaciones), 1000) * 0.001

    ordenadas = sorted(escalas, key=puntuacion, reverse=True)
    mejor = ordenadas[0]
    cobertura = _cobertura(mejor, notas)
    conflictos = []
    for otra in ordenadas[1:]:
        if _cobertura(otra, notas) < 0.99:
            continue
        for n in notas:
            a, b = convertir(mejor, n), convertir(otra, n)
            if a and b and abs(a["equivalente_5_10"] - b["equivalente_5_10"]) > 0.01:
                conflictos.append(otra)
                break
    motivo = []
    if notas and cobertura < 0.99:
        motivo.append(f"la escala solo reconoce {round(cobertura * len(notas))} de {len(notas)} notas")
    if conflictos:
        motivo.append("otras escalas del país darían notas distintas: " + ", ".join(e.id_escala for e in conflictos))
    return EleccionEscala(mejor, bool(motivo), conflictos, "; ".join(motivo))


@dataclass
class Resultado:
    nota: str            # '8,5', 'Suspenso' o 'No presentado'
    superada: bool
    detalle: str


# Marcas de ausencia: «No presentado». Cualquier otra nota no superada (4,9, F, U…) es «Suspenso».
AUSENCIA = {"", "-", "—", "ABS", "ABSENT", "NE", "NP", "N/A", "EXC", "NOT ASSESSED", "NO EVALUADO",
            "NON AVALIADO", "NO PRESENTADO", "NON PRESENTADO"}


def es_ausencia(nota) -> bool:
    return (nota or "").strip().upper().rstrip(".") in AUSENCIA


def resultado_linea(escala, nota, superada_declarada=None) -> Resultado:
    """Resultado de una sola materia de destino (superada_declarada: True/False/None = según nota)."""
    eq = None if superada_declarada is False else convertir(escala, nota)
    if eq is None and superada_declarada is True and not es_ausencia(nota):
        return Resultado("", True, f"superada según el ToR, pero «{nota}» no está en la escala: revisar")
    if eq is None:
        if es_ausencia(nota):
            return Resultado(NO_PRESENTADO, False, f"«{nota or 'sin nota'}»: no presentado")
        return Resultado(SUSPENSO, False, f"{nota}: no superada")
    return Resultado(formatear(eq["equivalente_5_10"]), True,
                     f"{nota} → {formatear(eq['equivalente_5_10'])} ({escala.id_escala})")


def combinar(componentes) -> Resultado:
    """componentes: [(escala, nota_original, ects, superada_declarada)] de las materias de destino
    que reconocen una misma materia UDC. Si alguna no está superada, la UDC no se reconoce."""
    resultados = [resultado_linea(e, n, s) for e, n, _, s in componentes]
    if len(componentes) == 1:
        return resultados[0]
    fallidas = [r for r in resultados if not r.superada]
    if fallidas:
        nota = SUSPENSO if any(r.nota == SUSPENSO for r in fallidas) else NO_PRESENTADO
        return Resultado(nota, False, "; ".join(r.detalle for r in resultados) + " → no se reconoce")
    sin_nota = [r for r in resultados if not r.nota]
    if sin_nota:
        return Resultado("", True, "; ".join(r.detalle for r in resultados))
    total = sum(Decimal(str(c[2])) for c in componentes)
    escalas = {c[0].pk for c in componentes}
    numeros = [parse_numero(c[1]) for c in componentes]
    if len(escalas) == 1 and None not in numeros:
        ponderada = sum(Decimal(str(n)) * Decimal(str(c[2])) for n, c in zip(numeros, componentes)) / total
        eq = convertir(componentes[0][0], float(ponderada))
        if eq:
            return Resultado(formatear(eq["equivalente_5_10"]), True,
                             f"media ponderada de las notas originales {formatear(ponderada)} → "
                             f"{formatear(eq['equivalente_5_10'])}")
    ponderada = sum(Decimal(r.nota.replace(",", ".")) * Decimal(str(c[2]))
                    for r, c in zip(resultados, componentes)) / total
    return Resultado(formatear(ponderada), True,
                     "media ponderada por ECTS de las notas convertidas: " + "; ".join(r.detalle for r in resultados))
