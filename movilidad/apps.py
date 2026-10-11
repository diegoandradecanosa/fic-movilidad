from django.apps import AppConfig


class MovilidadConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "movilidad"

    def ready(self):
        from django.db.models.signals import post_migrate

        from movilidad import signals  # noqa: F401
        from movilidad.roles import configurar_grupos

        post_migrate.connect(configurar_grupos, sender=self)
