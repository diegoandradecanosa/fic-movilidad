"""Validaciones del acuerdo académico (RN-01 a RN-07): avisos para el coordinador, nunca bloqueos.

`validar(movilidad)` comprueba el LA vigente; `validar(movilidad, version=borrador)` comprueba
cómo quedaría el LA si se firmase ese borrador.
"""

import calendar
from dataclasses import dataclass
from decimal import Decimal

from movilidad.la_vigente import la_vigente
from movilidad.models import AsignaturaUDC, Cuatrimestre, EstadoVersionLA, TipoVersionLA

ECTS_POR_HORA = Decimal("8.5")


@dataclass(frozen=True)
class Aviso:
    regla: str
    mensaje: str

    def __str__(self):
        return f"{self.regla}: {self.mensaje}"


def sumar_meses(fecha, meses=1):
    mes = fecha.month - 1 + meses
    anio, mes = fecha.year + mes // 12, mes % 12 + 1
    return fecha.replace(year=anio, month=mes, day=min(fecha.day, calendar.monthrange(anio, mes)[1]))


def _f(n) -> str:
    """Decimal('18.00') -> '18'; Decimal('4.50') -> '4,5'."""
    return f"{Decimal(n).normalize():f}".replace(".", ",")


def numero_semestres(movilidad, la) -> int:
    return max(movilidad.duracion_semestres or 0, len(la.semestres), 1)


def validar(movilidad, version=None) -> list:
    la = la_vigente(movilidad, incluir=version)
    avisos = [Aviso("LA", i) for i in la.incidencias]
    for regla in (rn01, rn02, rn03, rn04, rn05, rn06, rn07, firmas):
        avisos.extend(regla(movilidad, la))
    return avisos


def rn01(movilidad, la):
    """Mínimo de ECTS UDC reconocidos: 18 por semestre (36 anual)."""
    n = numero_semestres(movilidad, la)
    minimo, udc = 18 * n, la.ects_udc()
    if udc < minimo:
        return [Aviso("RN-01", f"{_f(udc)} ECTS UDC reconocidos, mínimo {minimo} "
                                f"(salvo que solo le quede el TFG)")]
    return []


def rn02(movilidad, la):
    """Máximo: el de destino (30 por semestre); si destino lo supera, hasta 39 por semestre."""
    n = numero_semestres(movilidad, la)
    udc, destino = la.ects_udc(), la.ects_destino()
    if udc > 39 * n:
        return [Aviso("RN-02", f"{_f(udc)} ECTS UDC reconocidos superan el máximo de {39 * n} "
                                f"({45 * n} solo en estudios simultáneos)")]
    if udc > 30 * n and destino <= 30 * n:
        return [Aviso("RN-02", f"{_f(udc)} ECTS UDC reconocidos superan {30 * n} y en destino "
                                f"solo cursa {_f(destino)}")]
    return []


def rn03(movilidad, la):
    """Diferencia de créditos UDC–destino ≤ 2 por semestre (≤ 4 anual)."""
    n = numero_semestres(movilidad, la)
    udc, destino = la.ects_udc(), la.ects_destino()
    if abs(udc - destino) > 2 * n:
        return [Aviso("RN-03", f"diferencia de {_f(abs(udc - destino))} ECTS entre UDC ({_f(udc)}) "
                                f"y destino ({_f(destino)}); máximo {2 * n}")]
    return []


def rn04(movilidad, la):
    """Si el destino da horas en vez de ECTS: ECTS = horas / 8,5."""
    avisos, vistas = [], set()
    for grupo in la.grupos:
        for a in grupo.materias_destino:
            if a.pk in vistas or a.horas_totales is None:
                continue
            vistas.add(a.pk)
            esperado = (a.horas_totales / ECTS_POR_HORA).quantize(Decimal("0.1"))
            if abs(a.ects - esperado) > Decimal("0.1"):
                avisos.append(Aviso("RN-04", f"«{a.nombre}»: {_f(a.horas_totales)} h equivalen a "
                                             f"{_f(esperado)} ECTS, no {_f(a.ects)}"))
    return avisos


