"""
Audit log query routes.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from management.server.auth.dependencies import get_current_user_id, require_org_admin, require_superuser

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


@router.get("/audit")
def global_audit_log(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    org_id: str | None = None,
    action: str | None = None,
    user=Depends(require_superuser),
):
    """Query all audit logs across all orgs (superuser only)."""
    from api.db.services.audit_service import AuditService
    from api.db.db_models import DB, Organisation

    logs = AuditService.query_global(
        page=page,
        page_size=page_size,
        org_id=org_id,
        action=action,
    )

    # Batch-resolve org names — LEFT JOIN equivalent in Python
    org_ids = {log.org_id for log in logs if log.org_id}
    org_names: dict[str, str] = {}
    if org_ids:
        with DB.connection_context():
            rows = list(Organisation.select(Organisation.id, Organisation.name).where(Organisation.id.in_(org_ids)))
            for o in rows:
                from management.server.routers.archives import _strip_deleted
                org_names[o.id] = _strip_deleted(o.name)

    items = []
    for log in logs:
        d = _log_to_dict(log)
        if log.org_id:
            d["org_name"] = org_names.get(log.org_id, "[Organisation Supprimée]")
        else:
            d["org_name"] = None
        items.append(d)

    return {"page": page, "page_size": page_size, "items": items}


def _log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "org_id": getattr(log, "org_id", None),
        "workspace_id": getattr(log, "workspace_id", None),
        "user_id": log.user_id,
        "actor_email": getattr(log, "actor_email", None),
        "action": log.action,
        "status": getattr(log, "status", "success"),
        "resource_type": getattr(log, "resource_type", None),
        "resource_id": getattr(log, "resource_id", None),
        "details": getattr(log, "details", None),
        "diff": getattr(log, "diff", None),
        "ip_address": getattr(log, "ip_address", None),
        "user_agent": getattr(log, "user_agent", None),
        "create_time": getattr(log, "create_time", None),
    }
