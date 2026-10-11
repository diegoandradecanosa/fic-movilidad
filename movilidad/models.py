"""Modelo de datos.

Bloque 1: convocatorias, solicitudes de adjudicación y movilidades.
Bloque 2: catálogos de asignaturas, versiones del acuerdo académico (LA) y filas de correspondencia.

Ver CLAUDE.md para el dominio completo. La Movilidad es la entidad central: un estudiante,
un destino, un curso y un plazo de convocatoria.
"""

import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import m2m_changed
from django.dispatch import receiver


def hash_dni(dni: str) -> str:
    """HMAC-SHA256 del DNI normalizado. Permite emparejar sin guardar el DNI en claro."""
    normalizado = "".join(dni.split()).upper()
    return hmac.new(settings.DNI_HMAC_KEY.encode(), normalizado.encode(), hashlib.sha256).hexdigest()


def enmascarar_dni(dni: str) -> str:
    """'34291207R' -> '34*****7R' (mismo formato que las listas publicadas por la FIC)."""
    d = "".join(dni.split()).upper()
    if len(d) <= 4:
        return "*" * len(d)
    return d[:2] + "*" * (len(d) - 4) + d[-2:]


class Programa(models.TextChoices):
    ERASMUS = "ERASMUS", "Erasmus+ estudios (SMS)"
    ERASMUS_INT = "ERASMUS_INT", "Erasmus+ estudios internacional (KA131 Int.)"
    BILATERAL = "BILATERAL", "Bilateral SM estudios"
    BILATERAL_ESPECIFICO = "BILATERAL_ESP", "Bilateral SM específico"
    SICUE = "SICUE", "SICUE"
    OTRO = "OTRO", "Otro"


class Titulacion(models.Model):
    nombre = models.CharField(max_length=200, unique=True)
    codigo_plan = models.CharField(max_length=10, blank=True, help_text="p. ej. 10009 (GEI)")

    class Meta:
        verbose_name = "titulación"
        verbose_name_plural = "titulaciones"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Institucion(models.Model):
    nombre = models.CharField(max_length=255)
    pais = models.CharField(max_length=100, help_text="Código ISO 3166-1 alfa-2 (ES, IT, PT…)")
    ciudad = models.CharField(max_length=100, blank=True)
    codigo_erasmus = models.CharField(max_length=20, blank=True)
    web = models.URLField(blank=True)
    en_ewp = models.BooleanField(null=True, blank=True, help_text="¿Conectada a Erasmus Without Paper?")

    class Meta:
        verbose_name = "institución de destino"
        verbose_name_plural = "instituciones de destino"
        ordering = ["pais", "nombre"]
        constraints = [models.UniqueConstraint(fields=["nombre", "pais"], name="institucion_unica")]

    def __str__(self):
        return f"{self.nombre} ({self.pais})"


