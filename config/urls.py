from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import LoginView
from django.urls import include, path
from django.views.generic import RedirectView
from django.views.static import serve

# Documentos de alumnos: solo Coordinación y Administración (permiso de lectura de expedientes).
# En producción se sustituirá por el almacenamiento de la nube.
@login_required
@permission_required("movilidad.view_movilidad", raise_exception=True)
def documentos(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


class Entrar(LoginView):
    """Usuario y contraseña de Django y, si está configurada, la cuenta UDC (Entra ID)."""

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "entra_activo": settings.ENTRA_ACTIVO,
                "error_entra": self.request.GET.get("error") == "entra"}


urlpatterns = [
    path("cuentas/login/", Entrar.as_view(), name="login"),
]
if settings.ENTRA_ACTIVO:
    urlpatterns += [
        path("oidc/", include("mozilla_django_oidc.urls")),
        # El admin entra por la misma página (con el botón de la cuenta UDC).
        path("admin/login/", RedirectView.as_view(url="/cuentas/login/", query_string=True)),
    ]
urlpatterns += [
    path("admin/", admin.site.urls),
    path("cuentas/", include("django.contrib.auth.urls")),
    path(f"{settings.MEDIA_URL.strip('/')}/<path:path>", documentos),
    path("", include("movilidad.urls")),
]
