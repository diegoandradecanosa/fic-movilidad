"""Admin de Django: primera interfaz del coordinador.

Administración del centro usará el permiso 'view' (solo lectura) hasta que exista la vista propia
con anotaciones (RF-30).
"""

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from movilidad import models as m
from movilidad.envios import enviar_la_a_administracion
from movilidad.la_vigente import la_vigente
from movilidad.plazos import nivel_alerta, pendientes
from movilidad.validaciones_la import validar


class EventoInline(admin.TabularInline):
    model = m.EventoMovilidad
    extra = 0
    can_delete = False
    readonly_fields = ["fecha", "estado_anterior", "estado_nuevo", "usuario", "nota"]


class VersionLAInline(admin.TabularInline):
    """Historial de versiones en la ficha de la movilidad; se editan en su propia página."""

    model = m.VersionLA
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["numero", "tipo", "formato", "estado", "corrige", "fecha_firma_estudiante", "fecha_firma_udc",
              "fecha_firma_destino", "pdf"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class PlazoInline(admin.TabularInline):
    model = m.Plazo
    extra = 0
    fields = ["tipo", "descripcion", "fecha_limite", "responsable", "estado", "origen", "nota"]
    readonly_fields = ["origen"]
    show_change_link = True


class ComunicacionInline(admin.TabularInline):
    model = m.Comunicacion
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["fecha", "canal", "asunto", "para", "cc", "enviada", "error"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(m.Movilidad)
class MovilidadAdmin(admin.ModelAdmin):
    list_display = ["estudiante", "institucion", "programa", "convocatoria", "semestre_inicio", "duracion_semestres", "estado"]
    list_filter = ["convocatoria", "estado", "programa", "institucion__pais"]
    search_fields = ["estudiante__nombre_completo", "institucion__nombre"]
    readonly_fields = ["estado", "creada", "actualizada", "solicitud", "acuerdo_academico"]
    inlines = [VersionLAInline, PlazoInline, ComunicacionInline, EventoInline]

    @admin.display(description="Acuerdo académico")
    def acuerdo_academico(self, obj):
        if not obj.pk:
            return "—"
        return format_html(
            '<a href="{}">Ver LA vigente</a> · <a href="{}?movilidad={}">Añadir versión del LA</a>',
            reverse("admin:movilidad_movilidad_la_vigente", args=[obj.pk]),
            reverse("admin:movilidad_versionla_add"), obj.pk,
        )

    def get_urls(self):
        return [
            path("<int:pk>/la-vigente/", self.admin_site.admin_view(self.vista_la_vigente),
                 name="movilidad_movilidad_la_vigente"),
        ] + super().get_urls()

    def vista_la_vigente(self, request, pk):
        """RF-09: el LA consolidado, solo lectura. No hay formulario: se deriva de las versiones."""
        movilidad = get_object_or_404(m.Movilidad, pk=pk)
        if not self.has_view_permission(request, movilidad):
            raise PermissionDenied
        la = la_vigente(movilidad)
        semestres = [
            {"numero": s, "grupos": [g for g in la.grupos if g.semestre == s],
             "ects_destino": la.ects_destino(s), "ects_udc": la.ects_udc(s)}
            for s in la.semestres
        ]
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": movilidad,
            "title": f"LA vigente · {movilidad}",
            "la": la,
            "semestres": semestres,
            "avisos": validar(movilidad),
        }
        return TemplateResponse(request, "admin/movilidad/la_vigente.html", context)


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


@admin.register(m.AsignaturaUDC)
class AsignaturaUDCAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "ects", "curso", "cuatrimestre", "titulacion", "curso_academico"]
    list_filter = ["curso_academico", "tipo", "titulacion", "cuatrimestre"]
    search_fields = ["codigo", "codigo_oficial", "nombre"]


@admin.register(m.AsignaturaDestino)
class AsignaturaDestinoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "codigo", "ects", "semestre", "horas_totales", "institucion"]
    list_filter = ["institucion__pais"]
    search_fields = ["nombre", "codigo", "institucion__nombre"]
    autocomplete_fields = ["institucion"]

    def get_readonly_fields(self, request, obj=None):
        return ["ects"] if obj and obj.usada_en_version_firmada() else []


