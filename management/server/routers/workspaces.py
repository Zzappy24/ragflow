"""
Workspace CRUD routes.
Org admins can create/delete workspaces within their org.
"""
import os

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
        "description": getattr(ws, "description", "") or "",
        "status": ws.status,
        "bu": (getattr(ws, "settings_json", None) or {}).get("bu", "") or "",
        "is_model_template": bool((getattr(ws, "settings_json", None) or {}).get("model_template")),
        "created_by": ws.created_by,
        "create_time": getattr(ws, "create_time", None),
    }


def _set_model_template(ws_id: str, on: bool) -> None:
    """Pose/retire le flag settings_json.model_template. Exclusif quand on=True
    (un seul workspace-référence à la fois sur la plateforme)."""
    from api.db.db_models import DB, Workspace
    from api.db.services.workspace_service import WorkspaceService
    with DB.connection_context():
        if on:
            # retire le flag partout ailleurs
            for w in Workspace.select(Workspace.id, Workspace.settings_json).where(
                    Workspace.status == "1"):
                sj = dict(w.settings_json or {})
                if sj.pop("model_template", None) is not None and w.id != ws_id:
                    Workspace.update(settings_json=sj).where(Workspace.id == w.id).execute()
        ok, ws = WorkspaceService.get_by_id(ws_id)
        sj = dict((ws.settings_json or {}) if ok and ws else {})
        if on:
            sj["model_template"] = True
        else:
            sj.pop("model_template", None)
        Workspace.update(settings_json=sj).where(Workspace.id == ws_id).execute()


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


@router.post("/workspaces/{ws_id}/model-template")
def set_workspace_model_template(ws_id: str, user=Depends(require_superuser)):
    """Marque ce workspace comme référence de modèles : tout nouveau workspace
    héritera de ses modèles (embedding, reranking, LLM) au lieu de repartir
    vide. Exclusif — désactive le flag sur les autres. Superadmin uniquement
    (réglage plateforme)."""
    from api.db.services.workspace_service import WorkspaceService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws or ws.status != "1":
        raise HTTPException(status_code=404, detail="Workspace not found")
    _set_model_template(ws_id, True)
    return {"workspace_id": ws_id, "is_model_template": True}


@router.delete("/workspaces/{ws_id}/model-template")
def unset_workspace_model_template(ws_id: str, user=Depends(require_superuser)):
    """Retire le statut de référence de modèles. Superadmin uniquement."""
    from api.db.services.workspace_service import WorkspaceService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    _set_model_template(ws_id, False)
    return {"workspace_id": ws_id, "is_model_template": False}


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
    if not ok or not ws or ws.org_id != org_id or ws.status != "1":
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _ws_to_response(ws)


@router.put("/orgs/{org_id}/workspaces/{ws_id}", response_model=WsResponse)
def update_workspace(org_id: str, ws_id: str, body: WsUpdate, user_id: str = Depends(get_current_user_id)):
    """Update workspace settings."""
    require_org_admin(org_id, user_id)
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws or ws.org_id != org_id or ws.status != "1":
        raise HTTPException(status_code=404, detail="Workspace not found")

    update_data = body.model_dump(exclude_none=True)
    # `bu` est un tag DANS settings_json : on merge au lieu d'écraser le dict
    # (et on ne laisse pas un settings_json explicite le clobber en silence).
    if "bu" in update_data:
        bu = update_data.pop("bu").strip()
        merged = dict(ws.settings_json or {})
        if bu:
            merged["bu"] = bu
        else:
            merged.pop("bu", None)  # "" = retirer le tag
        if "settings_json" in update_data:
            update_data["settings_json"] = {**merged, **update_data["settings_json"]}
        else:
            update_data["settings_json"] = merged
    # CUSTOM B2B SaaS — `model_template` désigne LE workspace dont tous les
    # nouveaux workspaces (toutes orgs) copient les modèles, clés API et
    # api_base compris. Il ne se pose que par la route superuser
    # /model-template : un org_admin pouvait le poser ici via settings_json et
    # détourner les modèles de toute la plateforme vers son endpoint (audit
    # 2026-09-06, F2). On retire la clé du payload et on préserve le drapeau
    # existant, qu'un settings_json partiel aurait sinon écrasé.
    if "settings_json" in update_data:
        incoming = dict(update_data["settings_json"] or {})
        incoming.pop("model_template", None)
        if (ws.settings_json or {}).get("model_template") is True:
            incoming["model_template"] = True
        update_data["settings_json"] = incoming
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
    if not ok or not ws or ws.org_id != org_id or ws.status != "1":
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
    request: Request,
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

    # CUSTOM B2B SaaS — une purge est l'acte le plus lourd du panel : tracé
    # comme ORG_PURGE (avant, seule la purge d'organisation était auditée).
    audit_svc.record(
        request=request,
        actor_user_id=user.id,
        action=audit_svc.WS_PURGE,
        org_id=org_id,
        workspace_id=ws_id,
        resource_type="workspace",
        resource_id=ws_id,
        details={"target_display_name": ws.name, "name": ws.name, "tenant_id": ws.tenant_id},
    )


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
async def launch_workspace(ws_id: str, user_id: str = Depends(get_current_user_id)):
    from management.server.auth.dependencies import require_ws_member
    from management.server.config import settings
    import httpx

    require_ws_member(ws_id, user_id)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                # Path migrated in upstream RESTful API refactor: old /v1/user/internal/bridge/prepare
                # → new /api/v1/internal/bridge/prepare (registered in api/apps/restful_apis/user_api.py).
                f"{settings.RAGFLOW_API_URL}/api/v1/internal/bridge/prepare",
                json={"user_id": user_id, "ws_id": ws_id},
                # CUSTOM B2B SaaS — la route vérifie le secret partagé depuis le
                # 2026-09-06 (avant : usurpation ouverte à qui connaît un user_id).
                headers={"X-Internal-Secret": os.environ.get("INTERNAL_API_SECRET", "")},
                timeout=10,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise HTTPException(status_code=502, detail=data.get("message", "Bridge prepare failed"))
        code = data["data"]["code"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RAGFlow bridge prepare failed: {e}")

    return {
        "bridge_url": f"{settings.RAGFLOW_BASE_URL}/?bridge_code={code}",
        "expires_in": 30,
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

    # CUSTOM B2B SaaS — import Peewee direct au lieu de KnowledgebaseService.query.
    # Le service upstream cascade vers api_utils → mcp/openai/etc. dans son module,
    # alors qu'on n'a besoin ici que d'un simple COUNT sur la table Knowledgebase.
    # Suivre le même pattern que UserService/TenantService: import direct des modèles
    # depuis api.db.db_models (juste peewee, aucune dep lourde).
    from api.db.db_models import Knowledgebase
    datasets_count = Knowledgebase.select().where(
        Knowledgebase.tenant_id == ws.tenant_id,
        Knowledgebase.status == "1",
    ).count()

    return {
        "workspace_id": ws_id,
        "tenant_id": ws.tenant_id,
        "members_count": len(members),
        "datasets_count": datasets_count,
    }
