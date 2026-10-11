"""Inicio de sesión con la cuenta UDC (Microsoft Entra ID) sobre mozilla-django-oidc.

Activo solo con ENTRA_TENANT_ID, ENTRA_CLIENT_ID y ENTRA_CLIENT_SECRET (ver config/settings.py).

- El id_token se verifica (firma RS256 con las claves del inquilino, caducidad y nonce en la
  librería) y además aquí: destinatario (aud = nuestra aplicación), emisor e inquilino de la UDC.
- Los datos del usuario salen del id_token verificado; no se consulta Microsoft Graph.
- Solo cuentas de los dominios de ENTRA_DOMINIOS. El usuario se busca por correo; si no existe, no
  entra (salvo ENTRA_CREAR_USUARIOS, que lo crea sin grupos ni permisos). Los permisos siguen
  siendo los de los grupos «Coordinación» y «Administración».
"""

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


def correo(claims) -> str:
    """En Entra ID el correo puede venir en «email» o solo en «preferred_username» (UPN)."""
    return (claims.get("email") or claims.get("preferred_username") or "").strip().lower()


def comprobar_token(payload):
    """Que el id_token sea para esta aplicación y de este inquilino (la librería no lo comprueba)."""
    emisor = f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/v2.0"
    if payload.get("aud") != settings.ENTRA_CLIENT_ID:
        raise SuspiciousOperation("El token de Entra ID no es para esta aplicación.")
    if payload.get("tid") != settings.ENTRA_TENANT_ID or payload.get("iss") != emisor:
        raise SuspiciousOperation("El token de Entra ID no es del inquilino de la UDC.")


class BackendEntraID(OIDCAuthenticationBackend):
    def verify_token(self, token, **kwargs):
        payload = super().verify_token(token, **kwargs)
        comprobar_token(payload)
        return payload

    def get_userinfo(self, access_token, id_token, payload):
        return {**payload, "email": correo(payload)}

    def verify_claims(self, claims):
        email = correo(claims)
        return "@" in email and email.rsplit("@", 1)[1] in settings.ENTRA_DOMINIOS

    def filter_users_by_claims(self, claims):
        return self.UserModel.objects.filter(email__iexact=correo(claims), is_active=True)

    def get_username(self, claims):
        return correo(claims)[:150]

    def create_user(self, claims):
        """Sin grupos ni permisos: el coordinador le asigna el rol."""
        user = self.UserModel.objects.create_user(self.get_username(claims), email=correo(claims))
        return self.update_user(user, claims)

    def update_user(self, user, claims):
        nombre, apellidos = claims.get("given_name", ""), claims.get("family_name", "")
        if (nombre or apellidos) and (user.first_name, user.last_name) != (nombre[:150], apellidos[:150]):
            user.first_name, user.last_name = nombre[:150], apellidos[:150]
            user.save(update_fields=["first_name", "last_name"])
        return user
