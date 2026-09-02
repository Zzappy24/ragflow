"""
Sessions opaques du panel admin — le modèle GitHub/Slack pour une app
web first-party : un token ALÉATOIRE (aucun secret ne peut le forger),
dont seul le HASH est stocké en base (une lecture de la table ne donne
aucun token utilisable). La row est la session : la supprimer révoque
instantanément, zéro fenêtre résiduelle. Expiration absolue 24 h
(ADMIN_SESSION_TTL_MINUTES). Remplace le couple JWT access/refresh
(2026-09-02) : moins de code, révocation immédiate, pas de secret de
signature à protéger pour l'authentification.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from management.server.config import settings


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def open_session(user_id: str, request=None) -> str:
    """Crée la session et retourne le token en clair (jamais stocké)."""
    from common.time_utils import current_timestamp
    from api.db.db_models import DB, AdminSession

    token = secrets.token_urlsafe(48)
    ttl = getattr(settings, "ADMIN_SESSION_TTL_MINUTES", 60 * 24)
    with DB.connection_context():
        AdminSession.create(
            id=_hash(token), user_id=user_id,
            expires_at=datetime.now() + timedelta(minutes=ttl),
            ip=(request.client.host if request is not None and request.client else None),
            user_agent=(request.headers.get("user-agent", "")[:255] if request is not None else None),
            create_time=current_timestamp(), update_time=current_timestamp(),
        )
    return token


def resolve_session(token: str) -> str | None:
    """user_id d'une session vivante, sinon None."""
    if not token or len(token) < 32:
        return None
    from api.db.db_models import DB, AdminSession
    with DB.connection_context():
        row = AdminSession.get_or_none(AdminSession.id == _hash(token))
    if row is None:
        return None
    if row.expires_at and row.expires_at < datetime.now():
        return None
    return row.user_id


def close_session(token: str) -> None:
    from api.db.db_models import DB, AdminSession
    with DB.connection_context():
        row = AdminSession.get_or_none(AdminSession.id == _hash(token))
        if row:
            row.delete_instance()


def revoke_all_for_user(user_id: str) -> int:
    """Tue toutes les sessions d'un compte (offboarding, incident)."""
    from api.db.db_models import DB, AdminSession
    with DB.connection_context():
        return AdminSession.delete().where(AdminSession.user_id == user_id).execute()
