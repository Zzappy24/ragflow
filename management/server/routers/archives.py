"""
Archives — Centre de gouvernance des données (superuser only).

Liste, restaure et purge définitivement les entités soft-deletées
(Organisations, Workspaces, Users).

Soft-delete convention: champ unique suffixé ___deleted___[unix_ts], status="0".
Restore: on retire le suffixe et on repasse status="1".
Purge: hard-delete avec cascade (org → workspaces, ws → datasets/docs).
       Pour les Users: tombstone RGPD (effacement des PII, la ligne reste).
"""
import re

from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import require_superuser

router = APIRouter()

_DELETED_RE = re.compile(r"___deleted___\d+$")


def _strip_deleted(value: str) -> str:
    """Remove ___deleted___[timestamp] suffix added at soft-delete time."""
    return _DELETED_RE.sub("", value)


# ---------------------------------------------------------------------------
# LIST archived entities
# ---------------------------------------------------------------------------

@router.get("/archives/orgs")
def list_archived_orgs(user=Depends(require_superuser)):
    from api.db.db_models import DB, Organisation
    with DB.connection_context():
        rows = list(
            Organisation.select()
            .where(Organisation.status == "0")
            .order_by(Organisation.create_time.desc())
        )
    return [
        {
            "id": o.id,
            "name": _strip_deleted(o.name),
            "slug": _strip_deleted(o.slug),
            "raw_name": o.name,
            "raw_slug": o.slug,
            "created_by": o.created_by,
            "create_time": getattr(o, "create_time", None),
        }
        for o in rows
    ]


@router.get("/archives/workspaces")
def list_archived_workspaces(user=Depends(require_superuser)):
    from api.db.db_models import DB, Workspace, Organisation
    with DB.connection_context():
        rows = list(
            Workspace.select()
            .where(Workspace.status == "0")
            .order_by(Workspace.create_time.desc())
        )
        # Resolve org names in one shot
        org_ids = {w.org_id for w in rows}
        orgs = {o.id: o for o in Organisation.select().where(Organisation.id.in_(org_ids))} if org_ids else {}

    return [
        {
            "id": w.id,
            "org_id": w.org_id,
            "org_name": _strip_deleted(orgs[w.org_id].name) if w.org_id in orgs else None,
            "name": _strip_deleted(w.name),
            "raw_name": w.name,
            "tenant_id": w.tenant_id,
            "created_by": w.created_by,
            "create_time": getattr(w, "create_time", None),
        }
        for w in rows
    ]


@router.get("/archives/users")
def list_archived_users(user=Depends(require_superuser)):
    from api.db.db_models import DB, User
    with DB.connection_context():
        rows = list(
            User.select()
            .where(
                (User.status == "0") &
                # Exclude RGPD tombstones (already purged)
                ~(User.email.startswith("purged_"))
            )
            .order_by(User.create_time.desc())
        )
    return [
        {
            "id": u.id,
            "email": _strip_deleted(u.email),
            "raw_email": u.email,
            "nickname": u.nickname,
            "is_superuser": u.is_superuser,
            "create_time": getattr(u, "create_time", None),
        }
        for u in rows
    ]


# ---------------------------------------------------------------------------
# RESTORE
# ---------------------------------------------------------------------------

@router.post("/archives/orgs/{org_id}/restore")
def restore_org(org_id: str, user=Depends(require_superuser)):
    from api.db.db_models import DB, Organisation
    with DB.connection_context():
        org = Organisation.get_or_none(
            (Organisation.id == org_id) & (Organisation.status == "0")
        )
        if not org:
            raise HTTPException(status_code=404, detail="Archived organisation not found")

        clean_name = _strip_deleted(org.name)
        clean_slug = _strip_deleted(org.slug)

        # Check uniqueness before restoring
        conflict = Organisation.get_or_none(
            (Organisation.slug == clean_slug) & (Organisation.status == "1")
        )
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"Slug '{clean_slug}' is already taken by an active organisation",
            )

        Organisation.update({
            Organisation.name: clean_name,
            Organisation.slug: clean_slug,
            Organisation.status: "1",
        }).where(Organisation.id == org_id).execute()

    return {"id": org_id, "name": clean_name, "slug": clean_slug}