class RelacionDestino(models.Model):
    """Una 'oportunidad de movilidad' publicada en MoveON para la FIC (una institución puede tener varias)."""

    institucion = models.ForeignKey(Institucion, on_delete=models.CASCADE, related_name="relaciones")
    oportunidad = models.CharField(max_length=300, unique=True)
    programa = models.CharField(max_length=20, choices=Programa.choices)
    nivel = models.CharField(max_length=200, blank=True)
    isced = models.CharField(max_length=200, blank=True)
    idioma_ensenanza = models.CharField(max_length=300, blank=True)
    requisito_idioma = models.TextField(blank=True)
    plazas_restantes = models.IntegerField(null=True, blank=True)
    curso_plazas = models.CharField(max_length=9, blank=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        verbose_name = "relación de destino"
        verbose_name_plural = "relaciones de destino"

    def __str__(self):
        return self.oportunidad


class Convocatoria(models.Model):
    curso = models.CharField(max_length=9, help_text="2025/26")
    plazo = models.PositiveSmallIntegerField(help_text="1 = primer plazo, 2 = segundo plazo")

    class Meta:
        ordering = ["-curso", "plazo"]
        constraints = [models.UniqueConstraint(fields=["curso", "plazo"], name="convocatoria_unica")]

    def __str__(self):
        return f"{self.curso} · {self.plazo}.º plazo"


class Estudiante(models.Model):
    nombre_completo = models.CharField(max_length=200, help_text="Apellidos, Nombre")
    dni_hash = models.CharField(max_length=64, unique=True)
    dni_mascara = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    titulacion = models.ForeignKey(Titulacion, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["nombre_completo"]

    def __str__(self):
        return self.nombre_completo


class EstadoSolicitud(models.TextChoices):
    ADJUDICADA = "ADJUDICADA", "Adjudicada"
    SUPLENTE = "SUPLENTE", "Suplente"
    LISTA_ESPERA = "LISTA_ESPERA", "Lista de espera"
    RECHAZADA = "RECHAZADA", "Rechazada"
    NO_OFERTADA = "NO_OFERTADA", "No se oferta plaza"
    DESCONOCIDO = "DESCONOCIDO", "Sin reconocer"


class Listado(models.TextChoices):
    PROVISIONAL = "PROVISIONAL", "Provisional"
    DEFINITIVA = "DEFINITIVA", "Definitiva"


class Solicitud(models.Model):
    """Una fila del Excel de adjudicación de la ORI: estudiante × destino × orden de preferencia."""

    convocatoria = models.ForeignKey(Convocatoria, on_delete=models.CASCADE, related_name="solicitudes")
    estudiante = models.ForeignKey(Estudiante, on_delete=models.CASCADE, related_name="solicitudes")
    titulacion = models.ForeignKey(Titulacion, null=True, blank=True, on_delete=models.SET_NULL)
    institucion = models.ForeignKey(Institucion, on_delete=models.PROTECT)
    programa = models.CharField(max_length=20, choices=Programa.choices)
    programa_texto = models.CharField(max_length=100, help_text="Texto original del Excel")
    orden_preferencia = models.PositiveSmallIntegerField()
    semestre_inicio = models.PositiveSmallIntegerField(null=True, blank=True)
    duracion_semestres = models.PositiveSmallIntegerField(null=True, blank=True)
    plaza_ofertada = models.CharField(max_length=300, blank=True)
    certificado_idioma = models.CharField(max_length=200, blank=True)
    observaciones_ori = models.TextField(blank=True)
    estado = models.CharField(max_length=20, choices=EstadoSolicitud.choices)
    estado_texto = models.CharField(max_length=100, help_text="Texto original del Excel")
    numero_suplente = models.PositiveSmallIntegerField(null=True, blank=True)
    nota_media = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    otros_meritos = models.CharField(max_length=100, blank=True)
    motivo_rechazo = models.TextField(blank=True)
    condiciones = models.TextField(blank=True)
    listado = models.CharField(max_length=12, choices=Listado.choices)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "solicitudes"
        ordering = ["convocatoria", "estudiante", "orden_preferencia"]
        constraints = [
            models.UniqueConstraint(
                fields=["convocatoria", "estudiante", "orden_preferencia"], name="solicitud_unica"
            )
        ]

    def __str__(self):
        return f"{self.estudiante} → {self.institucion.nombre} ({self.orden_preferencia}.ª)"


class EstadoMovilidad(models.TextChoices):
    ADJUDICADA = "ADJUDICADA", "Adjudicada"
    ACEPTADA = "ACEPTADA", "Aceptada"
    RENUNCIADA = "RENUNCIADA", "Renunciada"
    LA_PREPARACION = "LA_PREPARACION", "LA en preparación"
    LA_FIRMADO = "LA_FIRMADO", "LA firmado"
    EN_MOVILIDAD = "EN_MOVILIDAD", "En movilidad"
    MODIFICACION_ABIERTA = "MODIFICACION_ABIERTA", "Modificación del LA abierta"
    FINALIZADA = "FINALIZADA", "Estancia finalizada"
    TOR_RECIBIDO = "TOR_RECIBIDO", "ToR recibido"
    ACTA_BORRADOR = "ACTA_BORRADOR", "Acta en borrador"
    ACTA_FIRMADA = "ACTA_FIRMADA", "Acta firmada"
    ENVIADA_ADMINISTRACION = "ENVIADA_ADMINISTRACION", "Enviada a Administración"
    CERRADA = "CERRADA", "Cerrada"
    ADJUDICACION_RETIRADA = "ADJUDICACION_RETIRADA", "Adjudicación retirada"


E = EstadoMovilidad

TRANSICIONES = {
    E.ADJUDICADA: {E.ACEPTADA, E.RENUNCIADA, E.ADJUDICACION_RETIRADA},
    E.ACEPTADA: {E.LA_PREPARACION, E.RENUNCIADA},
    E.LA_PREPARACION: {E.LA_FIRMADO, E.RENUNCIADA},
    E.LA_FIRMADO: {E.EN_MOVILIDAD, E.RENUNCIADA},
    E.EN_MOVILIDAD: {E.MODIFICACION_ABIERTA, E.FINALIZADA},
    E.MODIFICACION_ABIERTA: {E.EN_MOVILIDAD},
    E.FINALIZADA: {E.TOR_RECIBIDO},
    E.TOR_RECIBIDO: {E.ACTA_BORRADOR},
    E.ACTA_BORRADOR: {E.ACTA_FIRMADA},
    E.ACTA_FIRMADA: {E.ENVIADA_ADMINISTRACION},
    E.ENVIADA_ADMINISTRACION: {E.CERRADA, E.ACTA_BORRADOR},
    E.CERRADA: {E.ACTA_BORRADOR},
    E.RENUNCIADA: set(),
    E.ADJUDICACION_RETIRADA: {E.ADJUDICADA},
}


class TransicionNoPermitida(Exception):
    pass


class Movilidad(models.Model):
    estudiante = models.ForeignKey(Estudiante, on_delete=models.CASCADE, related_name="movilidades")
    convocatoria = models.ForeignKey(Convocatoria, on_delete=models.PROTECT, related_name="movilidades")
    institucion = models.ForeignKey(Institucion, on_delete=models.PROTECT, related_name="movilidades")
    programa = models.CharField(max_length=20, choices=Programa.choices)
    solicitud = models.ForeignKey(Solicitud, null=True, blank=True, on_delete=models.SET_NULL)
    semestre_inicio = models.PositiveSmallIntegerField(null=True, blank=True)
    duracion_semestres = models.PositiveSmallIntegerField(null=True, blank=True)
    condiciones = models.TextField(blank=True)
    fecha_llegada_prevista = models.DateField(
        null=True, blank=True, help_text="Llegada prevista: el Before debe estar firmado antes (RN-06)"
    )
    fecha_llegada = models.DateField(null=True, blank=True, help_text="Fecha del certificado de llegada")
    fecha_fin = models.DateField(null=True, blank=True, help_text="Fecha real de fin de estancia")
    fecha_inicio_s2 = models.DateField(
        null=True, blank=True, help_text="Inicio del 2.º semestre en destino (plazo de modificación, RN-05)"
    )
    estado = models.CharField(max_length=30, choices=EstadoMovilidad.choices, default=E.ADJUDICADA)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "movilidades"
        ordering = ["convocatoria", "estudiante"]
        constraints = [
            models.UniqueConstraint(fields=["estudiante", "convocatoria"], name="una_movilidad_por_plazo")
        ]

    def __str__(self):
        return f"{self.estudiante} · {self.institucion.nombre} · {self.convocatoria}"

    @transaction.atomic
    def cambiar_estado(self, nuevo, usuario=None, nota="", forzar=False):
        """Cambia el estado validando la transición y deja traza en EventoMovilidad."""
        nuevo = EstadoMovilidad(nuevo)
        actual = EstadoMovilidad(self.estado)
        if not forzar and nuevo not in TRANSICIONES[actual]:
            raise TransicionNoPermitida(f"{actual.label} → {nuevo.label} no está permitido")
        self.estado = nuevo
        self.save(update_fields=["estado", "actualizada"])
        EventoMovilidad.objects.create(
            movilidad=self, estado_anterior=actual, estado_nuevo=nuevo, usuario=usuario, nota=nota
        )


class EventoMovilidad(models.Model):
    movilidad = models.ForeignKey(Movilidad, on_delete=models.CASCADE, related_name="eventos")
    fecha = models.DateTimeField(auto_now_add=True)
    estado_anterior = models.CharField(max_length=30, choices=EstadoMovilidad.choices, blank=True)
    estado_nuevo = models.CharField(max_length=30, choices=EstadoMovilidad.choices, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    nota = models.TextField(blank=True)

    class Meta:
        verbose_name = "evento de movilidad"
        verbose_name_plural = "eventos de movilidad"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y %H:%M} {self.estado_anterior or '—'} → {self.estado_nuevo}"


# --- Bloque 2: acuerdo académico ---------------------------------------------------------------


class ModificacionNoPermitida(Exception):
    """Se intentó cambiar algo firmado (regla 3 de CLAUDE.md: nada firmado se sobrescribe)."""


class Cuatrimestre(models.TextChoices):
    C1 = "C1", "1.º cuatrimestre"
    C2 = "C2", "2.º cuatrimestre"
    VARIOS = "VARIOS", "Diferentes períodos"
    DESCONOCIDO = "DESCONOCIDO", "Sin especificar"


class _ECTSBloqueadosAlFirmar(models.Model):
    """Los ECTS de una asignatura usada en un LA firmado no cambian: el LA vigente y las
    validaciones se calculan con ellos. Si el destino cambia los créditos, se crea otra asignatura."""

    campo_filas = None  # nombre del related_name en FilaCorrespondencia

    class Meta:
        abstract = True

    def usada_en_version_firmada(self) -> bool:
        if not self.pk:
            return False
        filas = getattr(self, self.campo_filas)
        return filas.exclude(version__estado=EstadoVersionLA.BORRADOR).exists()

    def save(self, *args, **kwargs):
        if self.pk:
            anterior = type(self).objects.filter(pk=self.pk).values_list("ects", flat=True).first()
            if anterior is not None and anterior != self.ects and self.usada_en_version_firmada():
                raise ModificacionNoPermitida(
                    f"{self.nombre}: sus ECTS ({anterior}) están en un LA firmado; crea otra asignatura"
                )
        super().save(*args, **kwargs)


class AsignaturaUDC(_ECTSBloqueadosAlFirmar):
    """Materia del catálogo de la FIC para un curso académico (RF-04)."""

    campo_filas = "filas_udc"

    curso_academico = models.CharField(max_length=9, help_text="2026/27")
    titulacion = models.ForeignKey(Titulacion, on_delete=models.PROTECT, related_name="asignaturas")
    tipo = models.CharField(max_length=10, blank=True, help_text="Grao / Máster")
    codigo = models.CharField(max_length=10, help_text="Código de materia, p. ej. 200188")
    codigo_oficial = models.CharField(max_length=12, blank=True, help_text="Código 614G01xxx de los OLA")
    nombre = models.CharField(max_length=255)
    ects = models.DecimalField(max_digits=5, decimal_places=2)
    curso = models.CharField(max_length=5, blank=True, help_text="1, 2… o rango (3-4)")
    cuatrimestre = models.CharField(max_length=12, choices=Cuatrimestre.choices, default=Cuatrimestre.DESCONOCIDO)

    class Meta:
        verbose_name = "asignatura UDC"
        verbose_name_plural = "asignaturas UDC"
        ordering = ["titulacion", "curso", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["curso_academico", "codigo"], name="asignatura_udc_unica")
        ]

    def __str__(self):
        return f"{self.codigo} {self.nombre} ({self.ects:g} ECTS)"


class AsignaturaDestino(_ECTSBloqueadosAlFirmar):
    """Materia de una institución de destino; el catálogo crece con cada LA registrado (RF-05)."""

    campo_filas = "filas_destino"

    institucion = models.ForeignKey(Institucion, on_delete=models.PROTECT, related_name="asignaturas")
    codigo = models.CharField(max_length=50, blank=True)
    nombre = models.CharField(max_length=255)
    ects = models.DecimalField(max_digits=5, decimal_places=2)
    semestre = models.PositiveSmallIntegerField(null=True, blank=True, choices=[(1, "1.º"), (2, "2.º")])
    horas_totales = models.DecimalField(
        max_digits=6, decimal_places=1, null=True, blank=True,
        help_text="Solo si el destino no usa ECTS: ECTS = horas / 8,5 (RN-04)",
    )
    enlace_guia = models.URLField(blank=True)

    class Meta:
        verbose_name = "asignatura de destino"
        verbose_name_plural = "asignaturas de destino"
        ordering = ["institucion", "nombre"]

    def __str__(self):
        codigo = f"{self.codigo} " if self.codigo else ""
        return f"{codigo}{self.nombre} ({self.ects:g} ECTS)"


class TipoVersionLA(models.TextChoices):
    BEFORE = "BEFORE", "Before"
    DURING_S1 = "DURING_S1", "During 1.er semestre"
    DURING_S2 = "DURING_S2", "During 2.º semestre"
    AMPLIACION = "AMPLIACION", "Ampliación"


class FormatoLA(models.TextChoices):
    OLA_EWP = "OLA_EWP", "OLA en EWP"
    PDF_ORI = "PDF_ORI", "PDF/Word de la ORI"
    SICUE = "SICUE", "Acuerdo SICUE (Impreso C)"


class EstadoVersionLA(models.TextChoices):
    BORRADOR = "BORRADOR", "Borrador"
    FIRMADA = "FIRMADA", "Firmada"
    INVALIDADA = "INVALIDADA", "Invalidada"


# Cambios de estado permitidos en una versión. Firmada solo puede invalidarse (p. ej. la ORI anula
# un OLA-Before aprobado tarde); una corrección se hace con una versión nueva que la 'corrige'.
TRANSICIONES_VERSION = {
    EstadoVersionLA.BORRADOR: {EstadoVersionLA.BORRADOR, EstadoVersionLA.FIRMADA, EstadoVersionLA.INVALIDADA},
    EstadoVersionLA.FIRMADA: {EstadoVersionLA.FIRMADA, EstadoVersionLA.INVALIDADA},
    EstadoVersionLA.INVALIDADA: {EstadoVersionLA.INVALIDADA},
}

# Al firmar una versión, la movilidad avanza sola si está en el estado de origen.
AVANCE_AL_FIRMAR = {
    TipoVersionLA.BEFORE: (E.LA_PREPARACION, E.LA_FIRMADO),
    TipoVersionLA.DURING_S1: (E.MODIFICACION_ABIERTA, E.EN_MOVILIDAD),
    TipoVersionLA.DURING_S2: (E.MODIFICACION_ABIERTA, E.EN_MOVILIDAD),
}


# Versiones que el coordinador envía a Administración para el cambio de matrícula (caso del alumno B).
TIPOS_ENVIO_ADMINISTRACION = {TipoVersionLA.DURING_S1, TipoVersionLA.DURING_S2, TipoVersionLA.AMPLIACION}


class VersionLA(models.Model):
    """Una versión del acuerdo académico (RF-08). El LA vigente se deriva de las firmadas (RF-09)."""

    movilidad = models.ForeignKey(Movilidad, on_delete=models.CASCADE, related_name="versiones_la")
    numero = models.PositiveSmallIntegerField(blank=True, help_text="Orden de aplicación (1, 2, 3…)")
    tipo = models.CharField(max_length=12, choices=TipoVersionLA.choices)
    formato = models.CharField(max_length=10, choices=FormatoLA.choices)
    estado = models.CharField(max_length=12, choices=EstadoVersionLA.choices, default=EstadoVersionLA.BORRADOR)
    corrige = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="correcciones",
        help_text="Versión firmada a la que sustituye esta (se aplica en su lugar)",
    )
    fecha_solicitud = models.DateField(null=True, blank=True, help_text="Cuándo pidió el estudiante el cambio (RN-05)")
    fecha_firma_estudiante = models.DateField(null=True, blank=True)
    fecha_firma_udc = models.DateField(null=True, blank=True)
    fecha_firma_destino = models.DateField(null=True, blank=True)
    pdf = models.FileField(upload_to="la/%Y/", blank=True)
    motivo = models.TextField(blank=True)
    motivo_invalidacion = models.TextField(blank=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "versión del LA"
        verbose_name_plural = "versiones del LA"
        ordering = ["movilidad", "numero"]
        constraints = [models.UniqueConstraint(fields=["movilidad", "numero"], name="version_la_unica")]

    def __str__(self):
        return f"LA v{self.numero} {self.get_tipo_display()} ({self.get_estado_display().lower()})"

    def clean(self):
        if self.corrige_id:
            if self.corrige.movilidad_id != self.movilidad_id:
                raise ValidationError({"corrige": "La versión corregida es de otra movilidad."})
            if self.corrige.estado != EstadoVersionLA.FIRMADA:
                raise ValidationError({"corrige": "Solo se corrige una versión firmada."})
        if self.estado == EstadoVersionLA.INVALIDADA and not self.motivo_invalidacion:
            raise ValidationError({"motivo_invalidacion": "Indica por qué se invalida."})

    def save(self, *args, **kwargs):
        if self.pk:
            self._comprobar_inmutable()
        if not self.numero:
            ultimo = VersionLA.objects.filter(movilidad_id=self.movilidad_id).aggregate(m=models.Max("numero"))["m"]
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.estado != EstadoVersionLA.BORRADOR:
            raise ModificacionNoPermitida(f"{self}: no se borra una versión firmada o invalidada")
        return super().delete(*args, **kwargs)

    def _comprobar_inmutable(self):
        guardada = VersionLA.objects.filter(pk=self.pk).first()
        if guardada is None:
            return
        if self.estado not in TRANSICIONES_VERSION[EstadoVersionLA(guardada.estado)]:
            raise ModificacionNoPermitida(f"{guardada}: no puede pasar a {self.get_estado_display()}")
        if guardada.estado == EstadoVersionLA.BORRADOR:
            return
        editables = {"estado", "motivo_invalidacion"} if guardada.estado == EstadoVersionLA.FIRMADA else set()
        if not guardada.pdf:
            editables.add("pdf")  # se puede adjuntar el PDF que faltaba, nunca sustituirlo
        cambiados = [
            f.name for f in self._meta.concrete_fields
            if f.name not in editables and getattr(self, f.attname) != getattr(guardada, f.attname)
        ]
        if cambiados:
            raise ModificacionNoPermitida(f"{guardada}: no se puede modificar ({', '.join(cambiados)})")

    @property
    def editable(self) -> bool:
        return self.estado == EstadoVersionLA.BORRADOR

    @transaction.atomic
    def firmar(self, usuario=None):
        """Marca la versión como firmada; desde aquí es inmutable. Avanza la movilidad si procede."""
        if self.estado != EstadoVersionLA.BORRADOR:
            raise ModificacionNoPermitida(f"{self}: solo se firma un borrador")
        self.estado = EstadoVersionLA.FIRMADA
        self.save()
        origen, destino = AVANCE_AL_FIRMAR.get(self.tipo, (None, None))
        if origen and self.movilidad.estado == origen:
            self.movilidad.cambiar_estado(destino, usuario=usuario, nota=f"{self} firmada")
        if self.tipo in TIPOS_ENVIO_ADMINISTRACION and settings.ENVIO_AUTOMATICO_LA:
            from movilidad.envios import enviar_la_a_administracion

            transaction.on_commit(lambda: enviar_la_a_administracion(self, usuario=usuario))


class AccionFila(models.TextChoices):
    ALTA = "ALTA", "+ (alta)"
    BAJA = "BAJA", "− (baja)"


class FilaCorrespondencia(models.Model):
    """N materias de destino ↔ M materias UDC (RF-06). En el Before todas son altas."""

    version = models.ForeignKey(VersionLA, on_delete=models.CASCADE, related_name="filas")
    orden = models.PositiveSmallIntegerField(default=0)
    semestre = models.PositiveSmallIntegerField(choices=[(1, "1.º"), (2, "2.º")], default=1)
    accion = models.CharField(max_length=4, choices=AccionFila.choices, default=AccionFila.ALTA)
    materias_destino = models.ManyToManyField(AsignaturaDestino, related_name="filas_destino")
    materias_udc = models.ManyToManyField(AsignaturaUDC, blank=True, related_name="filas_udc")
    sin_reconocimiento = models.BooleanField(
        default=False, help_text="Actividad que figura en el LA pero no se reconoce (p. ej. curso de idioma)"
    )
    observaciones = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "fila de correspondencia"
        verbose_name_plural = "filas de correspondencia"
        ordering = ["version", "semestre", "orden", "id"]

    def __str__(self):
        signo = "+" if self.accion == AccionFila.ALTA else "−"
        return f"{signo} S{self.semestre} fila {self.orden}"

    def _comprobar_editable(self):
        if self.version_id and not VersionLA.objects.get(pk=self.version_id).editable:
            raise ModificacionNoPermitida("La versión del LA no es un borrador: sus filas no se modifican")

    def save(self, *args, **kwargs):
        self._comprobar_editable()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._comprobar_editable()
        return super().delete(*args, **kwargs)


@receiver(m2m_changed, sender=FilaCorrespondencia.materias_destino.through)
@receiver(m2m_changed, sender=FilaCorrespondencia.materias_udc.through)
def _materias_de_fila_firmada(sender, instance, action, reverse, pk_set, **kwargs):
    if not action.startswith("pre_"):
        return
    if not reverse:
        instance._comprobar_editable()
        return
    filas = FilaCorrespondencia.objects.filter(pk__in=pk_set or [])
    if action == "pre_clear":
        filas = getattr(instance, instance.campo_filas).all()
    if filas.exclude(version__estado=EstadoVersionLA.BORRADOR).exists():
        raise ModificacionNoPermitida("La asignatura está en un LA firmado")


# --- Bloque 3: plazos, tareas y comunicaciones ---------------------------------------------------


class TipoPlazo(models.TextChoices):
    FIRMAR_BEFORE = "FIRMAR_BEFORE", "Firmar el Before antes de la llegada"
    VENTANA_MOD_S1 = "VENTANA_MOD_S1", "Plazo de modificación del 1.er semestre"
    VENTANA_MOD_S2 = "VENTANA_MOD_S2", "Plazo de modificación del 2.º semestre"
    VENTANA_AMPLIACION = "VENTANA_AMPLIACION", "Plazo para pedir la ampliación"
    PREFERENCIA_AMPLIACION = "PREFERENCIA_AMPLIACION", "Ampliación con preferencia de financiación (ORI, 10/11)"
    VENTANA_REDUCCION = "VENTANA_REDUCCION", "Plazo para pedir la reducción"
    REVISAR_LA = "REVISAR_LA", "Revisar y firmar la versión del LA"
    ENVIAR_LA_ADMINISTRACION = "ENVIAR_LA_ADMINISTRACION", "Enviar el LA a Administración"
    ENVIAR_ACTA = "ENVIAR_ACTA", "Enviar el acta firmada (5 semanas desde el ToR)"
    PETICION = "PETICION", "Petición de Administración"
    OTRO = "OTRO", "Otro"


# Las ventanas caducan solas (no son trabajo del coordinador); el resto son tareas que vencen.
VENTANAS = {
    TipoPlazo.VENTANA_MOD_S1, TipoPlazo.VENTANA_MOD_S2, TipoPlazo.VENTANA_AMPLIACION,
    TipoPlazo.PREFERENCIA_AMPLIACION, TipoPlazo.VENTANA_REDUCCION,
}


class EstadoPlazo(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    CUMPLIDO = "CUMPLIDO", "Cumplido"
    DESCARTADO = "DESCARTADO", "Descartado"


class OrigenPlazo(models.TextChoices):
    REGLA = "REGLA", "Regla (automático)"
    MANUAL = "MANUAL", "Manual"


class Plazo(models.Model):
    """Plazo o tarea de una movilidad (RF-12, RF-13). Los de origen REGLA los mantiene plazos.py."""

    movilidad = models.ForeignKey(Movilidad, on_delete=models.CASCADE, related_name="plazos")
    tipo = models.CharField(max_length=30, choices=TipoPlazo.choices, default=TipoPlazo.OTRO)
    descripcion = models.CharField(max_length=300, blank=True)
    fecha_limite = models.DateField(null=True, blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        help_text="Vacío = coordinador",
    )
    estado = models.CharField(max_length=12, choices=EstadoPlazo.choices, default=EstadoPlazo.PENDIENTE)
    origen = models.CharField(max_length=10, choices=OrigenPlazo.choices, default=OrigenPlazo.MANUAL)
    regla = models.CharField(max_length=10, blank=True, help_text="p. ej. RN-05")
    version_la = models.ForeignKey(VersionLA, null=True, blank=True, on_delete=models.CASCADE, related_name="plazos")
    tor = models.ForeignKey("ToR", null=True, blank=True, on_delete=models.CASCADE, related_name="plazos")
    anotacion = models.ForeignKey("Anotacion", null=True, blank=True, on_delete=models.CASCADE, related_name="plazos")
    cumplido_en = models.DateTimeField(null=True, blank=True)
    nota = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "plazo / tarea"
        verbose_name_plural = "plazos y tareas"
        ordering = [models.F("fecha_limite").asc(nulls_last=True), "id"]

    def __str__(self):
        fecha = f" · {self.fecha_limite:%d/%m/%Y}" if self.fecha_limite else ""
        return f"{self.descripcion or self.get_tipo_display()}{fecha}"

    @property
    def es_ventana(self) -> bool:
        return self.tipo in VENTANAS

    def cerrar(self, estado=EstadoPlazo.CUMPLIDO, nota=""):
        from django.utils import timezone

        self.estado = estado
        self.cumplido_en = timezone.now()
        if nota:
            self.nota = f"{self.nota}\n{nota}".strip()
        self.save()


class CanalComunicacion(models.TextChoices):
    CORREO = "CORREO", "Correo"
    TEAMS = "TEAMS", "Teams"
    MOVEON = "MOVEON", "Aviso de MoveON"


class Comunicacion(models.Model):
    """Registro de un mensaje de la movilidad. Por ahora, los correos que envía la aplicación."""

    movilidad = models.ForeignKey(Movilidad, on_delete=models.CASCADE, related_name="comunicaciones")
    version_la = models.ForeignKey(VersionLA, null=True, blank=True, on_delete=models.SET_NULL)
    acta = models.ForeignKey("Acta", null=True, blank=True, on_delete=models.SET_NULL, related_name="comunicaciones")
    anotacion = models.ForeignKey(
        "Anotacion", null=True, blank=True, on_delete=models.SET_NULL, related_name="comunicaciones"
    )
    canal = models.CharField(max_length=10, choices=CanalComunicacion.choices, default=CanalComunicacion.CORREO)
    saliente = models.BooleanField(default=True)
    fecha = models.DateTimeField(auto_now_add=True)
    remitente = models.CharField(max_length=254, blank=True)
    para = models.TextField(blank=True)
    cc = models.TextField(blank=True)
    asunto = models.CharField(max_length=300)
    cuerpo = models.TextField(blank=True)
    adjuntos = models.TextField(blank=True, help_text="Nombres de los ficheros adjuntos")
    enviada = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "comunicación"
        verbose_name_plural = "comunicaciones"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.fecha:%d/%m/%Y %H:%M} {self.asunto}"


# --- Bloque 4: ToR, conversión de notas y acta ATM ------------------------------------------------


class EscalaNotas(models.Model):
    """Una escala de calificación de un país con su equivalencia española (Resol. 18/09/2017).

    Se importa del JSON oficial de equivalencias (comando importar_equivalencias)."""

    pais = models.CharField(max_length=100, help_text="Nombre del país en el JSON oficial (ALEMANIA)")
    pais_iso = models.CharField(max_length=2, blank=True, db_index=True)
    id_escala = models.CharField(max_length=120)
    tipo_nota_origen = models.CharField(max_length=30, blank=True)
    num_calificaciones = models.PositiveIntegerField(default=0)
    nota_minima_aprobatoria = models.CharField(max_length=20, blank=True)
    nota_maxima = models.CharField(max_length=20, blank=True)
    calificaciones = models.JSONField(help_text="{nota local: {equivalente_5_10, equivalente_1_4, equivalente_literal}}")

    class Meta:
        verbose_name = "escala de notas"
        verbose_name_plural = "escalas de notas"
        ordering = ["pais", "id_escala"]
        constraints = [models.UniqueConstraint(fields=["pais", "id_escala"], name="escala_unica")]

    def __str__(self):
        return f"{self.pais} · {self.id_escala}"


class RemitenteToR(models.TextChoices):
    ORI = "ORI", "ORI"
    DESTINO = "DESTINO", "Universidad de destino"
    ALUMNO = "ALUMNO", "Alumno/a"


class SemestresToR(models.TextChoices):
    S1 = "S1", "1.er semestre"
    S2 = "S2", "2.º semestre"
    AMBOS = "AMBOS", "Curso completo"


class EstadoToR(models.TextChoices):
    BORRADOR = "BORRADOR", "Pendiente de revisar"
    ACEPTADO = "ACEPTADO", "Revisado y aceptado"


class ToR(models.Model):
    """Transcript of Records recibido (RF-14). Sus líneas (a mano o extraídas por IA) son un
    borrador hasta que el coordinador lo acepta; solo los aceptados se usan en el acta."""

    movilidad = models.ForeignKey(Movilidad, on_delete=models.CASCADE, related_name="tors")
    fecha_recepcion = models.DateField(help_text="Fecha de recepción (inicia el plazo de 5 semanas, RN-15)")
    remitente = models.CharField(max_length=10, choices=RemitenteToR.choices, default=RemitenteToR.ORI)
    semestres = models.CharField(max_length=5, choices=SemestresToR.choices, default=SemestresToR.S1)
    parcial = models.BooleanField(default=False, help_text="Faltan notas o semestres")
    pdf = models.FileField(upload_to="tor/%Y/", blank=True)
    estado = models.CharField(max_length=10, choices=EstadoToR.choices, default=EstadoToR.BORRADOR)
    nombre_en_documento = models.CharField(max_length=200, blank=True)
    descripcion_escala = models.TextField(blank=True, help_text="Escala de calificación tal como la describe el ToR")
    nota_minima = models.CharField(max_length=20, blank=True)
    nota_maxima = models.CharField(max_length=20, blank=True)
    escala = models.ForeignKey(
        EscalaNotas, null=True, blank=True, on_delete=models.PROTECT,
        help_text="Vacío = la elige la aplicación según las notas del ToR",
    )
    extraido_con_ia = models.BooleanField(default=False)
    notas = models.TextField(blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ToR (certificado de notas)"
        verbose_name_plural = "ToR (certificados de notas)"
        ordering = ["movilidad", "fecha_recepcion"]

    def __str__(self):
        return f"ToR {self.get_semestres_display()} · recibido {self.fecha_recepcion:%d/%m/%Y}"

    def usado_en_acta_firmada(self) -> bool:
        return self.pk is not None and self.actas.exclude(estado=EstadoActa.BORRADOR).exists()

    @transaction.atomic
    def aceptar(self, usuario=None):
        self.estado = EstadoToR.ACEPTADO
        self.save(update_fields=["estado"])


class Superada(models.TextChoices):
    SI = "SI", "Sí"
    NO = "NO", "No"
    DESCONOCIDO = "?", "Según la nota"


class LineaToR(models.Model):
    tor = models.ForeignKey(ToR, on_delete=models.CASCADE, related_name="lineas")
    orden = models.PositiveSmallIntegerField(default=0)
    codigo = models.CharField(max_length=50, blank=True)
    titulo = models.CharField(max_length=255)
    ects = models.DecimalField(max_digits=5, decimal_places=2)
    nota = models.CharField(max_length=30, blank=True, help_text="Tal cual figura en el ToR (7,5 / A / ABS / NE)")
    superada = models.CharField(max_length=2, choices=Superada.choices, default=Superada.DESCONOCIDO)
    escala = models.ForeignKey(
        EscalaNotas, null=True, blank=True, on_delete=models.PROTECT,
        help_text="Solo si el ToR indica otra escala para esta materia",
    )
    asignatura_destino = models.ForeignKey(
        AsignaturaDestino, null=True, blank=True, on_delete=models.PROTECT, related_name="lineas_tor",
        help_text="Emparejamiento con la materia del LA",
    )

    class Meta:
        verbose_name = "línea del ToR"
        verbose_name_plural = "líneas del ToR"
        ordering = ["tor", "orden", "id"]

    def __str__(self):
        return f"{self.titulo}: {self.nota or '—'}"

    def _comprobar_editable(self):
        tor = ToR.objects.filter(pk=self.tor_id).first()
        if tor and tor.estado != EstadoToR.BORRADOR:
            raise ModificacionNoPermitida("El ToR ya está aceptado: devuélvelo a borrador para corregirlo")

    def save(self, *args, **kwargs):
        self._comprobar_editable()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._comprobar_editable()
        return super().delete(*args, **kwargs)


class EstadoActa(models.TextChoices):
    BORRADOR = "BORRADOR", "Borrador"
    FIRMADA = "FIRMADA", "Firmada"
    ENVIADA = "ENVIADA", "Enviada a Administración"


class Acta(models.Model):
    """Acta de reconocimiento 'After the Mobility' (RF-17…RF-20). Las firmadas no se modifican:
    una corrección es un acta nueva que 'corrige' a la anterior (v2, v3…)."""

    movilidad = models.ForeignKey(Movilidad, on_delete=models.CASCADE, related_name="actas")
    numero = models.PositiveSmallIntegerField(blank=True)
    estado = models.CharField(max_length=10, choices=EstadoActa.choices, default=EstadoActa.BORRADOR)
    corrige = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="correcciones")
    motivo_correccion = models.TextField(blank=True, help_text="Escala, materia omitida, error de nota…")
    tors = models.ManyToManyField(ToR, related_name="actas", blank=True)
    avisos = models.JSONField(default=list, blank=True)
    docx = models.FileField(upload_to="actas/%Y/", blank=True)
    pdf = models.FileField(upload_to="actas/%Y/", blank=True, help_text="PDF del borrador (si hay LibreOffice)")
    pdf_firmado = models.FileField(upload_to="actas/%Y/", blank=True, help_text="Acta firmada con FNMT")
    fecha_firma = models.DateField(null=True, blank=True)
    fecha_envio = models.DateTimeField(null=True, blank=True)
    creada = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "acta de reconocimiento"
        verbose_name_plural = "actas de reconocimiento"
        ordering = ["movilidad", "numero"]
        constraints = [models.UniqueConstraint(fields=["movilidad", "numero"], name="acta_unica")]

    def __str__(self):
        return f"Acta v{self.numero} ({self.get_estado_display().lower()})"

    @property
    def editable(self):
        return self.estado == EstadoActa.BORRADOR

    def clean(self):
        if self.corrige_id:
            if self.corrige.movilidad_id != self.movilidad_id:
                raise ValidationError({"corrige": "El acta corregida es de otra movilidad."})
            if self.corrige.estado == EstadoActa.BORRADOR:
                raise ValidationError({"corrige": "Un borrador se edita, no se corrige."})
            if not self.motivo_correccion:
                raise ValidationError({"motivo_correccion": "Indica el motivo de la corrección."})

    def save(self, *args, **kwargs):
        if self.pk:
            guardada = Acta.objects.filter(pk=self.pk).first()
            if guardada and guardada.estado != EstadoActa.BORRADOR:
                editables = {"estado", "fecha_envio"} if guardada.estado == EstadoActa.FIRMADA else set()
                cambiados = [
                    f.name for f in self._meta.concrete_fields
                    if f.name not in editables and getattr(self, f.attname) != getattr(guardada, f.attname)
                ]
                if cambiados or (guardada.estado == EstadoActa.FIRMADA and self.estado == EstadoActa.BORRADOR):
                    raise ModificacionNoPermitida(f"{guardada}: firmada, no se modifica; crea una corrección")
        if not self.numero:
            ultimo = Acta.objects.filter(movilidad_id=self.movilidad_id).aggregate(m=models.Max("numero"))["m"]
            self.numero = (ultimo or 0) + 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.editable:
            raise ModificacionNoPermitida(f"{self}: no se borra un acta firmada")
        return super().delete(*args, **kwargs)

    @transaction.atomic
    def firmar(self, pdf_firmado, fecha_firma, usuario=None):
        """RF-18: se sube el PDF firmado (FNMT, fuera de la aplicación) y el acta queda vigente."""
        if not self.editable:
            raise ModificacionNoPermitida(f"{self}: ya está firmada")
        if not pdf_firmado:
            raise ValidationError("Falta el PDF firmado.")
        self.pdf_firmado, self.fecha_firma = pdf_firmado, fecha_firma
        self.save()
        self.estado = EstadoActa.FIRMADA
        self.save()
        if self.movilidad.estado == E.ACTA_BORRADOR:
            self.movilidad.cambiar_estado(E.ACTA_FIRMADA, usuario=usuario, nota=f"{self} firmada")


class FilaActaC(models.Model):
    """Tabla C: materias cursadas en destino (todas, superadas o no)."""

    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="filas_c")
    orden = models.PositiveSmallIntegerField(default=0)
    codigo = models.CharField(max_length=50, blank=True)
    titulo = models.CharField(max_length=255)
    superada = models.BooleanField(default=True)
    ects = models.DecimalField(max_digits=5, decimal_places=2)
    nota_original = models.CharField(max_length=30, blank=True)

    class Meta:
        verbose_name = "fila de la tabla C"
        verbose_name_plural = "tabla C (materias de destino)"
        ordering = ["acta", "orden"]


