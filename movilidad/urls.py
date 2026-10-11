from django.urls import path

from movilidad import views

urlpatterns = [
    path("", views.panel, name="panel"),
    path("expediente/<int:pk>/", views.expediente, name="expediente"),
    path("expediente/<int:pk>/anotar/", views.anotar, name="anotar"),
    path("anotacion/<int:pk>/<str:accion>/", views.accion_anotacion, name="accion_anotacion"),
]
