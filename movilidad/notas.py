"""Conversión de notas a la escala española (RN-12, RN-13, RF-16).

Port de lib_grades.py (skill acta-reconocimiento) sobre el modelo EscalaNotas, con sus reglas:

- La escala de un país se elige por cobertura de las notas observadas en el ToR; a igualdad, la
  más sencilla (menos valores). Si el ToR declara mínimo/máximo, se premia la escala que coincide.
  Si otra escala compatible daría un resultado distinto, se avisa (escala ambigua).
- Una nota que la escala no contiene es «Suspenso» (las tablas oficiales solo listan aprobados),
  salvo las marcas de ausencia (ABS, NE, «-», EXC, vacía), que son «No presentado».
- Suspenso con nota numérica: se le da su equivalente (Regl. 3/2025 art. 25.1: cuentan todas las
  calificaciones). Como la tabla oficial no tiene suspensos, se escala de forma proporcional por
  debajo del aprobado: 5 × nota / nota mínima de aprobado (p. ej. 12 sobre aprobado 18 → 3,33).
  Las letras de suspenso ECTS (F, FX) valen 4 (criterio del coordinador). Si no se puede (otras
  letras, escalas en las que menos es mejor), queda «Suspenso» para completar.
- Si el ToR da una materia por no superada aunque la escala la aprobaría, está suspensa:
  min(4, nota convertida).
- «2 por 1»: media ponderada por ECTS de las notas ya convertidas (Regl. 3/2025 art. 25.2). Con
  alguna parte suspensa, la UDC queda suspensa con min(4, esa media).
- La nota final se redondea hacia arriba al primer decimal (6,54 → 6,6); un suspenso no pasa de
  4,9. (Criterios del coordinador, 10/2026.)
"""

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

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


def redondear(valor, suspenso=False) -> Decimal:
    """Hacia arriba al primer decimal (6,54 → 6,6; 8 → 8); un suspenso se queda en 4,9 como mucho."""
    d = Decimal(str(valor)).quantize(Decimal("0.000001"))  # sin el ruido de los float (6,2000001)
    d = d.quantize(Decimal("0.1"), rounding=ROUND_CEILING)
    return min(d, Decimal("4.9")) if suspenso else d


@dataclass
class Resultado:
    nota: str            # '8,5', 'Suspenso (4)', 'Suspenso' o 'No presentado'
    superada: bool
    detalle: str
    valor: Decimal | None = None  # equivalente sin redondear, para las medias del «2 por 1»


def _aprobado(valor, detalle) -> Resultado:
    final = redondear(valor)
    return Resultado(formatear(final), True, detalle, Decimal(str(valor)))


def _suspenso(valor, detalle) -> Resultado:
    final = redondear(valor, suspenso=True)
    return Resultado(f"{SUSPENSO} ({formatear(final)})", False, detalle, Decimal(str(valor)))


# Marcas de ausencia: «No presentado». Cualquier otra nota no superada (4,9, F, U…) es «Suspenso».
AUSENCIA = {"", "-", "—", "ABS", "ABSENT", "NE", "NP", "N/A", "EXC", "NOT ASSESSED", "NO EVALUADO",
            "NON AVALIADO", "NO PRESENTADO", "NON PRESENTADO"}


def es_ausencia(nota) -> bool:
    return (nota or "").strip().upper().rstrip(".") in AUSENCIA


# Suspensos ECTS por letra: equivalente fijo (criterio del coordinador, 10/2026).
SUSPENSO_LETRA = {"F": 4, "FX": 4, "FX/F": 4, "F/FX": 4}


def equivalente_suspenso(escala, nota):
    """(nota española < 5, explicación) de un suspenso, o None si no hay forma de calcularla."""
    letra = str(nota or "").strip().upper().replace(" ", "")
    if letra in SUSPENSO_LETRA:
        return SUSPENSO_LETRA[letra], f"{letra} = {SUSPENSO_LETRA[letra]}"
    numero = parse_numero(nota)
    if escala is None or numero is None:
        return None
    pares = [(parse_numero(k), v["equivalente_5_10"]) for k, v in escala.calificaciones.items()
             if parse_numero(k) is not None]
    if len(pares) < 2:
        return None
    aprobado = min(pares, key=lambda p: p[1])[0]
    mejor = max(pares, key=lambda p: p[1])[0]
    if mejor <= aprobado or aprobado <= 0 or not 0 <= numero < aprobado:
        return None  # escala invertida (1 = mejor) o la nota no está por debajo del aprobado
    valor = min(5 * numero / aprobado, 4.99)
    return valor, f"5 × {nota} / {formatear(aprobado)}, mínimo de aprobado"


def resultado_linea(escala, nota, superada_declarada=None) -> Resultado:
    """Resultado de una sola materia de destino (superada_declarada: True/False/None = según nota)."""
    eq = convertir(escala, nota)
    if eq is not None and superada_declarada is False:
        valor = min(eq["equivalente_5_10"], 4)
        return _suspenso(valor, f"{nota}: no superada según el ToR (la escala daría "
                                f"{formatear(eq['equivalente_5_10'])}) → mín(4, …) = {formatear(valor)}")
    if eq is None and superada_declarada is True and not es_ausencia(nota):
        return Resultado("", True, f"superada según el ToR, pero «{nota}» no está en la escala: revisar")
    if eq is None:
        if es_ausencia(nota):
            return Resultado(NO_PRESENTADO, False, f"«{nota or 'sin nota'}»: no presentado")
        equivalente = equivalente_suspenso(escala, nota)
        if equivalente:
            valor, como = equivalente
            return _suspenso(valor, f"{nota}: no superada → {formatear(valor)} ({como})")
        return Resultado(SUSPENSO, False, f"{nota}: no superada, sin equivalente numérico: indícalo")
    valor = eq["equivalente_5_10"]
    return _aprobado(valor, f"{nota} → {formatear(valor)} ({escala.id_escala})")


def combinar(componentes) -> Resultado:
    """componentes: [(escala, nota_original, ects, superada_declarada)] de las materias de destino
    que reconocen una misma materia UDC: media ponderada por ECTS de las notas convertidas."""
    resultados = [resultado_linea(e, n, s) for e, n, _, s in componentes]
    if len(componentes) == 1:
        return resultados[0]
    detalle = "; ".join(r.detalle for r in resultados)
    fallidas = [r for r in resultados if not r.superada]
    valores = [r.valor for r in resultados]
    if None not in valores and (not fallidas or any(r.nota.startswith(SUSPENSO) for r in fallidas)):
        total = sum(Decimal(str(c[2])) for c in componentes)
        media = sum(v * Decimal(str(c[2])) for v, c in zip(valores, componentes)) / total
        if not fallidas:
            return _aprobado(media, f"media ponderada por ECTS de las notas convertidas: {detalle} → {formatear(media)}")
        valor = min(media, Decimal(4))
        return _suspenso(valor, f"{detalle} → media ponderada {formatear(media)}; suspensa: mín(4, media) = "
                                f"{formatear(valor)}")
    if fallidas:
        nota = SUSPENSO if any(r.nota.startswith(SUSPENSO) for r in fallidas) else NO_PRESENTADO
        return Resultado(nota, False, f"{detalle} → no se reconoce; falta la nota de alguna parte: indícala")
    return Resultado("", True, detalle)
