"""LA vigente (RF-09): resultado de aplicar en orden las versiones firmadas del acuerdo.

Nunca se guarda ni se edita: se recalcula cada vez desde las versiones.

- Se aplican las versiones FIRMADA por `numero`. Una versión que corrige a otra se aplica en el
  lugar de la corregida, y la corregida deja de contar. Las INVALIDADA no cuentan.
- Fila ALTA: añade un grupo de correspondencia.
- Fila BAJA: quita sus materias de los grupos del mismo semestre; los grupos que se quedan vacíos
  desaparecen y los que pierden solo una parte (p. ej. −B en A+B → X) se conservan con una
  incidencia para que el coordinador lo revise.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from movilidad.models import AccionFila, EstadoVersionLA, VersionLA


@dataclass
class Grupo:
    semestre: int
    materias_destino: list
    materias_udc: list
    sin_reconocimiento: bool
    versiones: list = field(default_factory=list)

    @property
    def ects_destino(self) -> Decimal:
        return sum((a.ects for a in self.materias_destino), Decimal(0))

    @property
    def ects_udc(self) -> Decimal:
        return sum((a.ects for a in self.materias_udc), Decimal(0))

    @property
    def vacio(self) -> bool:
        return not self.materias_destino and not self.materias_udc

    def __str__(self):
        destino = " + ".join(a.nombre for a in self.materias_destino) or "—"
        udc = " + ".join(a.nombre for a in self.materias_udc) or ("sin reconocimiento" if self.sin_reconocimiento else "—")
        return f"S{self.semestre}: {destino} → {udc}"


@dataclass
class Baja:
    semestre: int
    version: VersionLA
    asignatura: object


@dataclass
class LAVigente:
    grupos: list = field(default_factory=list)
    versiones: list = field(default_factory=list)
    bajas: list = field(default_factory=list)
    incidencias: list = field(default_factory=list)

    @property
    def semestres(self) -> list:
        return sorted({g.semestre for g in self.grupos})

    def grupos_reconocidos(self, semestre=None):
        return [
            g for g in self.grupos
            if not g.sin_reconocimiento and (semestre is None or g.semestre == semestre)
        ]

    def ects_udc(self, semestre=None) -> Decimal:
        return sum((g.ects_udc for g in self.grupos_reconocidos(semestre)), Decimal(0))

    def ects_destino(self, semestre=None) -> Decimal:
        """ECTS de destino que cuentan para el reconocimiento (sin las filas 'sin reconocimiento')."""
        return sum((g.ects_destino for g in self.grupos_reconocidos(semestre)), Decimal(0))


def versiones_aplicables(movilidad, incluir=None) -> list:
    """Versiones que forman el LA vigente, en orden de aplicación.

    `incluir` es una versión en borrador que se quiere ver aplicada (para validar antes de firmar).
    """
    versiones = [
        v for v in movilidad.versiones_la.prefetch_related("filas__materias_destino", "filas__materias_udc")
        if v.estado == EstadoVersionLA.FIRMADA or (incluir is not None and v.pk == incluir.pk)
    ]
    if incluir is not None and incluir.pk is None:
        versiones.append(incluir)
    por_pk = {v.pk: v for v in movilidad.versiones_la.all()}
    sustituidas = {v.corrige_id for v in versiones if v.corrige_id}

    def raiz(v):
        while v.corrige_id and v.corrige_id in por_pk:
            v = por_pk[v.corrige_id]
        return v.numero or 0

    vigentes = [v for v in versiones if v.pk is None or v.pk not in sustituidas]
    return sorted(vigentes, key=lambda v: (raiz(v), v.numero or 0))


def la_vigente(movilidad, incluir=None) -> LAVigente:
    la = LAVigente()
    for version in versiones_aplicables(movilidad, incluir):
        la.versiones.append(version)
        filas = version.filas.all() if version.pk else []
        for fila in filas:
            if fila.accion == AccionFila.ALTA:
                la.grupos.append(Grupo(
                    semestre=fila.semestre,
                    materias_destino=list(fila.materias_destino.all()),
                    materias_udc=list(fila.materias_udc.all()),
                    sin_reconocimiento=fila.sin_reconocimiento,
                    versiones=[version],
                ))
            else:
                _aplicar_baja(la, version, fila)
    return la


def _aplicar_baja(la, version, fila):
    tocados = []
    for lado, materias in (("materias_destino", fila.materias_destino.all()), ("materias_udc", fila.materias_udc.all())):
        for asignatura in materias:
            la.bajas.append(Baja(fila.semestre, version, asignatura))
            grupo = next(
                (g for g in la.grupos if g.semestre == fila.semestre and asignatura in getattr(g, lado)), None
            )
            if grupo is None:
                la.incidencias.append(
                    f"{version}: baja de «{asignatura.nombre}» (S{fila.semestre}), que no está en el LA vigente"
                )
                continue
            getattr(grupo, lado).remove(asignatura)
            grupo.versiones.append(version)
            if grupo not in tocados:
                tocados.append(grupo)
    for grupo in tocados:
        if grupo.vacio:
            la.grupos.remove(grupo)
        elif not grupo.sin_reconocimiento and (not grupo.materias_destino or not grupo.materias_udc):
            la.incidencias.append(f"{version}: la baja deja la correspondencia incompleta: {grupo}")
        else:
            la.incidencias.append(f"{version}: la baja quita solo una parte de la correspondencia: {grupo}")