@router.post("/archives/workspaces/{ws_id}/restore")
def restore_workspace(ws_id: str, user=Depends(require_superuser)):
    from api.db.db_models import DB, Workspace, Tenant, User
    with DB.connection_context():
        ws = Workspace.get_or_none(
            (Workspace.id == ws_id) & (Workspace.status == "0")
        )
        if not ws:
            raise HTTPException(status_code=404, detail="Archived workspace not found")

        clean_name = _strip_deleted(ws.name)

        Workspace.update({
            Workspace.name: clean_name,
            Workspace.status: "1",
        }).where(Workspace.id == ws_id).execute()

        # Also restore the associated RAGFlow tenant + technical user
        if ws.tenant_id:
            Tenant.update(status="1").where(Tenant.id == ws.tenant_id).execute()
            User.update(status="1").where(User.id == ws.tenant_id).execute()

    return {"id": ws_id, "name": clean_name}


@router.post("/archives/users/{user_id}/restore")
def restore_user(user_id: str, user=Depends(require_superuser)):
    from api.db.db_models import DB, User
    with DB.connection_context():
        target = User.get_or_none(
            (User.id == user_id) & (User.status == "0")
        )
        if not target:
            raise HTTPException(status_code=404, detail="Archived user not found")

        if target.email.startswith("purged_"):
            raise HTTPException(status_code=400, detail="Cannot restore a purged (tombstoned) user")

        clean_email = _strip_deleted(target.email)

        conflict = User.get_or_none(
            (User.email == clean_email) & (User.status == "1")
        )
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"Email '{clean_email}' is already taken by an active user",
            )

        User.update({
            User.email: clean_email,
            User.status: "1",
            User.is_active: "1",
        }).where(User.id == user_id).execute()

    return {"id": user_id, "email": clean_email}


# ---------------------------------------------------------------------------
# PURGE (hard-delete / RGPD tombstone)
# ---------------------------------------------------------------------------

@router.delete("/archives/orgs/{org_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_archived_org(org_id: str, confirm: str = "", user=Depends(require_superuser)):
    if confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Purge requires ?confirm=DELETE")

    from api.db.db_models import DB, OrgMember, Organisation
    from api.db.services.workspace_service import WorkspaceService
    from management.server.services.provisioning import purge_workspace

    with DB.connection_context():
        org = Organisation.get_or_none(Organisation.id == org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    workspaces = WorkspaceService.list_by_org(org_id, include_deleted=True)
    for ws in workspaces:
        purge_workspace(ws.id)

    with DB.connection_context():
        OrgMember.delete().where(OrgMember.org_id == org_id).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


@router.delete("/archives/workspaces/{ws_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_archived_workspace(ws_id: str, confirm: str = "", user=Depends(require_superuser)):
    if confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Purge requires ?confirm=DELETE")

    from api.db.services.workspace_service import WorkspaceService
    from management.server.services.provisioning import purge_workspace

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    purge_workspace(ws_id)


@router.delete("/archives/users/{user_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_archived_user(user_id: str, confirm: str = "", user=Depends(require_superuser)):
    """
    RGPD Tombstone: efface toutes les PII mais conserve la ligne pour l'intégrité
    référentielle (clés étrangères sur created_by, tenant_id, etc.).
    """
    if confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Purge requires ?confirm=DELETE")

    import time
    from api.db.db_models import DB, User, Tenant, UserTenant, WsGroupMember
    from api.db.services.workspace_service import WsMember
    from api.db.services.org_service import OrgMember

    with DB.connection_context():
        target = User.get_or_none(User.id == user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_superuser:
        raise HTTPException(status_code=400, detail="Cannot purge a superuser account")

    with DB.connection_context():
        # Revoke all remaining access rows
        WsGroupMember.delete().where(WsGroupMember.user_id == user_id).execute()
        WsMember.delete().where(WsMember.user_id == user_id).execute()
        OrgMember.delete().where(OrgMember.user_id == user_id).execute()
        UserTenant.delete().where(UserTenant.user_id == user_id).execute()
        # Delete personal tenant shell
        Tenant.delete().where(Tenant.id == user_id).execute()

        # RGPD tombstone — overwrite PII, keep row
        User.update({
            User.email: f"purged_{user_id}@anonymized.local",
            User.nickname: "Utilisateur Supprimé",
            User.password: "",
            User.avatar: None,
            User.is_active: "0",
            User.status: "0",
        }).where(User.id == user_id).execute()
