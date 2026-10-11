"""Panel de expedientes (RF-03): una fila por movilidad con su estado, LA, próximo plazo,
peticiones abiertas y bloqueos."""

from dataclasses import dataclass, field

from django.db.models import Prefetch

from movilidad.models import (
    Anotacion,
    EstadoAnotacion,
    EstadoPlazo,
    EstadoVersionLA,
    Movilidad,
    Plazo,
    TipoPlazo,
    VENTANAS,
)
from movilidad.plazos import nivel_alerta


@dataclass
class FilaPanel:
    movilidad: Movilidad
    la: str
    proximo: Plazo | None
    alerta: str | None
    peticiones: int
    bloqueos: list = field(default_factory=list)


def resumen_la(versiones) -> str:
    firmadas = [v for v in versiones if v.estado == EstadoVersionLA.FIRMADA]
    borradores = [v for v in versiones if v.estado == EstadoVersionLA.BORRADOR]
    texto = f"v{firmadas[-1].numero} {firmadas[-1].get_tipo_display()}" if firmadas else "Sin firmar"
    return texto + (" · borrador pendiente" if borradores else "")


def bloqueos(plazos, anotaciones, hoy) -> list:
    """Lo que impide avanzar o ya va tarde."""
    b = []
    for p in plazos:
        if p.tipo == TipoPlazo.ENVIAR_LA_ADMINISTRACION:
            b.append("LA modificado sin enviar a Administración")
        elif p.tipo == TipoPlazo.ENVIAR_ACTA and p.fecha_limite and p.fecha_limite < hoy:
            b.append("Acta fuera de plazo (5 semanas, RN-15)")
        elif p.tipo not in VENTANAS and p.fecha_limite and p.fecha_limite < hoy:
            b.append(f"Vencido: {p.descripcion or p.get_tipo_display()}")
    for a in anotaciones:
        if a.es_peticion and a.fecha_limite and a.fecha_limite < hoy:
            b.append(f"Petición vencida: {a.texto[:60]}")
    return b


def filas(movilidades, hoy) -> list:
    qs = movilidades.select_related("estudiante__titulacion", "institucion", "convocatoria").prefetch_related(
        "versiones_la",
        Prefetch("plazos", queryset=Plazo.objects.filter(estado=EstadoPlazo.PENDIENTE), to_attr="pendientes"),
        Prefetch("anotaciones", queryset=Anotacion.objects.filter(estado=EstadoAnotacion.ABIERTA), to_attr="abiertas"),
    )
    resultado = []
    for m in qs:
        vivos = [p for p in m.pendientes if not (p.tipo in VENTANAS and p.fecha_limite and p.fecha_limite < hoy)]
        con_fecha = sorted((p for p in vivos if p.fecha_limite), key=lambda p: p.fecha_limite)
        proximo = con_fecha[0] if con_fecha else None
        resultado.append(FilaPanel(
            movilidad=m, la=resumen_la(list(m.versiones_la.all())), proximo=proximo,
            alerta=nivel_alerta(proximo, hoy) if proximo else None,
            peticiones=sum(1 for a in m.abiertas if a.es_peticion), bloqueos=bloqueos(m.pendientes, m.abiertas, hoy),
        ))
    return resultado
