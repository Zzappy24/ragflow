"""
JWT token creation and verification.
"""
from datetime import datetime, timedelta, timezone

import jwt

from management.server.config import settings


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str, jti: str) -> str:
    """Refresh token STATEFUL : le jti doit correspondre à une row
    admin_session — le JWT seul ne suffit pas (révocation serveur)."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_REFRESH_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire, "type": "refresh", "jti": jti}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_refresh(token: str) -> tuple[str, str] | None:
    """(user_id, jti) d'un refresh token valide, sinon None. Un token sans
    jti (émis avant le passage stateful) est rejeté : re-login unique."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != "refresh" or not payload.get("jti") or not payload.get("sub"):
        return None
    return payload["sub"], payload["jti"]


def decode_token(token: str, expected_type: str = "access") -> str | None:
    """Decode a JWT token and return user_id, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != expected_type:
            return None
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
