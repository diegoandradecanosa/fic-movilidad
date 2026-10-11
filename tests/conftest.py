import pytest


@pytest.fixture(autouse=True)
def _hash_rapido(settings):
    """Hash de contraseñas rápido en los tests (el real es deliberadamente lento)."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
