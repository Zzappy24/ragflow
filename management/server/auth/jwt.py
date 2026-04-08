"""
JWT token creation and verification.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from management.server.config import settings


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_REFRESH_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


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


def create_bridge_token(user_id: str, workspace_id: str) -> str:
    """
    Short-lived single-use token for the admin-panel → RAGFlow auth handoff.

    Carries a fresh ``jti`` so the consumer (RAGFlow) can enforce single-use
    via Redis SETNX. ``ws_id`` binds the token to a specific workspace; the
    consumer must re-verify membership at consumption time.
    """
    expire = datetime.now(timezone.utc) + timedelta(seconds=settings.BRIDGE_TOKEN_EXPIRE_SECONDS)
    payload = {
        "sub": user_id,
        "ws_id": workspace_id,
        "exp": expire,
        "type": "bridge",
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
