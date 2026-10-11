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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
