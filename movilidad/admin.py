"""Admin de Django: primera interfaz del coordinador.

Administración del centro usará el permiso 'view' (solo lectura) hasta que exista la vista propia
con anotaciones (RF-30).
"""

from django.contrib import admin

from movilidad import models as m


class EventoInline(admin.TabularInline):
    model = m.EventoMovilidad
    extra = 0
    can_delete = False
    readonly_fields = ["fecha", "estado_anterior", "estado_nuevo", "usuario", "nota"]


@admin.register(m.Movilidad)
class MovilidadAdmin(admin.ModelAdmin):
    list_display = ["estudiante", "institucion", "programa", "convocatoria", "semestre_inicio", "duracion_semestres", "estado"]
    list_filter = ["convocatoria", "estado", "programa", "institucion__pais"]
    search_fields = ["estudiante__nombre_completo", "institucion__nombre"]
    readonly_fields = ["estado", "creada", "actualizada", "solicitud"]
    inlines = [EventoInline]


@admin.register(m.Solicitud)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ["estudiante", "orden_preferencia", "institucion", "programa", "estado", "numero_suplente", "nota_media", "listado"]
    list_filter = ["convocatoria", "estado", "programa", "listado"]
    search_fields = ["estudiante__nombre_completo", "institucion__nombre"]


@admin.register(m.Estudiante)
class EstudianteAdmin(admin.ModelAdmin):
    list_display = ["nombre_completo", "dni_mascara", "titulacion", "email"]
    search_fields = ["nombre_completo", "email"]
    exclude = ["dni_hash"]
    readonly_fields = ["dni_mascara"]


@admin.register(m.Institucion)
class InstitucionAdmin(admin.ModelAdmin):
    list_display = ["nombre", "pais", "ciudad", "codigo_erasmus", "en_ewp"]
    list_filter = ["pais", "en_ewp"]
    search_fields = ["nombre", "codigo_erasmus"]


@admin.register(m.RelacionDestino)
class RelacionDestinoAdmin(admin.ModelAdmin):
    list_display = ["oportunidad", "programa", "requisito_idioma", "plazas_restantes"]
    list_filter = ["programa", "institucion__pais"]
    search_fields = ["oportunidad"]


admin.site.register(m.Convocatoria)
admin.site.register(m.Titulacion)
admin.site.site_header = "FIC · Movilidad"
