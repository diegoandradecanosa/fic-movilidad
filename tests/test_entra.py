"""Inicio de sesión con la cuenta UDC (Entra ID): verificación del token y emparejamiento de usuarios."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import Group, User
from django.core.exceptions import SuspiciousOperation

from movilidad.entra import BackendEntraID, comprobar_token

pytestmark = pytest.mark.django_db
INQUILINO = "11111111-2222-3333-4444-555555555555"
APLICACION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def entra(settings):
    settings.ENTRA_TENANT_ID, settings.ENTRA_CLIENT_ID = INQUILINO, APLICACION
    settings.OIDC_RP_CLIENT_ID, settings.OIDC_RP_CLIENT_SECRET = APLICACION, "secreto"
    settings.ENTRA_DOMINIOS = ["udc.es", "udc.gal"]
    settings.OIDC_CREATE_USER = False
    return settings


@pytest.fixture
def clave():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def id_token(clave, **cambios):
    claims = {"aud": APLICACION, "tid": INQUILINO, "iss": f"https://login.microsoftonline.com/{INQUILINO}/v2.0",
              "nonce": "n1", "exp": int(time.time()) + 600, "iat": int(time.time()),
              "preferred_username": "Persoa.Proba@udc.es", "given_name": "Persoa", "family_name": "Proba"}
    claims.update(cambios)
    return jwt.encode(claims, clave, algorithm="RS256")


def backend(clave, monkeypatch):
    b = BackendEntraID()
    monkeypatch.setattr(b, "retrieve_matching_jwk", lambda token: clave.public_key())  # JWKS del inquilino
    return b


def test_token_valido_y_usuario_existente(entra, clave, monkeypatch):
    usuario = User.objects.create_user("persoa", email="persoa.proba@UDC.es")
    b = backend(clave, monkeypatch)
    payload = b.verify_token(id_token(clave), nonce="n1")
    assert b.get_or_create_user("acceso", "id", payload) == usuario  # correo sin distinguir mayúsculas
    usuario.refresh_from_db()
    assert (usuario.first_name, usuario.last_name) == ("Persoa", "Proba")


@pytest.mark.parametrize("cambios, error", [
    ({"aud": "otra-aplicacion"}, "no es para esta aplicación"),
    ({"tid": "otro-inquilino"}, "inquilino"),
    ({"iss": "https://login.microsoftonline.com/common/v2.0"}, "inquilino"),
])
def test_token_de_otra_aplicacion_o_inquilino(entra, clave, monkeypatch, cambios, error):
    with pytest.raises(SuspiciousOperation, match=error):
        backend(clave, monkeypatch).verify_token(id_token(clave, **cambios), nonce="n1")


def test_firma_caducidad_y_nonce_los_comprueba_la_libreria(entra, clave, monkeypatch):
    b = backend(clave, monkeypatch)
    otra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(SuspiciousOperation):
        b.verify_token(id_token(otra), nonce="n1")
    with pytest.raises(jwt.ExpiredSignatureError):
        b.verify_token(id_token(clave, exp=int(time.time()) - 60), nonce="n1")
    with pytest.raises(SuspiciousOperation, match="Nonce"):
        b.verify_token(id_token(clave), nonce="otro")


def test_solo_dominios_udc_y_usuarios_dados_de_alta(entra, clave, monkeypatch):
    b = backend(clave, monkeypatch)
    with pytest.raises(SuspiciousOperation, match="Claims"):
        b.get_or_create_user("a", "i", {"preferred_username": "alguien@gmail.com"})
    # sin usuario con ese correo: no entra (por defecto no se crean)
    assert b.get_or_create_user("a", "i", {"email": "nova@udc.gal"}) is None
    User.objects.create_user("baixa", email="baixa@udc.es", is_active=False)
    assert b.get_or_create_user("a", "i", {"email": "baixa@udc.es"}) is None


def test_crear_usuarios_sin_permisos(entra, clave, monkeypatch):
    entra.OIDC_CREATE_USER = True
    usuario = backend(clave, monkeypatch).get_or_create_user(
        "a", "i", {"preferred_username": "Nova@udc.gal", "given_name": "Nova", "family_name": "Persoa"})
    assert usuario.username == usuario.email == "nova@udc.gal"
    assert not usuario.is_staff and not usuario.groups.exists() and not usuario.has_perm("movilidad.view_movilidad")
    assert Group.objects.filter(name="Coordinación").exists()  # los roles se asignan a mano


def test_comprobar_token_directo(entra):
    comprobar_token({"aud": APLICACION, "tid": INQUILINO,
                     "iss": f"https://login.microsoftonline.com/{INQUILINO}/v2.0"})


def test_pagina_de_entrada(client, settings):
    settings.ENTRA_ACTIVO = False
    html = client.get("/cuentas/login/").content.decode()
    assert "Entrar con la cuenta UDC" not in html and 'name="password"' in html
    settings.ENTRA_ACTIVO = True
    html = client.get("/cuentas/login/?next=/expediente/3/&error=entra").content.decode()
    assert 'href="/oidc/authenticate/?next=/expediente/3/"' in html
    assert "pide al coordinador" in html
