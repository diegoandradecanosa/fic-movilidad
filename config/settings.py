"""Settings de fic-movilidad.

La configuración sensible se lee de variables de entorno. En desarrollo se usa SQLite;
en producción, DATABASE_URL de PostgreSQL (nube externa, datos en la UE).
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insegura-no-usar-en-produccion")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

# Clave para el HMAC del DNI. Debe ser estable: si cambia, las importaciones nuevas no
# emparejan con los estudiantes ya guardados.
DNI_HMAC_KEY = os.environ.get("DNI_HMAC_KEY", "dev-dni-key")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "movilidad",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


def _database_from_env():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
    # postgres://usuario:clave@host:puerto/nombre
    from urllib.parse import urlparse

    u = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": u.path.lstrip("/"),
        "USER": u.username,
        "PASSWORD": u.password,
        "HOST": u.hostname,
        "PORT": u.port or 5432,
    }


DATABASES = {"default": _database_from_env()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# PDF de LA (y más adelante ToR y actas): datos de alumnos, fuera del repo (privado/ está en
# .gitignore) y servidos solo a usuarios staff (ver config/urls.py).
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", BASE_DIR / "privado" / "media"))
MEDIA_URL = "/privado/media/"

# Correo. En desarrollo se imprime en la consola; en producción, SMTP (más adelante Microsoft 365).
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
# Buzón del vicedecanato (remitente y copia) y buzón de Administración del centro. Sin valor por
# defecto: hasta que se configuren, los envíos quedan como tarea pendiente.
CORREO_COORDINADOR = os.environ.get("CORREO_COORDINADOR", "")
CORREO_ADMINISTRACION = os.environ.get("CORREO_ADMINISTRACION", "")
DEFAULT_FROM_EMAIL = CORREO_COORDINADOR or "webmaster@localhost"
# Al firmar un During o una ampliación, enviar el LA a Administración con copia al coordinador y al alumno.
ENVIO_AUTOMATICO_LA = os.environ.get("ENVIO_AUTOMATICO_LA", "1") == "1"
# Copia del «ATM de …» a la ORI.
CORREO_ORI = os.environ.get("CORREO_ORI", "")

# Acta de reconocimiento: recursos de la skill acta-reconocimiento (plantilla docx, cabecera, logo y
# JSON de equivalencias), fuera del repositorio. Misma variable que usa la skill.
RECURSOS_ACTA_DIR = os.environ.get("RECURSOS_ACTA_DIR", str(BASE_DIR / "privado" / "recursos_acta"))
ACTA_FIRMANTE = os.environ.get("ACTA_FIRMANTE", "")
ACTA_CARGO_FIRMANTE = os.environ.get("ACTA_CARGO_FIRMANTE", "Coordinador de mobilidade da Facultade de Informática")

# Extracción de ToR con la API de Claude: desactivada si no hay clave.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