class FilaActaD(models.Model):
    """Tabla D: materias reconocidas en la UDC con la nota ya convertida."""

    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="filas_d")
    orden = models.PositiveSmallIntegerField(default=0)
    asignatura_udc = models.ForeignKey(AsignaturaUDC, null=True, blank=True, on_delete=models.PROTECT)
    codigo = models.CharField(max_length=20, blank=True)
    titulo = models.CharField(max_length=255)
    ects = models.DecimalField(max_digits=5, decimal_places=2, help_text="ECTS reconocidos (0 si no superada)")
    nota = models.CharField(max_length=30, help_text="Nota española (8,5), «Suspenso» o «No presentado»")
    detalle = models.TextField(blank=True, help_text="Cómo se calculó")

    class Meta:
        verbose_name = "fila de la tabla D"
        verbose_name_plural = "tabla D (materias UDC)"
        ordering = ["acta", "orden"]


def _fila_acta_editable(sender, instance, **kwargs):
    if not Acta.objects.get(pk=instance.acta_id).editable:
        raise ModificacionNoPermitida("El acta está firmada: sus tablas no se modifican")


for _modelo in (FilaActaC, FilaActaD):
    models.signals.pre_save.connect(_fila_acta_editable, sender=_modelo)
    models.signals.pre_delete.connect(_fila_acta_editable, sender=_modelo)


