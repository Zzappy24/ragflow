"""
Workspace CRUD routes.
Org admins can create/delete workspaces within their org.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import get_current_user_id, require_org_admin, require_superuser
from management.server.models.schemas import WsCreate, WsUpdate, WsResponse
from management.server.services import audit as audit_svc

router = APIRouter()


def _ws_to_response(ws) -> dict:
    return {
        "id": ws.id,
        "org_id": ws.org_id,
        "tenant_id": ws.tenant_id,
        "name": ws.name,
        "description": getattr(ws, "description", ""),
        "status": ws.status,
        "created_by": ws.created_by,
        "create_time": getattr(ws, "create_time", None),
    }


@router.get("/orgs/{org_id}/workspaces", response_model=list[WsResponse])
def list_workspaces(
    org_id: str,
    include_deleted: bool = False,
    user_id: str = Depends(get_current_user_id),
):
    """List workspaces in an org. Pass ?include_deleted=true to also see soft-deleted."""
    require_org_admin(org_id, user_id)
    from api.db.services.workspace_service import WorkspaceService
    workspaces = WorkspaceService.list_by_org(org_id, include_deleted=include_deleted)
    return [_ws_to_response(ws) for ws in workspaces]


@router.post("/orgs/{org_id}/workspaces", response_model=WsResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(request: Request, org_id: str, body: WsCreate, user_id: str = Depends(get_current_user_id)):
    """Create a workspace. Provisions a RAGFlow tenant under the hood."""
    user = require_org_admin(org_id, user_id)

    from management.server.services.provisioning import provision_workspace
    from api.db.services.quota_service import check_quota

    allowed, msg = check_quota(org_id, "workspace")
    if not allowed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=msg)

    ws = provision_workspace(org_id=org_id, name=body.name, description=body.description, created_by=user_id)

    audit_svc.record(
        request=request,
        actor_user_id=user_id,
        action=audit_svc.WS_CREATE,
        org_id=org_id,
        workspace_id=ws.id,
        resource_type="workspace",
        resource_id=ws.id,
        details={"target_display_name": body.name, "name": body.name},
    )

    return _ws_to_response(ws)


@router.get("/orgs/{org_id}/workspaces/{ws_id}", response_model=WsResponse)
def get_workspace(org_id: str, ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Get workspace details."""
    require_org_admin(org_id, user_id)
    from api.db.services.workspace_service import WorkspaceService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws or ws.org_id != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _ws_to_response(ws)


@router.put("/orgs/{org_id}/workspaces/{ws_id}", response_model=WsResponse)
def update_workspace(org_id: str, ws_id: str, body: WsUpdate, user_id: str = Depends(get_current_user_id)):
    """Update workspace settings."""
    require_org_admin(org_id, user_id)
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws or ws.org_id != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")

    update_data = body.model_dump(exclude_none=True)
    if update_data:
        WorkspaceService.update_by_id(ws_id, update_data)

    ok, ws = WorkspaceService.get_by_id(ws_id)
    return _ws_to_response(ws)


@router.delete("/orgs/{org_id}/workspaces/{ws_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(request: Request, org_id: str, ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Soft-delete a workspace + its RAGFlow tenant + technical user."""
    require_org_admin(org_id, user_id)
    from api.db.services.workspace_service import WorkspaceService
    from management.server.services.provisioning import deprovision_workspace

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws or ws.org_id != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    deprovision_workspace(ws_id)

    audit_svc.record(
        request=request,
        actor_user_id=user_id,
        action=audit_svc.WS_ARCHIVE,
        org_id=org_id,
        workspace_id=ws_id,
        resource_type="workspace",
        resource_id=ws_id,
        details={"target_display_name": ws.name, "name": ws.name},
    )


@router.post("/orgs/{org_id}/workspaces/{ws_id}/restore", response_model=WsResponse)
def restore_workspace_route(request: Request, org_id: str, ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Restore a soft-deleted workspace (org_admin or superuser)."""
    require_org_admin(org_id, user_id)
    from api.db.services.workspace_service import WorkspaceService
    from management.server.services.provisioning import restore_workspace

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws or ws.org_id != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    restore_workspace(ws_id)
    ok, ws = WorkspaceService.get_by_id(ws_id)

    audit_svc.record(
        request=request,
        actor_user_id=user_id,
        action=audit_svc.WS_RESTORE,
        org_id=org_id,
        workspace_id=ws_id,
        resource_type="workspace",
        resource_id=ws_id,
        details={"target_display_name": ws.name if ok else ws_id, "name": ws.name if ok else ws_id},
    )

    return _ws_to_response(ws)


@router.delete("/orgs/{org_id}/workspaces/{ws_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_workspace_route(
    org_id: str,
    ws_id: str,
    confirm: str = "",
    user=Depends(require_superuser),
):
    """
    HARD delete a workspace + tenant + tech user + ALL datasets/documents.

    Irreversible. Restricted to superusers. Requires ``?confirm=DELETE`` in the
    query string as an additional safeguard.
    """
    if confirm != "DELETE":
        raise HTTPException(
            status_code=400,
            detail="Purge requires confirm=DELETE query parameter",
        )
    from api.db.services.workspace_service import WorkspaceService
    from management.server.services.provisioning import purge_workspace

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws or ws.org_id != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    purge_workspace(ws_id)


@router.get("/workspaces/{ws_id}", response_model=WsResponse)
def get_workspace_direct(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Get workspace details by ws_id alone (no org in path)."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)
    from api.db.services.workspace_service import WorkspaceService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _ws_to_response(ws)


@router.post("/workspaces/{ws_id}/launch")
def launch_workspace(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Issue a single-use bridge token and return the RAGFlow launch URL.

    Any member of the workspace (or org_admin / superuser) can launch. The
    bridge token is short-lived (60s by default) and bound to ``ws_id``; the
    consumer (RAGFlow) enforces single-use via Redis SETNX on ``jti``.
    """
    from management.server.auth.dependencies import require_ws_member
    from management.server.auth.jwt import create_bridge_token
    from management.server.config import settings

    require_ws_member(ws_id, user_id)
    token = create_bridge_token(user_id, ws_id)
    return {
        "bridge_url": f"{settings.RAGFLOW_BASE_URL}/?bridge_token={token}",
        "expires_in": settings.BRIDGE_TOKEN_EXPIRE_SECONDS,
    }


@router.get("/workspaces/{ws_id}/stats")
def workspace_stats(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Get workspace resource stats."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    members = WsMemberService.list_by_workspace(ws_id)

    from api.db.services.knowledgebase_service import KnowledgebaseService
    datasets = KnowledgebaseService.query(tenant_id=ws.tenant_id, status="1")

    return {
        "workspace_id": ws_id,
        "tenant_id": ws.tenant_id,
        "members_count": len(members),
        "datasets_count": len(list(datasets)),
    }
