from django.apps import AppConfig


class MovilidadConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "movilidad"

    def ready(self):
        from movilidad import signals  # noqa: F401
