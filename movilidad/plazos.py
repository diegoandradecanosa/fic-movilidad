"""Plazos y tareas automáticos de cada movilidad (RF-12, RF-13) y resumen diario de alertas.

`recalcular_plazos(movilidad)` se llama al guardar una movilidad o una versión del LA (signals.py).
Solo toca los plazos de origen REGLA que siguen pendientes: los manuales y los ya cerrados
(cumplidos o descartados por el coordinador) no se modifican.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db.models import Q

from movilidad.models import (
    EstadoPlazo,
    EstadoVersionLA,
    OrigenPlazo,
    Plazo,
    TipoPlazo,
    TipoVersionLA,
    VENTANAS,
)
from movilidad.validaciones_la import sumar_meses

# Tipos que mantiene recalcular_plazos. ENVIAR_LA_ADMINISTRACION lo crea envios.py si falla el envío.
TIPOS_RECALCULADOS = set(TipoPlazo) - {TipoPlazo.ENVIAR_LA_ADMINISTRACION, TipoPlazo.OTRO}


@dataclass
class Deseado:
    fecha: date | None
    descripcion: str
    regla: str = ""
    cerrar: str | None = None  # EstadoPlazo con el que se cierra, si ya está resuelto


def fin_ventana_s1(m):
    if m.fecha_llegada and m.semestre_inicio != 2:
        return sumar_meses(m.fecha_llegada)
    return None


def fin_ventana_s2(m):
    if m.semestre_inicio == 2:
        return sumar_meses(m.fecha_llegada) if m.fecha_llegada else None
    if (m.duracion_semestres or 1) >= 2 and m.fecha_inicio_s2:
        return sumar_meses(m.fecha_inicio_s2)
    return None


def plazos_deseados(m) -> dict:
    """{(tipo, version_la_id): Deseado} según los datos actuales de la movilidad."""
    versiones = list(m.versiones_la.all())
    firmadas = {v.tipo for v in versiones if v.estado == EstadoVersionLA.FIRMADA}
    cumplido = lambda tipo: EstadoPlazo.CUMPLIDO if tipo in firmadas else None  # noqa: E731
    d = {}
    semestral_s1 = (m.duracion_semestres or 1) == 1 and m.semestre_inicio != 2

    if m.fecha_llegada_prevista:
        d[(TipoPlazo.FIRMAR_BEFORE, None)] = Deseado(
            m.fecha_llegada_prevista, "Before firmado por las tres partes antes de la llegada", "RN-06",
            cumplido(TipoVersionLA.BEFORE))
    if fin := fin_ventana_s1(m):
        d[(TipoPlazo.VENTANA_MOD_S1, None)] = Deseado(
            fin, "Fin del plazo para modificar el LA del 1.er semestre (1 mes desde la llegada)", "RN-05",
            cumplido(TipoVersionLA.DURING_S1))
    if fin := fin_ventana_s2(m):
        d[(TipoPlazo.VENTANA_MOD_S2, None)] = Deseado(
            fin, "Fin del plazo para modificar el LA del 2.º semestre (1 mes)", "RN-05",
            cumplido(TipoVersionLA.DURING_S2))
    if m.fecha_llegada and semestral_s1:
        d[(TipoPlazo.VENTANA_AMPLIACION, None)] = Deseado(
            sumar_meses(m.fecha_llegada), "Fin del plazo para pedir la ampliación (1 mes desde la llegada)",
            "RN-16", cumplido(TipoVersionLA.AMPLIACION))
        preferencia = date(m.fecha_llegada.year, 11, 10)
        if m.fecha_llegada <= preferencia:
            d[(TipoPlazo.PREFERENCIA_AMPLIACION, None)] = Deseado(
                preferencia, "Las ampliaciones que llegan a la ORI antes del 10/11 tienen preferencia de financiación",
                "RN-16", cumplido(TipoVersionLA.AMPLIACION))
    if m.fecha_llegada and (m.duracion_semestres or 1) >= 2:
        d[(TipoPlazo.VENTANA_REDUCCION, None)] = Deseado(
            sumar_meses(m.fecha_llegada), "Fin del plazo para pedir la reducción a un semestre (1 mes desde la llegada)")

    fin_revision = {
        TipoVersionLA.BEFORE: m.fecha_llegada_prevista,
        TipoVersionLA.DURING_S1: fin_ventana_s1(m),
        TipoVersionLA.DURING_S2: fin_ventana_s2(m),
        TipoVersionLA.AMPLIACION: sumar_meses(m.fecha_llegada) if m.fecha_llegada else None,
    }
    for v in versiones:
        cerrar = {
            EstadoVersionLA.BORRADOR: None,
            EstadoVersionLA.FIRMADA: EstadoPlazo.CUMPLIDO,
            EstadoVersionLA.INVALIDADA: EstadoPlazo.DESCARTADO,
        }[EstadoVersionLA(v.estado)]
        d[(TipoPlazo.REVISAR_LA, v.pk)] = Deseado(fin_revision[TipoVersionLA(v.tipo)], f"Revisar y firmar {v}", "", cerrar)
    return d


def recalcular_plazos(m) -> None:
    deseados = plazos_deseados(m)
    existentes = {
        (p.tipo, p.version_la_id): p
        for p in m.plazos.filter(origen=OrigenPlazo.REGLA, tipo__in=TIPOS_RECALCULADOS)
    }
    for clave, p in existentes.items():
        if p.estado != EstadoPlazo.PENDIENTE:
            continue
        deseado = deseados.get(clave)
        if deseado is None:  # la fecha base ha desaparecido
            p.delete()
            continue
        if deseado.cerrar:
            p.fecha_limite, p.descripcion = deseado.fecha, deseado.descripcion
            p.cerrar(deseado.cerrar, "Cerrado automáticamente")
        elif (p.fecha_limite, p.descripcion) != (deseado.fecha, deseado.descripcion):
            p.fecha_limite, p.descripcion = deseado.fecha, deseado.descripcion
            p.save(update_fields=["fecha_limite", "descripcion"])
    for (tipo, version_id), deseado in deseados.items():
        if (tipo, version_id) in existentes or deseado.cerrar:
            continue
        Plazo.objects.create(
            movilidad=m, tipo=tipo, version_la_id=version_id, fecha_limite=deseado.fecha,
            descripcion=deseado.descripcion, regla=deseado.regla, origen=OrigenPlazo.REGLA,
        )


# --- alertas ---------------------------------------------------------------------------------------

class Alerta:
    VENCIDA = "VENCIDA"
    CADUCADA = "CADUCADA"
    URGENTE = "URGENTE"  # ≤ 2 días
    PROXIMA = "PROXIMA"  # ≤ 7 días


DIAS_AVISO = (7, 2)


def dias_restantes(p, hoy):
    return (p.fecha_limite - hoy).days if p.fecha_limite else None


def nivel_alerta(p, hoy) -> str | None:
    dias = dias_restantes(p, hoy)
    if p.estado != EstadoPlazo.PENDIENTE or dias is None:
        return None
    if dias < 0:
        return Alerta.CADUCADA if p.es_ventana else Alerta.VENCIDA
    if dias <= 2:
        return Alerta.URGENTE
    if dias <= 7:
        return Alerta.PROXIMA
    return None


def pendientes(hoy, usuario=None):
    """Bandeja «Mis pendientes»: lo del usuario y lo sin asignar, sin ventanas ya caducadas."""
    qs = Plazo.objects.filter(estado=EstadoPlazo.PENDIENTE).select_related(
        "movilidad__estudiante", "movilidad__institucion", "responsable"
    ).exclude(tipo__in=VENTANAS, fecha_limite__lt=hoy)
    if usuario is not None:
        qs = qs.filter(Q(responsable=usuario) | Q(responsable__isnull=True))
    return qs


def resumen_diario(hoy) -> dict:
    """{correo_destinatario: [plazos]}: lo que vence en 7 o 2 días y las tareas vencidas."""
    por_destinatario = defaultdict(list)
    for p in pendientes(hoy).exclude(fecha_limite__isnull=True):
        dias = dias_restantes(p, hoy)
        if dias in DIAS_AVISO or (dias < 0 and not p.es_ventana):
            destinatario = (p.responsable.email if p.responsable else "") or settings.CORREO_COORDINADOR
            por_destinatario[destinatario].append(p)
    return dict(por_destinatario)


def texto_resumen(plazos, hoy) -> str:
    lineas = ["Plazos y tareas de movilidad que requieren atención:", ""]
    for p in plazos:
        dias = dias_restantes(p, hoy)
        cuando = f"venció hace {-dias} día(s)" if dias < 0 else f"vence en {dias} días"
        lineas.append(f"- {p.fecha_limite:%d/%m/%Y} ({cuando}) · {p.movilidad.estudiante} · "
                      f"{p.movilidad.institucion.nombre}: {p.descripcion or p.get_tipo_display()}")
    lineas += ["", "Bandeja completa: «Mis pendientes» en la aplicación de movilidad."]
    return "\n".join(lineas)