def rn05(movilidad, la):
    """Una modificación por semestre, pedida en el primer mes desde la llegada / inicio del 2.º semestre."""
    avisos = []
    for tipo, semestre in ((TipoVersionLA.DURING_S1, 1), (TipoVersionLA.DURING_S2, 2)):
        durings = [v for v in la.versiones if v.tipo == tipo]
        if len(durings) > 1:
            avisos.append(Aviso("RN-05", f"{len(durings)} modificaciones del {semestre}.º semestre; "
                                          f"solo se admite una (salvo autorización de la ORI)"))
        if semestre == 1 or movilidad.semestre_inicio == 2:
            base, nombre_base = movilidad.fecha_llegada, "la llegada"
        else:
            base, nombre_base = movilidad.fecha_inicio_s2, "el inicio del 2.º semestre"
        for v in durings:
            pedida = v.fecha_solicitud or v.fecha_firma_estudiante
            if pedida is None or base is None:
                avisos.append(Aviso("RN-05", f"{v}: no se puede comprobar el plazo (falta la fecha de "
                                              f"{'solicitud' if pedida is None else nombre_base})"))
            elif pedida > sumar_meses(base):
                avisos.append(Aviso("RN-05", f"{v}: pedida el {pedida:%d/%m/%Y}, más de un mes después "
                                              f"de {nombre_base} ({base:%d/%m/%Y})"))
    return avisos


def rn06(movilidad, la):
    """La firma de destino del Before es anterior a la fecha del certificado de llegada."""
    llegada = movilidad.fecha_llegada
    if llegada is None:
        return []
    avisos = []
    for v in la.versiones:
        if v.tipo != TipoVersionLA.BEFORE:
            continue
        if v.fecha_firma_destino is None:
            avisos.append(Aviso("RN-06", f"{v}: falta la fecha de firma de destino (llegada el {llegada:%d/%m/%Y})"))
        elif v.fecha_firma_destino >= llegada:
            avisos.append(Aviso("RN-06", f"{v}: destino firmó el {v.fecha_firma_destino:%d/%m/%Y}, no antes "
                                          f"de la llegada ({llegada:%d/%m/%Y})"))
    return avisos


def rn07(movilidad, la):
    """Una materia evaluada en la UDC en el 1.er semestre no entra en el LA del 2.º."""
    bajas_s1 = {b.asignatura.pk for b in la.bajas if b.semestre == 1 and isinstance(b.asignatura, AsignaturaUDC)}
    avisos = []
    for grupo in la.grupos:
        if grupo.semestre != 2:
            continue
        for a in grupo.materias_udc:
            if a.cuatrimestre != Cuatrimestre.C1:
                continue
            if movilidad.semestre_inicio == 2:
                motivo = "la movilidad empieza en el 2.º semestre"
            elif a.pk in bajas_s1:
                motivo = "se dio de baja del LA del 1.er semestre"
            else:
                continue
            avisos.append(Aviso("RN-07", f"«{a.nombre}» es de 1.er cuatrimestre y {motivo}: pudo evaluarse "
                                          f"en la UDC y no puede entrar en el LA del 2.º semestre"))
    return avisos


def firmas(movilidad, la):
    """Las versiones firmadas deberían tener las fechas de firma de las tres partes."""
    avisos = []
    for v in la.versiones:
        if v.estado != EstadoVersionLA.FIRMADA:
            continue
        faltan = [parte for parte, fecha in (("estudiante", v.fecha_firma_estudiante), ("UDC", v.fecha_firma_udc),
                                              ("destino", v.fecha_firma_destino)) if fecha is None]
        if faltan:
            avisos.append(Aviso("FIRMAS", f"{v}: falta la fecha de firma de {', '.join(faltan)}"))
    return avisos
