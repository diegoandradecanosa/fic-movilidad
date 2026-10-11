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
from movilidad.acta import crear_correccion, generar_borrador, proponer_emparejamiento
from movilidad.acta_docx import RecursosNoDisponibles, generar_documentos
from movilidad.envios import EnvioImposible, enviar_atm, enviar_la_a_administracion
from movilidad import extraccion_ia
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


class AnotacionInline(admin.TabularInline):
    model = m.Anotacion
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["creada", "autor", "destinatario", "texto", "fecha_limite", "estado", "respuesta"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ToRInline(admin.TabularInline):
    model = m.ToR
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["fecha_recepcion", "remitente", "semestres", "parcial", "estado", "pdf"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ActaInline(admin.TabularInline):
    model = m.Acta
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ["numero", "estado", "corrige", "fecha_firma", "fecha_envio", "docx", "pdf_firmado"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class CambioEstadoForm(forms.Form):
    estado = forms.ChoiceField(label="Nuevo estado")
    nota = forms.CharField(label="Nota / motivo", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    forzar = forms.BooleanField(
        label="Forzar (saltar transiciones; p. ej. cargar casos de cursos anteriores)", required=False)

    def __init__(self, *args, permitidos=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.permitidos = set(permitidos)
        self.fields["estado"].choices = m.EstadoMovilidad.choices

    def clean(self):
        datos = super().clean()
        if datos.get("forzar"):
            if not datos.get("nota", "").strip():
                self.add_error("nota", "Al forzar un cambio de estado hay que indicar el motivo.")
        elif datos.get("estado") and datos["estado"] not in self.permitidos:
            self.add_error("estado", "Transición no permitida desde el estado actual (marca «Forzar» si es un caso "
                                     "especial).")
        return datos


@admin.register(m.Movilidad)
class MovilidadAdmin(admin.ModelAdmin):
    list_display = ["estudiante", "institucion", "programa", "convocatoria", "semestre_inicio", "duracion_semestres", "estado"]
    list_filter = ["convocatoria", "estado", "programa", "institucion__pais"]
    search_fields = ["estudiante__nombre_completo", "institucion__nombre"]
    readonly_fields = ["estado", "creada", "actualizada", "solicitud", "acuerdo_academico"]
    inlines = [AnotacionInline, VersionLAInline, ToRInline, ActaInline, PlazoInline, ComunicacionInline, EventoInline]
    actions = ["cambiar_estado", "generar_acta"]

    @admin.action(description="Cambiar estado (queda en el historial)", permissions=["change"])
    def cambiar_estado(self, request, queryset):
        movilidades = list(queryset.select_related("estudiante"))
        permitidos = set.intersection(*(set(m.TRANSICIONES[m.EstadoMovilidad(x.estado)]) for x in movilidades))
        form = CambioEstadoForm(request.POST if "estado" in request.POST else None, permitidos=permitidos)
        if form.is_valid():
            nuevo, forzar = form.cleaned_data["estado"], form.cleaned_data["forzar"]
            nota = form.cleaned_data["nota"].strip() + (" [forzado]" if forzar else "")
            for movilidad in movilidades:
                try:
                    movilidad.cambiar_estado(nuevo, usuario=request.user, nota=nota.strip(), forzar=forzar)
                except m.TransicionNoPermitida as e:
                    self.message_user(request, f"{movilidad}: {e}", messages.ERROR)
            self.message_user(request, f"Estado actualizado a «{m.EstadoMovilidad(nuevo).label}».")
            return None
        return TemplateResponse(request, "admin/movilidad/movilidad/cambiar_estado.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "title": "Cambiar estado",
            "movilidades": movilidades, "form": form,
            "permitidos": [m.EstadoMovilidad(e).label for e in m.EstadoMovilidad if e in permitidos],
        })

    @admin.action(description="Generar (o regenerar) el borrador del acta con los ToR aceptados")
    def generar_acta(self, request, queryset):
        for movilidad in queryset:
            try:
                acta = generar_borrador(movilidad, usuario=request.user)
            except ValueError as e:
                self.message_user(request, f"{movilidad}: {e}", messages.ERROR)
                continue
            self.message_user(request, f"{movilidad}: {acta} con {len(acta.avisos)} aviso(s).")

    @admin.display(description="Acuerdo académico")
    def acuerdo_academico(self, obj):
        if not obj.pk:
            return "—"
        return format_html(
            '<a href="{}">Ver LA vigente</a> · <a href="{}?movilidad={}">Añadir versión del LA</a> · '
            '<a href="{}?movilidad={}">Registrar ToR</a>',
            reverse("admin:movilidad_movilidad_la_vigente", args=[obj.pk]),
            reverse("admin:movilidad_versionla_add"), obj.pk,
            reverse("admin:movilidad_tor_add"), obj.pk,
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


@admin.register(m.Anotacion)
class AnotacionAdmin(admin.ModelAdmin):
    """Las anotaciones se crean y responden desde la ficha del expediente (envían avisos)."""

    list_display = ["creada", "movilidad", "autor", "destinatario", "estado", "fecha_limite"]
    list_filter = ["estado", "destinatario"]
    search_fields = ["texto", "movilidad__estudiante__nombre_completo"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in m.Anotacion._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(m.EscalaNotas)
class EscalaNotasAdmin(admin.ModelAdmin):
    """Tabla oficial de equivalencias (se importa del JSON; solo lectura)."""

    list_display = ["pais", "pais_iso", "id_escala", "num_calificaciones", "nota_minima_aprobatoria", "nota_maxima"]
    list_filter = ["pais_iso"]
    search_fields = ["pais", "id_escala"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in m.EscalaNotas._meta.fields]

    def has_add_permission(self, request):
        return False


def _html_avisos(avisos):
    if not avisos:
        return "Sin avisos."
    return format_html("<ul>{}</ul>", format_html_join("", "<li>{}</li>", ((a,) for a in avisos)))


class LineaToRInline(admin.TabularInline):
    model = m.LineaToR
    extra = 0
    fields = ["orden", "codigo", "titulo", "ects", "nota", "superada", "asignatura_destino", "escala"]
    autocomplete_fields = ["asignatura_destino", "escala"]

    def _editable(self, obj):
        return obj is None or obj.estado == m.EstadoToR.BORRADOR

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


@admin.register(m.ToR)
class ToRAdmin(admin.ModelAdmin):
    """RF-14/RF-15: registro del ToR y revisión de sus notas (lo extraído por IA es borrador)."""

    list_display = ["movilidad", "fecha_recepcion", "remitente", "semestres", "parcial", "estado", "extraido_con_ia"]
    list_filter = ["estado", "semestres", "remitente", "parcial", "movilidad__convocatoria"]
    search_fields = ["movilidad__estudiante__nombre_completo", "movilidad__institucion__nombre"]
    autocomplete_fields = ["movilidad", "escala"]
    readonly_fields = ["estado", "extraido_con_ia", "creado"]
    inlines = [LineaToRInline]
    actions = ["extraer_con_ia", "emparejar", "aceptar", "devolver_a_borrador", "generar_acta"]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.estado == m.EstadoToR.ACEPTADO:
            return [f.name for f in m.ToR._meta.fields if f.name != "id"]
        return self.readonly_fields

    @admin.action(description="Extraer las notas del PDF con IA (queda en borrador para revisar)")
    def extraer_con_ia(self, request, queryset):
        for tor in queryset:
            try:
                n = extraccion_ia.extraer_tor(tor)
                sin_pareja = proponer_emparejamiento(tor)
            except (extraccion_ia.IANoDisponible, ValueError) as e:
                self.message_user(request, f"{tor}: {e}", messages.ERROR)
                continue
            self.message_user(request, f"{tor}: {n} materias extraídas; {len(sin_pareja)} sin emparejar con el LA. "
                                       "Revísalas y acepta el ToR.")

    @admin.action(description="Proponer emparejamiento con el LA vigente")
    def emparejar(self, request, queryset):
        for tor in queryset.filter(estado=m.EstadoToR.BORRADOR):
            sin_pareja = proponer_emparejamiento(tor)
            nombres = ", ".join(l.titulo for l in sin_pareja) or "ninguna"
            self.message_user(request, f"{tor}: sin pareja en el LA: {nombres}.")

    @admin.action(description="Aceptar (notas y emparejamiento revisados)")
    def aceptar(self, request, queryset):
        for tor in queryset.filter(estado=m.EstadoToR.BORRADOR):
            tor.aceptar(usuario=request.user)
        self.message_user(request, "ToR aceptado(s): ya se pueden usar en el acta.")

    @admin.action(description="Devolver a borrador para corregirlo")
    def devolver_a_borrador(self, request, queryset):
        for tor in queryset.filter(estado=m.EstadoToR.ACEPTADO):
            if tor.usado_en_acta_firmada():
                self.message_user(request, f"{tor}: está en un acta firmada; corrige el acta con una versión nueva.",
                                  messages.ERROR)
                continue
            tor.estado = m.EstadoToR.BORRADOR
            tor.save(update_fields=["estado"])

    @admin.action(description="Generar el borrador del acta de su movilidad")
    def generar_acta(self, request, queryset):
        for movilidad in {t.movilidad for t in queryset}:
            try:
                acta = generar_borrador(movilidad, usuario=request.user)
            except ValueError as e:
                self.message_user(request, f"{movilidad}: {e}", messages.ERROR)
                continue
            self.message_user(request, format_html('<a href="{}">{}</a> generada con {} aviso(s).',
                              reverse("admin:movilidad_acta_change", args=[acta.pk]), acta, len(acta.avisos)))


class FilaActaInline(admin.TabularInline):
    """Las tablas C y D solo se editan mientras el acta es un borrador."""

    extra = 0

    def _editable(self, obj):
        return obj is None or obj.editable

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


class FilaCInline(FilaActaInline):
    model = m.FilaActaC
    fields = ["orden", "codigo", "titulo", "superada", "ects", "nota_original"]


class FilaDInline(FilaActaInline):
    model = m.FilaActaD
    fields = ["orden", "codigo", "titulo", "ects", "nota", "detalle"]
    readonly_fields = ["detalle"]


class MotivoCorreccionForm(forms.Form):
    motivo = forms.CharField(label="Motivo de la corrección", widget=forms.Textarea(attrs={"rows": 3}))


@admin.register(m.Acta)
class ActaAdmin(admin.ModelAdmin):
    """RF-17…RF-20. Se crea desde la movilidad o el ToR («Generar el borrador del acta»)."""

    list_display = ["movilidad", "numero", "estado", "corrige", "fecha_firma", "fecha_envio"]
    list_filter = ["estado", "movilidad__convocatoria"]
    search_fields = ["movilidad__estudiante__nombre_completo"]
    fields = ["movilidad", "numero", "estado", "corrige", "motivo_correccion", "tors", "avisos_html", "docx", "pdf",
              "pdf_firmado", "fecha_firma", "fecha_envio"]
    inlines = [FilaCInline, FilaDInline]
    actions = ["generar_docx", "marcar_firmada", "enviar", "corregir"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return (obj is None or obj.editable) and super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fijos = ["movilidad", "numero", "estado", "corrige", "tors", "avisos_html", "docx", "pdf", "fecha_envio"]
        if obj and not obj.editable:
            return fijos + ["motivo_correccion", "pdf_firmado", "fecha_firma"]
        return fijos

    @admin.display(description="Avisos")
    def avisos_html(self, obj):
        return _html_avisos(obj.avisos if obj else [])

    @admin.action(description="Generar el docx del borrador")
    def generar_docx(self, request, queryset):
        for acta in queryset.filter(estado=m.EstadoActa.BORRADOR):
            try:
                avisos = generar_documentos(acta)
            except (RecursosNoDisponibles, ValueError) as e:
                self.message_user(request, f"{acta}: {e}", messages.ERROR)
                continue
            self.message_user(request, f"{acta}: documento generado. " + " ".join(avisos))

    @admin.action(description="Marcar como firmada (con el PDF firmado ya subido)")
    def marcar_firmada(self, request, queryset):
        for acta in queryset.filter(estado=m.EstadoActa.BORRADOR):
            if not acta.pdf_firmado or not acta.fecha_firma:
                self.message_user(request, f"{acta}: sube el PDF firmado e indica la fecha de firma.", messages.ERROR)
                continue
            acta.firmar(acta.pdf_firmado, acta.fecha_firma, usuario=request.user)
            self.message_user(request, f"{acta}: firmada.")

    @admin.action(description="Enviar «ATM de …» a Administración (copia a la ORI)")
    def enviar(self, request, queryset):
        for acta in queryset:
            try:
                c = enviar_atm(acta, usuario=request.user)
            except EnvioImposible as e:
                self.message_user(request, f"{acta}: {e}", messages.ERROR)
                continue
            nivel = messages.SUCCESS if c.enviada else messages.ERROR
            self.message_user(request, f"{acta}: {'enviada' if c.enviada else 'no enviada'} a {c.para}. {c.error}", nivel)

    @admin.action(description="Crear una corrección (nueva versión) del acta firmada")
    def corregir(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Selecciona una sola acta.", messages.ERROR)
            return None
        acta = queryset.get()
        form = MotivoCorreccionForm(request.POST if "motivo" in request.POST else None)
        if form.is_valid():
            try:
                nueva = crear_correccion(acta, form.cleaned_data["motivo"], usuario=request.user)
            except ValueError as e:
                self.message_user(request, str(e), messages.ERROR)
                return None
            return redirect(reverse("admin:movilidad_acta_change", args=[nueva.pk]))
        return TemplateResponse(request, "admin/movilidad/acta/corregir.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "acta": acta, "form": form,
            "title": f"Corregir {acta}",
        })


admin.site.register(m.Convocatoria)
admin.site.register(m.Titulacion)
admin.site.site_header = "FIC · Movilidad"