class FilaInline(admin.TabularInline):
    model = m.FilaCorrespondencia
    extra = 0
    fields = ["semestre", "orden", "accion", "materias_destino", "materias_udc", "sin_reconocimiento", "observaciones"]
    autocomplete_fields = ["materias_destino", "materias_udc"]

    def _editable(self, obj):
        return obj is None or obj.editable

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


class VersionLAForm(forms.ModelForm):
    class Meta:
        model = m.VersionLA
        fields = "__all__"

    def clean_estado(self):
        nuevo = self.cleaned_data["estado"]
        if self.instance.pk:
            actual = m.EstadoVersionLA(m.VersionLA.objects.get(pk=self.instance.pk).estado)
            if nuevo not in m.TRANSICIONES_VERSION[actual]:
                raise ValidationError(f"Una versión {actual.label.lower()} no puede pasar a ese estado.")
        return nuevo


@admin.register(m.VersionLA)
class VersionLAAdmin(admin.ModelAdmin):
    """Versiones del LA. Firmada = inmutable (solo se puede invalidar); para corregir, versión nueva."""

    form = VersionLAForm
    list_display = ["movilidad", "numero", "tipo", "formato", "estado", "fecha_firma_udc"]
    list_filter = ["estado", "tipo", "formato", "movilidad__convocatoria"]
    search_fields = ["movilidad__estudiante__nombre_completo", "movilidad__institucion__nombre"]
    autocomplete_fields = ["movilidad"]
    readonly_fields = ["avisos", "creada"]
    inlines = [FilaInline]
    actions = ["firmar", "enviar_administracion"]

    def get_readonly_fields(self, request, obj=None):
        if obj is None or obj.editable:
            return self.readonly_fields
        bloqueados = [f.name for f in m.VersionLA._meta.concrete_fields if f.name != "id"]
        if obj.estado == m.EstadoVersionLA.FIRMADA:
            bloqueados = [f for f in bloqueados if f not in ("estado", "motivo_invalidacion")]
        if not obj.pdf:
            bloqueados.remove("pdf")
        return bloqueados + self.readonly_fields

    @admin.display(description="Avisos (RN-01…RN-07)")
    def avisos(self, obj):
        if not obj or not obj.pk:
            return "Se comprueban al guardar."
        avisos = validar(obj.movilidad, version=obj if obj.editable else None)
        if not avisos:
            return "Sin avisos."
        return format_html("<ul>{}</ul>", format_html_join("", "<li><b>{}</b>: {}</li>",
                                                          ((a.regla, a.mensaje) for a in avisos)))

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.editable) and super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            obj.delete()  # VersionLA.delete() impide borrar lo firmado

    def save_model(self, request, obj, form, change):
        # La firma se aplica en save_related, después de guardar las filas del borrador.
        obj._firmar = obj.estado == m.EstadoVersionLA.FIRMADA and (
            not change or form.initial.get("estado") == m.EstadoVersionLA.BORRADOR
        )
        if obj._firmar:
            obj.estado = m.EstadoVersionLA.BORRADOR
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
        if getattr(obj, "_firmar", False):
            obj.firmar(usuario=request.user)
        elif change and "pdf" in form.changed_data and obj.pdf and self._envio_pendiente(obj):
            # Se adjunta el PDF que faltaba en un During ya firmado: se envía ahora.
            c = enviar_la_a_administracion(obj, usuario=request.user)
            if c and c.enviada:
                messages.info(request, f"LA enviado a Administración ({c.para}).")
        for aviso in validar(obj.movilidad, version=obj if obj.editable else None):
            messages.warning(request, str(aviso))

    @staticmethod
    def _envio_pendiente(obj):
        return (
            settings.ENVIO_AUTOMATICO_LA and obj.estado == m.EstadoVersionLA.FIRMADA
            and obj.plazos.filter(tipo=m.TipoPlazo.ENVIAR_LA_ADMINISTRACION, estado=m.EstadoPlazo.PENDIENTE).exists()
        )

    @admin.action(description="Marcar como firmadas (las versiones pasan a ser inmutables)")
    def firmar(self, request, queryset):
        firmadas = 0
        for v in queryset.filter(estado=m.EstadoVersionLA.BORRADOR):
            v.firmar(usuario=request.user)
            firmadas += 1
        self.message_user(request, f"{firmadas} versión(es) firmada(s).")

    @admin.action(description="Enviar a Administración (con copia al coordinador y al alumno)")
    def enviar_administracion(self, request, queryset):
        for v in queryset:
            c = enviar_la_a_administracion(v, usuario=request.user)
            if c is None or not c.enviada:
                motivo = v.plazos.filter(tipo=m.TipoPlazo.ENVIAR_LA_ADMINISTRACION).last()
                self.message_user(request, f"{v}: no enviado. {motivo.nota if motivo else ''}", messages.ERROR)
            else:
                self.message_user(request, f"{v}: enviado a {c.para} (cc {c.cc or '—'}). {c.error}")


