"""ToR → acta de reconocimiento (RF-15, RF-16, RF-17, RF-20).

- `proponer_emparejamiento(tor)`: empareja cada línea del ToR con una materia de destino del LA
  vigente (por código o por parecido de nombre). Es una propuesta: el coordinador la revisa en el
  ToR antes de aceptarlo.
- `generar_borrador(movilidad)`: construye las tablas C y D con los ToR aceptados y el LA vigente,
  con los avisos que hay que revisar antes de firmar. Reglas fijas de la skill acta-reconocimiento:
  la materia no superada va siempre en la tabla C (ECTS nominales, nota literal) y en la tabla D
  con 0 ECTS y «Suspenso» / «No presentado»; el «2 por 1» lleva media ponderada; lo que no está en
  el LA solo va en la tabla C.
- `crear_correccion(acta, motivo)`: versión nueva de un acta firmada (v2, v3…) sin tocar la firmada.
"""

import re
from difflib import SequenceMatcher

from django.db import transaction

from movilidad.importers.comun import normalizar_nombre
from movilidad.la_vigente import la_vigente
from movilidad.models import (
    Acta,
    EscalaNotas,
    EstadoActa,
    EstadoMovilidad,
    EstadoToR,
    FilaActaC,
    FilaActaD,
    SemestresToR,
    Superada,
)
from movilidad.notas import combinar, convertir, elegir_escala, es_ausencia, resultado_linea

UMBRAL_PARECIDO = 0.75
DECLARADA = {Superada.SI: True, Superada.NO: False, Superada.DESCONOCIDO: None}


def mismo_codigo(a: str, b: str) -> bool:
    """«CN-101» = «cn101»: solo cuentan letras y números."""
    limpio = lambda s: re.sub(r"[^a-z0-9]", "", normalizar_nombre(s))  # noqa: E731
    return bool(limpio(a)) and limpio(a) == limpio(b)


def parecido(a: str, b: str) -> float:
    return SequenceMatcher(None, normalizar_nombre(a), normalizar_nombre(b)).ratio()


def materias_destino_del_la(movilidad) -> list:
    vistas, materias = set(), []
    for grupo in la_vigente(movilidad).grupos:
        for a in grupo.materias_destino:
            if a.pk not in vistas:
                vistas.add(a.pk)
                materias.append(a)
    return materias


def proponer_emparejamiento(tor) -> list:
    """Rellena asignatura_destino en las líneas sin emparejar; devuelve las que quedan sin pareja."""
    candidatas = materias_destino_del_la(tor.movilidad)
    usadas = {l.asignatura_destino_id for l in tor.lineas.all() if l.asignatura_destino_id}
    sin_pareja = []
    for linea in tor.lineas.filter(asignatura_destino__isnull=True):
        libres = [a for a in candidatas if a.pk not in usadas]
        por_codigo = [a for a in libres if mismo_codigo(a.codigo, linea.codigo)]
        elegida = por_codigo[0] if len(por_codigo) == 1 else None
        if elegida is None:
            exactas = [a for a in libres if normalizar_nombre(a.nombre) == normalizar_nombre(linea.titulo)]
            elegida = exactas[0] if len(exactas) == 1 else None
        if elegida is None and libres:
            puntuadas = sorted(((parecido(linea.titulo, a.nombre), a) for a in libres), key=lambda x: -x[0])
            mejor, segunda = puntuadas[0], (puntuadas[1][0] if len(puntuadas) > 1 else 0)
            if mejor[0] >= UMBRAL_PARECIDO and mejor[0] - segunda >= 0.05:
                elegida = mejor[1]
        if elegida is None:
            sin_pareja.append(linea)
            continue
        linea.asignatura_destino = elegida
        linea.save(update_fields=["asignatura_destino"])
        usadas.add(elegida.pk)
    return sin_pareja


def nombre_coincide(en_documento: str, nombre_completo: str) -> bool:
    """El ToR a veces trae solo el primer apellido: basta con que coincida alguna palabra significativa."""
    a = {p for p in normalizar_nombre(en_documento).split() if len(p) > 2}
    b = {p for p in normalizar_nombre(nombre_completo).split() if len(p) > 2}
    return not a or bool(a & b)


def escala_del_tor(tor, avisos) -> EscalaNotas | None:
    if tor.escala_id:
        return tor.escala
    pais = tor.movilidad.institucion.pais
    escalas = EscalaNotas.objects.filter(pais_iso=pais)
    if not escalas.exists():
        avisos.append(f"{tor}: no hay escalas de notas para el país {pais}; elige la escala en el ToR "
                      f"(¿se importó el JSON de equivalencias?)")
        return None
    # Las notas que no reconoce ninguna escala del país son suspensos (las tablas solo listan
    # aprobados): no cuentan para elegir la escala.
    escalas = list(escalas)
    notas = [l.nota for l in tor.lineas.all() if l.superada != Superada.NO and not es_ausencia(l.nota)
             and any(convertir(e, l.nota) for e in escalas)]
    eleccion = elegir_escala(escalas, notas, tor.nota_minima, tor.nota_maxima)
    if eleccion.ambigua:
        avisos.append(f"{tor}: escala ambigua, se usa «{eleccion.escala.id_escala}»: {eleccion.motivo}. "
                      f"Compruébala con la descripción del ToR y fíjala si hace falta")
    return eleccion.escala


def _cubre(tor, semestre) -> bool:
    return tor.semestres in (SemestresToR.AMBOS, f"S{semestre}")


