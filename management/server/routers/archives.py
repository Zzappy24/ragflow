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

from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import require_superuser, get_current_user_id, require_org_admin
from management.server.services import audit as audit_svc

router = APIRouter()

_DELETED_RE = re.compile(r"___deleted___(\d+)$")


def _strip_deleted(value: str) -> str:
    """Remove ___deleted___[timestamp] suffix added at soft-delete time."""
    return _DELETED_RE.sub("", value)


def _extract_ts(value: str) -> str | None:
    """Extract the timestamp from a ___deleted___[ts] suffix, or None."""
    m = _DELETED_RE.search(value)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# LIST archived entities
# ---------------------------------------------------------------------------

@router.get("/archives/orgs")
def list_archived_orgs(user=Depends(require_superuser)):
    from api.db.db_models import DB, Organisation, Workspace, User
    with DB.connection_context():
        rows = list(
            Organisation.select()
            .where(Organisation.status == "0")
            .order_by(Organisation.create_time.desc())
        )
        # For each archived org, count how many workspaces/users share the same ts
        result = []
        for o in rows:
            ts = _extract_ts(o.name)
            ws_count = (
                Workspace.select()
                .where(Workspace.name.contains(f"___deleted___{ts}"))
                .count()
            ) if ts else 0
            user_count = (
                User.select()
                .where(User.email.contains(f"___deleted___{ts}"))
                .count()
            ) if ts else 0
            result.append({
                "id": o.id,
                "name": _strip_deleted(o.name),
                "slug": _strip_deleted(o.slug),
                "raw_name": o.name,
                "raw_slug": o.slug,
                "archived_ts": ts,
                "archived_workspaces": ws_count,
                "archived_users": user_count,
                "created_by": o.created_by,
                "create_time": getattr(o, "create_time", None),
            })
    return result


@router.get("/archives/workspaces")
def list_archived_workspaces(user=Depends(require_superuser)):
    from api.db.db_models import DB, Workspace, Organisation
    with DB.connection_context():
        rows = list(
            Workspace.select()
            .where(Workspace.status == "0")
            .order_by(Workspace.create_time.desc())
        )
        # Build a ts→org map for snapshot detection
        archived_orgs = list(Organisation.select().where(Organisation.status == "0"))
        ts_to_org = {
            _extract_ts(o.name): o
            for o in archived_orgs
            if _extract_ts(o.name)
        }
        # Resolve org names for display (active or archived)
        org_ids = {w.org_id for w in rows}
        orgs = {o.id: o for o in Organisation.select().where(Organisation.id.in_(org_ids))} if org_ids else {}

    result = []
    for w in rows:
        ws_ts = _extract_ts(w.name)
        snapshot_org = ts_to_org.get(ws_ts) if ws_ts else None
        result.append({
            "id": w.id,
            "org_id": w.org_id,
            "org_name": _strip_deleted(orgs[w.org_id].name) if w.org_id in orgs else None,
            "name": _strip_deleted(w.name),
            "raw_name": w.name,
            "tenant_id": w.tenant_id,
            "created_by": w.created_by,
            "create_time": getattr(w, "create_time", None),
            # If this workspace was archived as part of an org cascade:
            "org_snapshot_id": snapshot_org.id if snapshot_org else None,
            "org_snapshot_name": _strip_deleted(snapshot_org.name) if snapshot_org else None,
        })
    return result


@router.get("/archives/users")
def list_archived_users(user=Depends(require_superuser)):
    from api.db.db_models import DB, User, Organisation, Workspace
    with DB.connection_context():
        technical_user_ids = (
            Workspace.select(Workspace.tenant_id)
            .where(Workspace.tenant_id.is_null(False))
        )
        rows = list(
            User.select()
            .where(
                (User.status == "0") &
                ~(User.email.startswith("purged_")) &
                User.id.not_in(technical_user_ids)
            )
            .order_by(User.create_time.desc())
        )
        # Build a ts→org map for snapshot detection
        archived_orgs = list(Organisation.select().where(Organisation.status == "0"))
        ts_to_org = {
            _extract_ts(o.name): o
            for o in archived_orgs
            if _extract_ts(o.name)
        }

    result = []
    for u in rows:
        user_ts = _extract_ts(u.email)
        snapshot_org = ts_to_org.get(user_ts) if user_ts else None
        result.append({
            "id": u.id,
            "email": _strip_deleted(u.email),
            "raw_email": u.email,
            "nickname": u.nickname,
            "is_superuser": u.is_superuser,
            "create_time": getattr(u, "create_time", None),
            # If this user was archived as part of an org cascade:
            "org_snapshot_id": snapshot_org.id if snapshot_org else None,
            "org_snapshot_name": _strip_deleted(snapshot_org.name) if snapshot_org else None,
        })
    return result