COLORES_ALERTA = {"VENCIDA": "#ba2121", "URGENTE": "#ba2121", "PROXIMA": "#b26b00", "CADUCADA": "#777"}


def alerta_html(plazo, hoy):
    nivel = nivel_alerta(plazo, hoy)
    if not nivel:
        return ""
    return format_html('<b style="color:{}">{}</b>', COLORES_ALERTA[nivel], nivel.capitalize())


@admin.register(m.Plazo)
class PlazoAdmin(admin.ModelAdmin):
    list_display = ["fecha_limite", "alerta", "movilidad", "tipo", "descripcion", "responsable", "estado", "origen"]
    list_filter = ["estado", "tipo", "origen", "responsable", "movilidad__convocatoria"]
    search_fields = ["descripcion", "movilidad__estudiante__nombre_completo", "movilidad__institucion__nombre"]
    autocomplete_fields = ["movilidad"]
    readonly_fields = ["origen", "regla", "version_la", "cumplido_en", "creado"]
    actions = ["marcar_cumplidos", "descartar"]

    @admin.display(description="Alerta")
    def alerta(self, obj):
        return alerta_html(obj, timezone.localdate())

    @admin.action(description="Marcar como cumplidos")
    def marcar_cumplidos(self, request, queryset):
        for p in queryset.filter(estado=m.EstadoPlazo.PENDIENTE):
            p.cerrar(m.EstadoPlazo.CUMPLIDO, f"Cerrado por {request.user}")

    @admin.action(description="Descartar")
    def descartar(self, request, queryset):
        for p in queryset.filter(estado=m.EstadoPlazo.PENDIENTE):
            p.cerrar(m.EstadoPlazo.DESCARTADO, f"Descartado por {request.user}")

    def get_urls(self):
        return [
            path("pendientes/", self.admin_site.admin_view(self.vista_pendientes), name="movilidad_plazo_pendientes"),
        ] + super().get_urls()

    def vista_pendientes(self, request):
        """RF-13: bandeja «Mis pendientes», ordenada por fecha límite."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        if request.method == "POST":
            if not self.has_change_permission(request):
                raise PermissionDenied
            plazo = get_object_or_404(m.Plazo, pk=request.POST.get("plazo"), estado=m.EstadoPlazo.PENDIENTE)
            accion = request.POST.get("accion")
            if accion in ("cumplido", "descartar"):
                estado = m.EstadoPlazo.CUMPLIDO if accion == "cumplido" else m.EstadoPlazo.DESCARTADO
                plazo.cerrar(estado, f"{estado.label} por {request.user}")
                self.message_user(request, f"«{plazo}»: {estado.label.lower()}.")
            return redirect(request.path)
        hoy = timezone.localdate()
        filas = [{"plazo": p, "alerta": alerta_html(p, hoy)} for p in pendientes(hoy, request.user)]
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Mis pendientes",
            "filas": filas,
            "hoy": hoy,
        }
        return TemplateResponse(request, "admin/movilidad/pendientes.html", context)


@admin.register(m.Comunicacion)
class ComunicacionAdmin(admin.ModelAdmin):
    """Registro de comunicaciones: solo lectura (es la traza de lo enviado)."""

    list_display = ["fecha", "movilidad", "canal", "asunto", "para", "enviada"]
    list_filter = ["canal", "enviada"]
    search_fields = ["asunto", "movilidad__estudiante__nombre_completo"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in m.Comunicacion._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(m.Convocatoria)
admin.site.register(m.Titulacion)
admin.site.site_header = "FIC · Movilidad"
