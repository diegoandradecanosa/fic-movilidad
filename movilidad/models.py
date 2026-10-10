"""Modelo de datos del bloque 1: convocatorias, solicitudes de adjudicación y movilidades.

Ver CLAUDE.md para el dominio completo. La Movilidad es la entidad central: un estudiante,
un destino, un curso y un plazo de convocatoria.
"""

import hashlib
import hmac

from django.conf import settings
from django.db import models, transaction


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