@transaction.atomic
def generar_borrador(movilidad, usuario=None) -> Acta:
    """Crea (o regenera) el acta en borrador de la movilidad."""
    tors = list(movilidad.tors.filter(estado=EstadoToR.ACEPTADO).prefetch_related("lineas__asignatura_destino"))
    if not tors:
        raise ValueError("No hay ningún ToR aceptado: revisa y acepta el ToR antes de generar el acta.")
    acta = movilidad.actas.filter(estado=EstadoActa.BORRADOR).order_by("-numero").first()
    if acta is None:
        acta = Acta.objects.create(movilidad=movilidad)
    acta.filas_c.all().delete()
    acta.filas_d.all().delete()
    acta.tors.set(tors)

    avisos = []
    la = la_vigente(movilidad)
    avisos += [f"LA vigente: {i}" for i in la.incidencias]
    if not la.versiones:
        avisos.append("No hay ninguna versión firmada del LA: la tabla D queda vacía.")
    en_la = {a.pk for g in la.grupos for a in g.materias_destino}

    lineas, escala_de = [], {}
    for tor in tors:
        if tor.parcial:
            avisos.append(f"{tor}: marcado como parcial (faltan notas o semestres).")
        if tor.nombre_en_documento and not nombre_coincide(tor.nombre_en_documento, movilidad.estudiante.nombre_completo):
            avisos.append(f"{tor}: el nombre del documento («{tor.nombre_en_documento}») no coincide con "
                          f"el del alumno/a ({movilidad.estudiante.nombre_completo}).")
        escala = escala_del_tor(tor, avisos)
        for l in tor.lineas.all():
            lineas.append(l)
            escala_de[l.pk] = l.escala or escala

    # Tabla C: todas las materias del ToR, superadas o no, con sus ECTS nominales.
    por_destino = {}
    for i, l in enumerate(lineas):
        r = resultado_linea(escala_de[l.pk], l.nota, DECLARADA[l.superada])
        ects = l.ects or (l.asignatura_destino.ects if l.asignatura_destino else 0)
        FilaActaC.objects.create(acta=acta, orden=i, codigo=l.codigo, titulo=l.titulo, superada=r.superada,
                                 ects=ects, nota_original=l.nota)
        if l.asignatura_destino_id and l.asignatura_destino_id in en_la:
            por_destino[l.asignatura_destino_id] = l
        else:
            avisos.append(f"«{l.titulo}» no está en el LA vigente: solo va en la tabla C (no se reconoce).")

    # Tabla D: cada correspondencia del LA vigente con materias UDC.
    orden = 0
    for grupo in la.grupos:
        if grupo.sin_reconocimiento or not grupo.materias_udc:
            continue
        faltan = [d for d in grupo.materias_destino if d.pk not in por_destino]
        if faltan:
            nombres = ", ".join(f"«{d.nombre}»" for d in faltan)
            if any(_cubre(t, grupo.semestre) for t in tors):
                avisos.append(f"Sin nota en el ToR para {nombres} (S{grupo.semestre}): "
                              f"{', '.join(u.nombre for u in grupo.materias_udc)} queda fuera del acta.")
            else:
                avisos.append(f"Pendiente del ToR del {grupo.semestre}.º semestre: {nombres}.")
            continue
        componentes = []
        for d in grupo.materias_destino:
            l = por_destino[d.pk]
            componentes.append((escala_de[l.pk], l.nota, l.ects or d.ects, DECLARADA[l.superada]))
        r = combinar(componentes)
        if not r.nota:
            avisos.append(f"{', '.join(u.nombre for u in grupo.materias_udc)}: {r.detalle}.")
        for u in grupo.materias_udc:
            FilaActaD.objects.create(
                acta=acta, orden=orden, asignatura_udc=u, codigo=u.codigo_oficial or u.codigo, titulo=u.nombre,
                ects=u.ects if r.superada else 0, nota=r.nota, detalle=r.detalle,
            )
            orden += 1

    acta.avisos = avisos
    acta.save()
    if movilidad.estado in (EstadoMovilidad.TOR_RECIBIDO, EstadoMovilidad.ENVIADA_ADMINISTRACION,
                            EstadoMovilidad.CERRADA):
        movilidad.cambiar_estado(EstadoMovilidad.ACTA_BORRADOR, usuario=usuario, nota=f"{acta} generada")
    return acta


@transaction.atomic
def crear_correccion(acta, motivo, usuario=None) -> Acta:
    """RF-20: nueva versión (borrador) de un acta firmada o enviada, con el motivo."""
    if acta.editable:
        raise ValueError("Un borrador se corrige editándolo o regenerándolo.")
    if acta.movilidad.actas.filter(estado=EstadoActa.BORRADOR).exists():
        raise ValueError("Ya hay un borrador abierto para esta movilidad.")
    nueva = Acta(movilidad=acta.movilidad, corrige=acta, motivo_correccion=motivo)
    nueva.full_clean(exclude=["numero"])
    nueva.save()
    return generar_borrador(acta.movilidad, usuario=usuario)


def acta_vigente(movilidad) -> Acta | None:
    """La última firmada o enviada que no ha sido corregida por otra firmada."""
    firmadas = list(movilidad.actas.exclude(estado=EstadoActa.BORRADOR).order_by("numero"))
    corregidas = {a.corrige_id for a in firmadas if a.corrige_id}
    vigentes = [a for a in firmadas if a.pk not in corregidas]
    return vigentes[-1] if vigentes else None
