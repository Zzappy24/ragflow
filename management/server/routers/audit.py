"""
Audit log query routes.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from management.server.auth.dependencies import get_current_user_id, require_org_admin

router = APIRouter()


@router.get("/orgs/{org_id}/audit")
def org_audit_log(
    org_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user_id_filter: str | None = None,
    action: str | None = None,
    user_id: str = Depends(get_current_user_id),
):
    """Query audit logs for an organisation."""
    require_org_admin(org_id, user_id)

    from api.db.services.audit_service import AuditService
    logs = AuditService.query_by_org(
        org_id=org_id,
        page=page,
        page_size=page_size,
        user_id=user_id_filter,
        action=action,
    )
    return {
        "page": page,
        "page_size": page_size,
        "items": [_log_to_dict(log) for log in logs],
    }


@router.get("/workspaces/{ws_id}/audit")
def ws_audit_log(
    ws_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user_id_filter: str | None = None,
    action: str | None = None,
    user_id: str = Depends(get_current_user_id),
):
    """Query audit logs for a workspace."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    from api.db.services.audit_service import AuditService
    logs = AuditService.query_by_workspace(
        workspace_id=ws_id,
        page=page,
        page_size=page_size,
        user_id=user_id_filter,
        action=action,
    )
    return {
        "page": page,
        "page_size": page_size,
        "items": [_log_to_dict(log) for log in logs],
    }


def _log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "org_id": getattr(log, "org_id", None),
        "workspace_id": getattr(log, "workspace_id", None),
        "user_id": log.user_id,
        "action": log.action,
        "resource_type": getattr(log, "resource_type", None),
        "resource_id": getattr(log, "resource_id", None),
        "details": getattr(log, "details", None),
        "ip_address": getattr(log, "ip_address", None),
        "create_time": getattr(log, "create_time", None),
    }
