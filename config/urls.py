from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    # Documentos de alumnos: solo staff. En producción se sustituirá por el almacenamiento de la nube.
    path(
        f"{settings.MEDIA_URL.strip('/')}/<path:path>",
        staff_member_required(serve),
        {"document_root": settings.MEDIA_ROOT},
    ),
]
