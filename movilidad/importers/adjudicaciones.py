"""Importador del Excel de adjudicaciones que envía la ORI a la FIC.

Cada fila es una solicitud (estudiante × destino × orden de preferencia). El importador:
- localiza las columnas por nombre (el orden y las columnas extra cambian entre cursos);
- crea o actualiza Estudiante, Titulación, Institución, Convocatoria y Solicitud;
- crea una Movilidad en estado ADJUDICADA por cada solicitud adjudicada;
- si al reimportar (provisional → definitiva) una adjudicación cambia, actualiza la movilidad
  mientras siga en ADJUDICADA y avisa si ya había avanzado;
- nunca borra nada: lo que desaparece se marca y se avisa.

Es idempotente: importar dos veces el mismo fichero no cambia nada.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from django.db import transaction

from movilidad.importers.comun import buscar_o_crear_institucion
from movilidad.models import (
    Convocatoria,
    EstadoMovilidad,
    EstadoSolicitud,
    Estudiante,
    Listado,
    Movilidad,
    Programa,
    Solicitud,
    Titulacion,
    enmascarar_dni,
    hash_dni,
)


def _norm(texto) -> str:
    """Minúsculas, sin tildes y con espacios simples (para comparar cabeceras y valores)."""
    if texto is None:
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# Nombre lógico -> posibles cabeceras (normalizadas).
COLUMNAS = {
    # El formato cambia entre cursos: 2025/26 («Orde de preferencia», «Programa», «Ano acad.»…) y
    # 2026/27 («orde prioridade», sin «Programa» ni «Ano acad.», «Oferta de praza»…).
    "nombre": ["nome"],
    "dni": ["dni"],
    "titulacion": ["titulacion", "titulacion udc"],
    "programa": ["programa"],
    "orden": ["orde de preferencia", "orde prioridade", "orde de prioridade"],
    "curso": ["ano acad."],
    "inicio": ["inicio (semestre)", "inicio periodo"],
    "duracion": ["duracion (semestres previstos)", "duracion (semestres)", "no de sem."],
    "pais": ["pais", "pais destino"],
    "institucion": ["institucion", "institucion destino"],
    "observaciones": ["observacions ori"],
    "idioma": ["certificacion de idiomas"],
    "estado": ["estado de solicitude - interno", "estado de solicitude-interno"],
    "nota": ["nota media"],
    "plaza": ["plaza ofertada", "oferta de praza"],
    "plazo": ["prazo da convocatoria", "convocatoria"],
    "meritos": ["outros meritos (max. 5%)", "outros meritos"],
    "motivo": ["motivos rexeitamento"],
    "condiciones": ["condicions para adxudicacion"],
}

OBLIGATORIAS = ["nombre", "dni", "orden", "institucion", "pais", "estado", "plazo"]
# Si faltan, se deducen: el programa de la plaza ofertada («Erasmus+ SMS - …») y el curso del
# inicio del periodo («1º semester 2026/27»).
ALTERNATIVAS = {"programa": "plaza", "curso": "inicio"}
FILAS_VACIAS_FIN = 200  # algunas hojas arrastran un millón de filas vacías


class FormatoNoReconocido(Exception):
    pass


@dataclass
class Informe:
    filas: int = 0
    solicitudes_nuevas: int = 0
    solicitudes_actualizadas: int = 0
    movilidades_nuevas: int = 0
    movilidades_actualizadas: int = 0
    avisos: list[str] = field(default_factory=list)

    def resumen(self) -> str:
        lineas = [
            f"Filas leídas: {self.filas}",
            f"Solicitudes nuevas: {self.solicitudes_nuevas} · actualizadas: {self.solicitudes_actualizadas}",
            f"Movilidades nuevas: {self.movilidades_nuevas} · actualizadas: {self.movilidades_actualizadas}",
        ]
        lineas += [f"AVISO: {a}" for a in self.avisos]
        return "\n".join(lineas)


def parse_estado(texto) -> tuple[str, int | None]:
    t = _norm(texto)
    if t.startswith("adxudicatari") or t.startswith("adjudicatari"):
        return EstadoSolicitud.ADJUDICADA, None
    if t.startswith("suplente"):
        m = re.search(r"(\d+)", t)
        return EstadoSolicitud.SUPLENTE, int(m.group(1)) if m else None
    if t.startswith("rexeitad") or t.startswith("rechazad"):
        return EstadoSolicitud.RECHAZADA, None
    if "agarda" in t or "espera" in t:
        return EstadoSolicitud.LISTA_ESPERA, None
    if "non se oferta" in t or "no se oferta" in t:
        return EstadoSolicitud.NO_OFERTADA, None
    return EstadoSolicitud.DESCONOCIDO, None


def parse_programa(texto) -> str:
    t = _norm(texto)
    if "sicue" in t:
        return Programa.SICUE
    if "erasmus" in t:
        return Programa.ERASMUS_INT if "int." in t else Programa.ERASMUS
    if "bilateral" in t:
        return Programa.BILATERAL_ESPECIFICO if "especifico" in t else Programa.BILATERAL
    return Programa.OTRO


def parse_plazo(texto) -> int:
    m = re.search(r"(\d+)", _norm(texto))
    if not m:
        raise ValueError(f"Plazo de convocatoria no reconocido: {texto!r}")
    return int(m.group(1))


def parse_semestre(texto) -> int | None:
    m = re.match(r"\s*(\d)", str(texto or ""))
    return int(m.group(1)) if m else None


def parse_curso(texto) -> str:
    m = re.search(r"(\d{4})\s*/\s*(\d{2,4})", str(texto or ""))
    if not m:
        raise ValueError(f"Curso académico no reconocido: {texto!r}")
    return f"{m.group(1)}/{m.group(2)[-2:]}"


def parse_nota(valor) -> Decimal | None:
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor).replace(",", ".").strip()).quantize(Decimal("0.0001"))
    except InvalidOperation:
        return None


def _entero(valor) -> int | None:
    try:
        return int(float(str(valor).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _mapear_cabeceras(cabecera) -> dict[str, int]:
    norm = [_norm(c) for c in cabecera]
    mapa = {}
    for clave, candidatos in COLUMNAS.items():
        for i, c in enumerate(norm):
            if c in candidatos:
                mapa[clave] = i
                break
    faltan = [c for c in OBLIGATORIAS if c not in mapa]
    faltan += [c for c, alternativa in ALTERNATIVAS.items() if c not in mapa and alternativa not in mapa]
    if faltan:
        raise FormatoNoReconocido(f"Faltan columnas obligatorias: {', '.join(faltan)}")
    return mapa


def _leer_filas(ruta: Path):
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    filas = ws.iter_rows(values_only=True)
    for cabecera in filas:
        if any(_norm(c) == "nome" for c in cabecera):
            break
    else:
        raise FormatoNoReconocido("No encuentro la fila de cabecera (columna 'NOME')")
    mapa = _mapear_cabeceras(cabecera)
    vacias = 0
    for fila in filas:
        if fila is None or all(v in (None, "") for v in fila):
            vacias += 1
            if vacias >= FILAS_VACIAS_FIN:
                break
            continue
        vacias = 0
        f = {k: (fila[i] if i < len(fila) else None) for k, i in mapa.items()}
        for clave, alternativa in ALTERNATIVAS.items():
            if clave not in mapa:
                f[clave] = f.get(alternativa)
        yield f


@transaction.atomic
def importar_adjudicaciones(ruta, listado: str = Listado.DEFINITIVA) -> Informe:
    ruta = Path(ruta)
    informe = Informe()
    vistas: dict[int, set[int]] = {}  # convocatoria_id -> ids de solicitudes presentes en el fichero
    adjudicadas: dict[tuple[int, int], list[Solicitud]] = {}

    for f in _leer_filas(ruta):
        informe.filas += 1
        dni = _texto(f["dni"])
        if not dni:
            informe.avisos.append(f"Fila {informe.filas}: sin DNI, se ignora")
            continue

        convocatoria, _ = Convocatoria.objects.get_or_create(
            curso=parse_curso(f["curso"]), plazo=parse_plazo(f["plazo"])
        )
        titulacion = None
        if _texto(f.get("titulacion")):
            titulacion, _ = Titulacion.objects.get_or_create(nombre=_texto(f["titulacion"]))

        estudiante, creado = Estudiante.objects.get_or_create(
            dni_hash=hash_dni(dni),
            defaults={
                "nombre_completo": _texto(f["nombre"]),
                "dni_mascara": enmascarar_dni(dni),
                "titulacion": titulacion,
            },
        )
        if not creado and titulacion and estudiante.titulacion_id is None:
            estudiante.titulacion = titulacion
            estudiante.save(update_fields=["titulacion"])

        institucion = buscar_o_crear_institucion(_texto(f["institucion"]), _texto(f["pais"]))
        estado, n_suplente = parse_estado(f["estado"])
        if estado == EstadoSolicitud.DESCONOCIDO:
            informe.avisos.append(
                f"{estudiante}: estado de solicitud no reconocido {_texto(f['estado'])!r}"
            )

        valores = {
            "titulacion": titulacion,
            "institucion": institucion,
            "programa": parse_programa(f["programa"]),
            "programa_texto": _texto(f["programa"])[:100],
            "semestre_inicio": parse_semestre(f.get("inicio")),
            "duracion_semestres": _entero(f.get("duracion")),
            "plaza_ofertada": _texto(f.get("plaza")),
            "certificado_idioma": _texto(f.get("idioma")),
            "observaciones_ori": _texto(f.get("observaciones")),
            "estado": estado,
            "estado_texto": _texto(f["estado"]),
            "numero_suplente": n_suplente,
            "nota_media": parse_nota(f.get("nota")),
            "otros_meritos": _texto(f.get("meritos")),
            "motivo_rechazo": _texto(f.get("motivo")),
            "condiciones": _texto(f.get("condiciones")),
            "listado": listado,
        }
        orden = _entero(f["orden"]) or 0
        solicitud = Solicitud.objects.filter(
            convocatoria=convocatoria, estudiante=estudiante, orden_preferencia=orden
        ).first()
        if solicitud is None:
            solicitud = Solicitud.objects.create(
                convocatoria=convocatoria, estudiante=estudiante, orden_preferencia=orden, **valores
            )
            informe.solicitudes_nuevas += 1
        else:
            cambios = {k: v for k, v in valores.items() if getattr(solicitud, k) != v}
            if cambios:
                for k, v in cambios.items():
                    setattr(solicitud, k, v)
                solicitud.save()
                informe.solicitudes_actualizadas += 1

        vistas.setdefault(convocatoria.id, set()).add(solicitud.id)
        if estado == EstadoSolicitud.ADJUDICADA:
            adjudicadas.setdefault((convocatoria.id, estudiante.id), []).append(solicitud)

    for (_, _), lista in adjudicadas.items():
        if len(lista) > 1:
            informe.avisos.append(
                f"{lista[0].estudiante}: {len(lista)} destinos adjudicados en el mismo plazo "
                f"({', '.join(s.institucion.nombre for s in lista)}); se usa la preferencia más alta"
            )
        _sincronizar_movilidad(min(lista, key=lambda s: s.orden_preferencia), informe)

    _revisar_retiradas(vistas, adjudicadas, informe)
    return informe


def _sincronizar_movilidad(solicitud: Solicitud, informe: Informe) -> None:
    datos = {
        "institucion": solicitud.institucion,
        "programa": solicitud.programa,
        "solicitud": solicitud,
        "semestre_inicio": solicitud.semestre_inicio,
        "duracion_semestres": solicitud.duracion_semestres,
        "condiciones": solicitud.condiciones,
    }
    movilidad = Movilidad.objects.filter(
        estudiante=solicitud.estudiante, convocatoria=solicitud.convocatoria
    ).first()
    if movilidad is None:
        movilidad = Movilidad.objects.create(
            estudiante=solicitud.estudiante, convocatoria=solicitud.convocatoria, **datos
        )
        movilidad.eventos.create(
            estado_nuevo=EstadoMovilidad.ADJUDICADA,
            nota=f"Creada desde el listado {solicitud.get_listado_display().lower()} de la ORI",
        )
        informe.movilidades_nuevas += 1
        return

    if movilidad.estado == EstadoMovilidad.ADJUDICACION_RETIRADA:
        movilidad.cambiar_estado(EstadoMovilidad.ADJUDICADA, nota="Vuelve a aparecer adjudicada en el listado")

    cambios = {k: v for k, v in datos.items() if getattr(movilidad, k) != v}
    if not cambios:
        return
    if movilidad.estado != EstadoMovilidad.ADJUDICADA and "institucion" in cambios:
        informe.avisos.append(
            f"{movilidad.estudiante}: el listado adjudica {solicitud.institucion.nombre} pero la "
            f"movilidad ya está en '{movilidad.get_estado_display()}' con {movilidad.institucion.nombre}; "
            "no se cambia, revísalo"
        )
        return
    for k, v in cambios.items():
        setattr(movilidad, k, v)
    movilidad.save()
    if "institucion" in cambios:
        movilidad.eventos.create(
            estado_anterior=movilidad.estado,
            estado_nuevo=movilidad.estado,
            nota=f"Destino cambiado a {solicitud.institucion.nombre} al importar el listado",
        )
    informe.movilidades_actualizadas += 1


def _revisar_retiradas(vistas, adjudicadas, informe: Informe) -> None:
    """Movilidades ADJUDICADA de las convocatorias del fichero cuyo estudiante ya no figura adjudicado."""
    for conv_id in vistas:
        con_adjudicacion = {est for (c, est) in adjudicadas if c == conv_id}
        for movilidad in Movilidad.objects.filter(convocatoria_id=conv_id).exclude(
            estudiante_id__in=con_adjudicacion
        ):
            if movilidad.estado == EstadoMovilidad.ADJUDICADA:
                movilidad.cambiar_estado(
                    EstadoMovilidad.ADJUDICACION_RETIRADA,
                    nota="Ya no figura como adjudicada en el último listado importado",
                )
                informe.avisos.append(f"{movilidad.estudiante}: adjudicación retirada en el nuevo listado")
            elif movilidad.estado != EstadoMovilidad.ADJUDICACION_RETIRADA:
                informe.avisos.append(
                    f"{movilidad.estudiante}: no figura como adjudicada en el listado pero la movilidad "
                    f"está en '{movilidad.get_estado_display()}'; revísalo"
                )
