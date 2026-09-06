"""CUSTOM B2B SaaS — vérification du secret partagé des routes service-à-service.

Les routes ``/api/v1/internal/*`` sont appelées par le panel d'administration
(compte de service, pas de session utilisateur). Elles n'ont ni
``@login_required`` ni RBAC : ce secret est leur SEULE authentification
applicative, doublée depuis le 2026-09-06 d'un 404 au bord (nginx du front).

Historique : ``internal_api.py`` vérifiait le secret, mais
``/internal/bridge/prepare`` et ``/internal/invite/prepare`` (user_api.py) ne
vérifiaient RIEN — n'importe qui connaissant un user_id pouvait fabriquer un
code de connexion ou d'invitation pour ce compte (constaté 2026-09-06).
Toute route interne DOIT appeler ``check_internal_secret()`` en premier ;
pin : test/multitenant/test_internal_routes_secret.py.
"""
import hmac
import os

from quart import request


def check_internal_secret() -> tuple[bool, str]:
    """(ok, message). Comparaison en temps constant ; serveur sans secret = refus."""
    expected = os.environ.get("INTERNAL_API_SECRET", "")
    if not expected:
        return False, "INTERNAL_API_SECRET not configured on this server"
    provided = request.headers.get("X-Internal-Secret", "")
    if not provided:
        return False, "Missing X-Internal-Secret header"
    if not hmac.compare_digest(expected, provided):
        return False, "Invalid X-Internal-Secret"
    return True, ""
