"""
Audit helper for the admin panel.

Provides a thin wrapper around AuditService.record() that:
- Extracts ip_address and user_agent from the FastAPI Request object
- Resolves actor_email from user_id at call time
- Is always best-effort (never raises, never blocks)

Action constants are defined here so all callers use the same strings.
"""
from fastapi import Request

# ---------------------------------------------------------------------------
# Action constants
# ---------------------------------------------------------------------------

# Organisations
ORG_ARCHIVE = "ORG_ARCHIVE"
ORG_RESTORE = "ORG_RESTORE"
ORG_PURGE = "ORG_PURGE"

# Workspaces
WS_CREATE = "WS_CREATE"
WS_ARCHIVE = "WS_ARCHIVE"
WS_RESTORE = "WS_RESTORE"
WS_PURGE = "WS_PURGE"

# Users
USER_INVITE = "USER_INVITE"
USER_DELETE = "USER_DELETE"
USER_RESTORE = "USER_RESTORE"
USER_PURGE = "USER_PURGE"

# Org members
ORG_MEMBER_ADD = "ORG_MEMBER_ADD"
ORG_MEMBER_REMOVE = "ORG_MEMBER_REMOVE"
ORG_MEMBER_ROLE_CHANGE = "ORG_MEMBER_ROLE_CHANGE"

# Workspace members
WS_MEMBER_ADD = "WS_MEMBER_ADD"
WS_MEMBER_REMOVE = "WS_MEMBER_REMOVE"
WS_MEMBER_ROLE_CHANGE = "WS_MEMBER_ROLE_CHANGE"

# Code product
# NOTE: aligned to the SCREAMING_SNAKE_CASE convention of the constants above
# (was dot-style "code.entitlement.set" — no prod data existed yet, safe to rename).
CODE_ENTITLEMENT_SET = "CODE_ENTITLEMENT_SET"
CODE_TEAM_CREATE = "CODE_TEAM_CREATE"
CODE_TEAM_UPDATE = "CODE_TEAM_UPDATE"
CODE_TEAM_ADMIN_ADD = "CODE_TEAM_ADMIN_ADD"
CODE_TEAM_ADMIN_REMOVE = "CODE_TEAM_ADMIN_REMOVE"
CODE_TEAM_DELETE = "CODE_TEAM_DELETE"
CODE_KEY_CREATE = "CODE_KEY_CREATE"
CODE_KEY_REVOKE = "CODE_KEY_REVOKE"
CODE_SEAT_INVITE = "CODE_SEAT_INVITE"
CODE_SEAT_CLAIMED = "CODE_SEAT_CLAIMED"
CODE_KEY_ROTATED = "CODE_KEY_ROTATED"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_actor_email(actor_user_id: str) -> str:
    try:
        from api.db.services.user_service import UserService
        ok, user = UserService.get_by_id(actor_user_id)
        if ok and user:
            return user.email or ""
    except Exception:
        pass
    return ""


def record(
    *,
    request: Request,
    actor_user_id: str,
    action: str,
    org_id: str | None = None,
    workspace_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
    diff: dict | None = None,
    status: str = "success",
) -> None:
    """Fire-and-forget audit record. Never raises."""
    try:
        from api.db.services.audit_service import AuditService
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")
        actor_email = _get_actor_email(actor_user_id)
        AuditService.record(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=actor_user_id,
            actor_email=actor_email,
            action=action,
            status=status,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            diff=diff,
            ip_address=ip,
            user_agent=ua,
        )
    except Exception:
        pass
