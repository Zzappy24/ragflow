"""
CUSTOM B2B SaaS — jetons de téléchargement direct (2026-09-05).

Le front devait envoyer l'en-tête Authorization, donc téléchargeait via
``fetch`` → blob entier en RAM de l'onglet → aucun retour visuel avant la
fin (200 Mo = minutes de silence). Avec un jeton signé à courte durée,
le front ouvre un lien normal : barre de téléchargement native du
navigateur (progression, vitesse, annulation), zéro RAM côté onglet.

Le jeton est émis par une route AUTHENTIFIÉE qui a déjà fait tous les
contrôles (existence, workspace, RBAC) et porte directement l'adresse
physique du blob : la route publique qui le consomme ne touche que le
stockage. Durée de vie courte (``DOWNLOAD_TOKEN_TTL_S``, défaut 60 s) :
le jeton transite dans l'URL, donc dans les access logs.
"""
import os

from itsdangerous import BadSignature, SignatureExpired
from itsdangerous.url_safe import URLSafeTimedSerializer

DOWNLOAD_TOKEN_TTL_S = int(os.environ.get("DOWNLOAD_TOKEN_TTL_S", "60"))
_SALT = "ragflow-direct-download"


def _serializer(secret: str | None):
    if secret is None:
        from common import settings

        secret = settings.get_secret_key()
    return URLSafeTimedSerializer(secret_key=secret, salt=_SALT)


def issue_download_token(bucket: str, name: str, filename: str, mimetype: str, *, secret: str | None = None) -> str:
    return _serializer(secret).dumps({"b": bucket, "n": name, "f": filename, "m": mimetype})


def verify_download_token(token: str, *, max_age: int | None = None, secret: str | None = None) -> dict | None:
    """Renvoie le payload {bucket, name, filename, mimetype} ou None (invalide/expiré)."""
    try:
        data = _serializer(secret).loads(token, max_age=DOWNLOAD_TOKEN_TTL_S if max_age is None else max_age)
    except (SignatureExpired, BadSignature, Exception):
        return None
    if not isinstance(data, dict) or not all(k in data for k in ("b", "n", "f", "m")):
        return None
    return {"bucket": data["b"], "name": data["n"], "filename": data["f"], "mimetype": data["m"]}
