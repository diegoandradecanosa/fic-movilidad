from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required, permission_required
from django.urls import include, path
from django.views.static import serve

# Documentos de alumnos: solo Coordinación y Administración (permiso de lectura de expedientes).
# En producción se sustituirá por el almacenamiento de la nube.
@login_required
@permission_required("movilidad.view_movilidad", raise_exception=True)
def documentos(request, path):
    return serve(request, path, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("cuentas/", include("django.contrib.auth.urls")),
    path(f"{settings.MEDIA_URL.strip('/')}/<path:path>", documentos),
    path("", include("movilidad.urls")),
]
