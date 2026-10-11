"""Vistas propias (fuera del admin): panel de expedientes (RF-03) y ficha del expediente con
anotaciones (RF-30). Coordinación y Administración ven todo; editar LA y actas sigue en el admin
y solo para Coordinación."""

from datetime import date

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from movilidad import anotaciones as anot
from movilidad.acta import acta_vigente
from movilidad.la_vigente import la_vigente
from movilidad.models import (
    DestinatarioAnotacion,
    EstadoMovilidad,
    EstadoPlazo,
    Movilidad,
    Programa,
    Titulacion,
    VENTANAS,
)
from movilidad.panel import filas
from movilidad.roles import es_coordinacion
from movilidad.validaciones_la import validar

lector = [login_required, permission_required("movilidad.view_movilidad", raise_exception=True)]


def con(*decoradores):
    def aplicar(vista):
        for d in reversed(decoradores):
            vista = d(vista)
        return vista
    return aplicar


class FiltrosPanel(forms.Form):
    curso = forms.ChoiceField(required=False)
    q = forms.CharField(required=False, label="Alumno/a o destino")
    programa = forms.ChoiceField(required=False, choices=[("", "Todos")] + Programa.choices)
    titulacion = forms.ModelChoiceField(required=False, queryset=Titulacion.objects.all(), empty_label="Todas")
    pais = forms.ChoiceField(required=False, label="País")
    semestre = forms.ChoiceField(required=False, choices=[("", "Todos"), ("1", "1.º"), ("2", "2.º")],
                                 label="Semestre de inicio")
    estado = forms.ChoiceField(required=False, choices=[("", "Todos")] + EstadoMovilidad.choices)
    bloqueos = forms.BooleanField(required=False, label="Solo con bloqueos")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cursos = sorted(set(Movilidad.objects.values_list("convocatoria__curso", flat=True)), reverse=True)
        self.fields["curso"].choices = [("", "Todos")] + [(c, c) for c in cursos]
        paises = sorted(set(Movilidad.objects.values_list("institucion__pais", flat=True)))
        self.fields["pais"].choices = [("", "Todos")] + [(p, p) for p in paises]


@con(*lector)
def panel(request):
    hoy = timezone.localdate()
    datos = request.GET.copy()
    if "curso" not in datos:
        ultimo = Movilidad.objects.order_by("-convocatoria__curso").values_list("convocatoria__curso", flat=True).first()
        datos["curso"] = ultimo or ""
    form = FiltrosPanel(datos)
    qs = Movilidad.objects.all()
    if form.is_valid():
        f = form.cleaned_data
        if f["curso"]:
            qs = qs.filter(convocatoria__curso=f["curso"])
        if f["q"]:
            qs = qs.filter(estudiante__nombre_completo__icontains=f["q"]) | qs.filter(institucion__nombre__icontains=f["q"])
        for campo, filtro in (("programa", "programa"), ("titulacion", "estudiante__titulacion"),
                              ("pais", "institucion__pais"), ("semestre", "semestre_inicio"), ("estado", "estado")):
            if f[campo]:
                qs = qs.filter(**{filtro: f[campo]})
    else:
        messages.error(request, "Algún filtro no es válido: " + "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
        qs = qs.none()
    resultado = filas(qs.order_by("estudiante__nombre_completo"), hoy)
    if form.is_valid() and form.cleaned_data["bloqueos"]:
        resultado = [r for r in resultado if r.bloqueos]
    por_estado = {}
    for r in resultado:
        etiqueta = r.movilidad.get_estado_display()
        por_estado[etiqueta] = por_estado.get(etiqueta, 0) + 1
    return render(request, "movilidad/panel.html", {
        "form": form, "filas": resultado, "por_estado": por_estado, "hoy": hoy,
        "con_bloqueos": sum(1 for r in resultado if r.bloqueos),
        "es_coordinacion": es_coordinacion(request.user),
    })


class AnotacionForm(forms.Form):
    texto = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Anotación o petición")
    destinatario = forms.ChoiceField(choices=DestinatarioAnotacion.choices, initial=DestinatarioAnotacion.COORDINADOR)
    fecha_limite = forms.DateField(required=False, label="Plazo", widget=forms.DateInput(attrs={"type": "date"}))

    def clean_fecha_limite(self):
        f = self.cleaned_data["fecha_limite"]
        if f and f < date.today():
            raise forms.ValidationError("El plazo no puede estar en el pasado.")
        return f


@con(*lector)
def expediente(request, pk):
    m = get_object_or_404(Movilidad.objects.select_related("estudiante", "institucion", "convocatoria"), pk=pk)
    hoy = timezone.localdate()
    la = la_vigente(m)
    lista_anotaciones = list(m.anotaciones.select_related("autor", "respondida_por"))
    for a in lista_anotaciones:
        a.puede_responder = anot.puede_responder(request.user, a)
        a.puede_cerrar = anot.puede_cerrar(request.user, a)
    plazos = [p for p in m.plazos.filter(estado=EstadoPlazo.PENDIENTE)
              if not (p.tipo in VENTANAS and p.fecha_limite and p.fecha_limite < hoy)]
    return render(request, "movilidad/expediente.html", {
        "m": m, "la": la, "avisos": validar(m),
        "semestres": [{"numero": s, "grupos": [g for g in la.grupos if g.semestre == s],
                       "ects_destino": la.ects_destino(s), "ects_udc": la.ects_udc(s)} for s in la.semestres],
        "versiones": m.versiones_la.all(), "tors": m.tors.all(), "actas": m.actas.all(), "acta_vigente": acta_vigente(m),
        "plazos": plazos, "comunicaciones": m.comunicaciones.all()[:20], "eventos": m.eventos.all()[:20],
        "anotaciones": lista_anotaciones, "form": AnotacionForm(),
        "puede_anotar": request.user.has_perm("movilidad.add_anotacion"),
        "es_coordinacion": es_coordinacion(request.user),
    })


@require_POST
@con(login_required, permission_required("movilidad.add_anotacion", raise_exception=True))
def anotar(request, pk):
    m = get_object_or_404(Movilidad, pk=pk)
    form = AnotacionForm(request.POST)
    if form.is_valid():
        a = anot.crear_anotacion(m, request.user, **form.cleaned_data)
        aviso = a.comunicaciones.first()
        if aviso and not aviso.enviada:
            messages.warning(request, f"Anotación guardada, pero el aviso no se envió: {aviso.error}")
        else:
            messages.success(request, "Anotación guardada." + (" Se ha avisado al destinatario." if aviso else ""))
    else:
        messages.error(request, "Revisa la anotación: " + "; ".join(e for es in form.errors.values() for e in es))
    return redirect("expediente", pk=pk)


@require_POST
@con(login_required, permission_required("movilidad.change_anotacion", raise_exception=True))
def accion_anotacion(request, pk, accion):
    from movilidad.models import Anotacion

    a = get_object_or_404(Anotacion, pk=pk)
    if accion == "responder" and anot.puede_responder(request.user, a) and request.POST.get("respuesta", "").strip():
        anot.responder(a, request.user, request.POST["respuesta"].strip())
        messages.success(request, "Petición respondida.")
    elif accion == "cerrar" and anot.puede_cerrar(request.user, a):
        anot.cerrar(a, request.user)
        messages.success(request, "Anotación cerrada.")
    else:
        messages.error(request, "No puedes hacer eso con esta anotación.")
    return redirect("expediente", pk=a.movilidad_id)