# --- Bloque 5: anotaciones y peticiones de Administración (RF-30) ---------------------------------


class DestinatarioAnotacion(models.TextChoices):
    NINGUNO = "NINGUNO", "Nota interna (sin petición)"
    COORDINADOR = "COORDINADOR", "Petición al coordinador"
    ALUMNO = "ALUMNO", "Petición al alumno/a"


class EstadoAnotacion(models.TextChoices):
    ABIERTA = "ABIERTA", "Abierta"
    RESPONDIDA = "RESPONDIDA", "Respondida"
    CERRADA = "CERRADA", "Cerrada"


class Anotacion(models.Model):
    """Anotación en un expediente (RF-30). Administración la usa para pedir algo al coordinador
    («falta el LA modificado para cambiar la matrícula») o al alumno; el destinatario recibe aviso."""

    movilidad = models.ForeignKey(Movilidad, on_delete=models.CASCADE, related_name="anotaciones")
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    creada = models.DateTimeField(auto_now_add=True)
    texto = models.TextField()
    destinatario = models.CharField(max_length=12, choices=DestinatarioAnotacion.choices,
                                    default=DestinatarioAnotacion.NINGUNO)
    fecha_limite = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=EstadoAnotacion.choices, default=EstadoAnotacion.ABIERTA)
    respuesta = models.TextField(blank=True)
    respondida_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="+")
    respondida_en = models.DateTimeField(null=True, blank=True)
    cerrada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "anotación"
        verbose_name_plural = "anotaciones"
        ordering = ["-creada"]

    def __str__(self):
        return f"{self.get_destinatario_display()}: {self.texto[:60]}"

    @property
    def es_peticion(self):
        return self.destinatario != DestinatarioAnotacion.NINGUNO