# ---------------------------------------------------------------------------
# RESTORE
# ---------------------------------------------------------------------------

@router.post("/archives/orgs/{org_id}/restore")
def restore_org(request: Request, org_id: str, user=Depends(require_superuser)):
    """Restore an archived org and cascade to all workspaces + users that share
    the same archive timestamp (timestamp-matching restore).

    The entire operation runs inside a single DB transaction: if any step fails
    (e.g. email conflict on a user), the whole restore rolls back and a 400 is
    returned so the database is never left in a half-restored state.
    """
    from api.db.db_models import DB, Organisation, Workspace, Tenant, User

    with DB.connection_context():
        org = Organisation.get_or_none(
            (Organisation.id == org_id) & (Organisation.status == "0")
        )
        if not org:
            raise HTTPException(status_code=404, detail="Archived organisation not found")

        clean_name = _strip_deleted(org.name)
        clean_slug = _strip_deleted(org.slug)
        ts = _extract_ts(org.name)

        try:
            with DB.atomic():
                # --- Restore org ---
                conflict_org = Organisation.get_or_none(
                    (Organisation.slug == clean_slug) & (Organisation.status == "1")
                )
                if conflict_org:
                    raise ValueError(f"Slug '{clean_slug}' is already taken by an active organisation")

                Organisation.update({
                    Organisation.name: clean_name,
                    Organisation.slug: clean_slug,
                    Organisation.status: "1",
                }).where(Organisation.id == org_id).execute()

                restored_ws = 0
                restored_users = 0

                if ts:
                    suffix = f"___deleted___{ts}"

                    # --- Restore workspaces with matching ts ---
                    archived_ws = list(
                        Workspace.select()
                        .where(Workspace.name.contains(suffix))
                    )
                    for ws in archived_ws:
                        ws_clean_name = _strip_deleted(ws.name)
                        Workspace.update({
                            Workspace.name: ws_clean_name,
                            Workspace.status: "1",
                        }).where(Workspace.id == ws.id).execute()
                        if ws.tenant_id:
                            Tenant.update(status="1").where(Tenant.id == ws.tenant_id).execute()
                            User.update(status="1").where(User.id == ws.tenant_id).execute()
                        restored_ws += 1

                    # --- Restore users with matching ts ---
                    archived_users = list(
                        User.select()
                        .where(
                            User.email.contains(suffix) &
                            ~(User.email.startswith("purged_"))
                        )
                    )
                    for u in archived_users:
                        clean_email = _strip_deleted(u.email)
                        conflict_user = User.get_or_none(
                            (User.email == clean_email) & (User.status == "1")
                        )
                        if conflict_user:
                            raise ValueError(
                                f"Cannot restore: email '{clean_email}' is already taken by an active user"
                            )
                        User.update({
                            User.email: clean_email,
                            User.status: "1",
                            User.is_active: "1",
                        }).where(User.id == u.id).execute()
                        restored_users += 1

        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    audit_svc.record(
        request=request,
        actor_user_id=user.id,
        action=audit_svc.ORG_RESTORE,
        org_id=org_id,
        resource_type="organisation",
        resource_id=org_id,
        details={"target_display_name": clean_name, "name": clean_name, "restored_workspaces": restored_ws, "restored_users": restored_users},
    )

    return {
        "id": org_id,
        "name": clean_name,
        "slug": clean_slug,
        "restored_workspaces": restored_ws,
        "restored_users": restored_users,
    }


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
def purge_archived_org(request: Request, org_id: str, confirm: str = "", user=Depends(require_superuser)):
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

    audit_svc.record(
        request=request,
        actor_user_id=user.id,
        action=audit_svc.ORG_PURGE,
        org_id=org_id,
        resource_type="organisation",
        resource_id=org_id,
        details={"target_display_name": _strip_deleted(org.name), "name": _strip_deleted(org.name)},
    )


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
