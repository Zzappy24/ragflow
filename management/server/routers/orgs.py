"""
Organisation CRUD routes.
Only superusers can create/delete orgs. Org admins can update their own org.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import (
    get_current_user,
    get_current_user_id,
    require_superuser,
    require_org_admin,
)
from pydantic import BaseModel

from management.server.models.schemas import OrgCreate, OrgUpdate, OrgResponse
from management.server.services import audit as audit_svc

router = APIRouter()


def _org_to_response(org) -> dict:
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "max_users": org.max_users,
        "max_workspaces": org.max_workspaces,
        "max_datasets": org.max_datasets,
        "max_documents": org.max_documents,
        "max_storage_gb": org.max_storage_gb,
        "llm_config": org.llm_config if isinstance(org.llm_config, dict) else None,
        "created_by": org.created_by,
        "create_time": getattr(org, "create_time", None),
    }


@router.get("", response_model=list[OrgResponse])
def list_orgs(user=Depends(get_current_user)):
    """List organisations visible to the caller.

    Superusers see every active org. Regular users see only the orgs where
    they hold a membership (any role) — the admin panel dashboard also
    surfaces ``/auth/me`` memberships, and both views need to agree.
    """
    from api.db.services.org_service import OrgService, OrgMemberService
    if user.is_superuser:
        orgs = OrgService.query(status="1")
    else:
        memberships = OrgMemberService.list_orgs_for_user(user.id)
        org_ids = {m.org_id for m in memberships}
        if not org_ids:
            return []
        orgs = [o for o in OrgService.query(status="1") if o.id in org_ids]
    return [_org_to_response(o) for o in orgs]


@router.post("", response_model=OrgResponse, status_code=status.HTTP_201_CREATED)
def create_org(body: OrgCreate, user=Depends(require_superuser)):
    """Create a new organisation (superuser only)."""
    from api.db.services.org_service import OrgService, OrgMemberService
    from common.misc_utils import get_uuid

    # Check slug uniqueness
    existing = OrgService.get_by_slug(body.slug)
    if existing:
        raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already exists")

    org_id = get_uuid()
    OrgService.save(**{
        "id": org_id,
        "name": body.name,
        "slug": body.slug,
        "max_users": body.max_users,
        "max_workspaces": body.max_workspaces,
        "max_datasets": body.max_datasets,
        "max_documents": body.max_documents,
        "max_storage_gb": body.max_storage_gb,
        "created_by": user.id,
    })

    # Creator becomes org_admin
    OrgMemberService.save(**{
        "id": get_uuid(),
        "org_id": org_id,
        "user_id": user.id,
        "role": "org_admin",
    })

    ok, org = OrgService.get_by_id(org_id)

    # --- CYLLENE CUSTOM CODE ---
    # Auto-create a default "Général" workspace for every new org so that
    # invited users always have at least one workspace to land in.
    try:
        from management.server.services.provisioning import provision_workspace
        provision_workspace(
            org_id=org_id,
            name="Général",
            description="Workspace par défaut de l'organisation",
            created_by=user.id,
        )
    except Exception as e:
        # Non-fatal — org is created, admin can create workspace manually.
        import logging
        logging.warning(f"Failed to auto-create default workspace for org {org_id}: {e}")
    # --- END CYLLENE CUSTOM CODE ---

    return _org_to_response(org)


@router.get("/{org_id}", response_model=OrgResponse)
def get_org(org_id: str, user_id: str = Depends(get_current_user_id)):
    """Get organisation details."""
    user = require_org_admin(org_id, user_id)
    from api.db.services.org_service import OrgService
    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org or org.status != "1":
        raise HTTPException(status_code=404, detail="Organisation not found")
    return _org_to_response(org)


@router.put("/{org_id}", response_model=OrgResponse)
def update_org(org_id: str, body: OrgUpdate, user_id: str = Depends(get_current_user_id)):
    """Update organisation (org_admin or superuser)."""
    user = require_org_admin(org_id, user_id)
    from api.db.services.org_service import OrgService

    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org or org.status != "1":
        raise HTTPException(status_code=404, detail="Organisation not found")

    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        return _org_to_response(org)

    OrgService.update_by_id(org_id, update_data)
    ok, org = OrgService.get_by_id(org_id)
    return _org_to_response(org)


# ------------------------------------------------------------------
# CUSTOM B2B SaaS — DA (identité visuelle) par organisation.
# Logo = data-URI base64 stocké dans Organisation.logo (comme tous les
# avatars du produit), couleur = #rrggbb dans Organisation.brand_color.
# Le front produit la lit via GET /api/v1/branding (branding_api.py).
# ------------------------------------------------------------------

_BRANDING_LOGO_MAX_BYTES = 400_000  # data-URI complet (~300 Ko d'image)
_BRANDING_MIMES = ("image/png", "image/jpeg", "image/svg+xml", "image/webp")


def validate_branding(logo: str | None, brand_color: str | None) -> str | None:
    """Retourne un message d'erreur, ou None si le branding est valide.

    Fonction pure (épinglée par test) : chaîne vide = effacement (OK),
    logo doit être un data-URI image base64 sous la limite de taille,
    couleur au format #rrggbb strict.
    """
    import re

    if logo:
        if not any(logo.startswith(f"data:{m};base64,") for m in _BRANDING_MIMES):
            return "Logo : format attendu data:image/(png|jpeg|svg+xml|webp);base64"
        if len(logo) > _BRANDING_LOGO_MAX_BYTES:
            return f"Logo trop lourd (max {_BRANDING_LOGO_MAX_BYTES // 1000} Ko encodé)"
    if brand_color and not re.fullmatch(r"#[0-9a-fA-F]{6}", brand_color):
        return "Couleur : format attendu #rrggbb"
    return None


class BrandingUpdate(BaseModel):
    logo: str | None = None
    brand_color: str | None = None


@router.get("/{org_id}/branding")
def get_org_branding(org_id: str, user_id: str = Depends(get_current_user_id)):
    require_org_admin(org_id, user_id)
    from api.db.db_models import DB, Organisation

    with DB.connection_context():
        org = Organisation.get_or_none(Organisation.id == org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation introuvable")
    return {"logo": org.logo or None, "brand_color": org.brand_color or None}


@router.put("/{org_id}/branding")
def update_org_branding(request: Request, org_id: str, body: BrandingUpdate,
                        user_id: str = Depends(get_current_user_id)):
    """Champ absent = inchangé ; chaîne vide = effacement (retour Cyllene)."""
    require_org_admin(org_id, user_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="Aucun champ fourni")
    err = validate_branding(fields.get("logo"), fields.get("brand_color"))
    if err:
        raise HTTPException(status_code=400, detail=err)

    from api.db.db_models import DB, Organisation

    with DB.connection_context():
        org = Organisation.get_or_none(Organisation.id == org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organisation introuvable")
        for k, v in fields.items():
            setattr(org, k, v or None)
        org.save()
    audit_svc.record(request=request, actor_user_id=user_id,
                     action="org.branding.update", org_id=org_id,
                     resource_type="organisation", resource_id=org_id,
                     details={"fields": sorted(fields)})
    return {"logo": org.logo or None, "brand_color": org.brand_color or None}


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org(request: Request, org_id: str, user=Depends(require_superuser)):
    """Soft-delete organisation and cascade to its workspaces + orphaned users.

    All entities share the same timestamp suffix so the entire snapshot can be
    restored atomically via ``POST /archives/orgs/{id}/restore``.

    Cascade rules:
    - Every active workspace of the org is soft-deleted (deprovision_workspace).
    - Human users whose ONLY active org membership was this org are deactivated
      (email suffixed, status=0, is_active=0). Users with memberships in other
      active orgs are left untouched.
    """
    import time
    from api.db.db_models import DB, Organisation, User
    from api.db.services.org_service import OrgService, OrgMemberService
    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    from management.server.services.provisioning import deprovision_workspace

    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    ts = int(time.time())

    # Collect all active workspaces before touching anything
    workspaces = WorkspaceService.list_by_org(org_id, include_deleted=False)
    ws_ids = [ws.id for ws in workspaces]

    # Collect all human user_ids in this org (via WsMember + OrgMember)
    user_ids: set[str] = set()
    for ws_id in ws_ids:
        for m in WsMemberService.list_by_workspace(ws_id):
            user_ids.add(m.user_id)
    for m in OrgMemberService.list_by_org(org_id):
        user_ids.add(m.user_id)

    # Soft-delete org
    with DB.connection_context():
        Organisation.update({
            Organisation.status: "0",
            Organisation.slug: f"{org.slug}___deleted___{ts}",
            Organisation.name: f"{org.name}___deleted___{ts}",
        }).where(Organisation.id == org_id).execute()

    # Cascade soft-delete to workspaces with the shared ts
    for ws_id in ws_ids:
        deprovision_workspace(ws_id, ts=ts)

    # Orphan check: deactivate users with no other active org membership
    with DB.connection_context():
        for uid in user_ids:
            other_active = (
                OrgMemberService.model
                .select()
                .where(
                    (OrgMemberService.model.user_id == uid) &
                    (OrgMemberService.model.org_id != org_id) &
                    (OrgMemberService.model.status == "1")
                )
                .count()
            )
            if other_active == 0:
                User.update({
                    User.email: User.email.concat(f"___deleted___{ts}"),
                    User.is_active: "0",
                    User.status: "0",
                }).where(User.id == uid).execute()

    audit_svc.record(
        request=request,
        actor_user_id=user.id,
        action=audit_svc.ORG_ARCHIVE,
        org_id=org_id,
        resource_type="organisation",
        resource_id=org_id,
        details={"target_display_name": org.name, "name": org.name, "cascaded_workspaces": len(ws_ids), "cascaded_users": len(user_ids)},
    )


@router.delete("/{org_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_org(org_id: str, confirm: str = "", user=Depends(require_superuser)):
    """
    HARD delete an organisation and ALL its data (superuser only).

    Cascades to: all workspaces (+ their tenants, datasets, documents),
    all members, the org row itself.
    Irreversible. Requires ``?confirm=DELETE`` query parameter.
    """
    if confirm != "DELETE":
        raise HTTPException(
            status_code=400,
            detail="Purge requires ?confirm=DELETE query parameter",
        )
    from api.db.services.org_service import OrgService
    from api.db.services.workspace_service import WorkspaceService
    from management.server.services.provisioning import purge_workspace

    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    # Purge every workspace first (cascades datasets/documents/members)
    workspaces = WorkspaceService.list_by_org(org_id, include_deleted=True)
    for ws in workspaces:
        purge_workspace(ws.id)

    # Then delete org-level rows
    from api.db.db_models import DB, OrgMember, Organisation
    from api.db.services.org_service import OrgMemberService
    with DB.connection_context():
        OrgMember.delete().where(OrgMember.org_id == org_id).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


@router.get("/{org_id}/stats")
def org_stats(org_id: str, user_id: str = Depends(get_current_user_id)):
    """Get resource usage stats for an org."""
    user = require_org_admin(org_id, user_id)
    from api.db.services.org_service import OrgService
    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    counts = OrgService.get_resource_counts(org_id)

    # CIA-9 — stockage : fichiers (MinIO, temps réel via SUM(document.size))
    # + index Infinity (mesure réelle via l'endpoint interne, cache 5 min).
    # infinity_bytes=None si la mesure est indisponible — l'UI dégrade en
    # "fichiers seuls", jamais de 500.
    minio_bytes = OrgService.get_minio_storage_bytes(org_id)
    infinity_bytes = None
    try:
        from api.db.db_models import Workspace
        from management.server.services.storage_usage import get_infinity_usage_by_tenant
        usage = get_infinity_usage_by_tenant()
        if usage is not None:
            tenant_ids = [
                ws.tenant_id
                for ws in Workspace.select().where(
                    (Workspace.org_id == org_id) & (Workspace.status == "1")
                )
            ]
            infinity_bytes = sum(usage.get(t, {}).get("bytes", 0) for t in tenant_ids)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("org_stats: mesure Infinity indisponible", exc_info=True)

    return {
        "quotas": {
            "users": {"current": counts.get("users", 0), "max": org.max_users},
            "workspaces": {"current": counts.get("workspaces", 0), "max": org.max_workspaces},
            "datasets": {"current": counts.get("datasets", 0), "max": org.max_datasets},
            "documents": {"current": counts.get("documents", 0), "max": org.max_documents},
        },
        "storage": {
            "minio_bytes": minio_bytes,
            "infinity_bytes": infinity_bytes,  # None = indisponible
            "total_bytes": minio_bytes + (infinity_bytes or 0),
            "max_bytes": (org.max_storage_gb or 0) * 1024**3,
        },
    }


@router.get("/{org_id}/storage")
def org_storage(org_id: str, user_id: str = Depends(get_current_user_id)):
    """CIA-9 phase 2 — ventilation du stockage par workspace, groupe et
    utilisateur. Fichiers = SUM(document.size) temps réel ; index Infinity =
    mesure réelle via l'endpoint interne (None si indisponible)."""
    require_org_admin(org_id, user_id)
    from peewee import fn
    from api.db.db_models import DB, Workspace, Knowledgebase, Document, User, WsGroup, WsGroupDataset
    from management.server.services.storage_usage import (
        get_infinity_usage_by_kb,
        get_infinity_usage_by_tenant,
    )

    inf_by_tenant = get_infinity_usage_by_tenant()  # None = indisponible
    inf_by_kb = get_infinity_usage_by_kb()

    with DB.connection_context():
        ws_list = list(
            Workspace.select().where(
                (Workspace.org_id == org_id) & (Workspace.status == "1")
            )
        )
        tenant_ids = [ws.tenant_id for ws in ws_list]
        ws_ids = [ws.id for ws in ws_list]

        # -- fichiers par tenant (workspace) et par KB, en 2 requêtes groupées
        minio_by_tenant: dict = {}
        minio_by_kb: dict = {}
        if tenant_ids:
            for row in (
                Document.select(
                    Knowledgebase.tenant_id.alias("tid"),
                    Document.kb_id.alias("kb"),
                    fn.COALESCE(fn.SUM(Document.size), 0).alias("b"),
                    fn.COUNT(Document.id).alias("n"),
                )
                .join(Knowledgebase, on=(Document.kb_id == Knowledgebase.id))
                .where(Knowledgebase.tenant_id.in_(tenant_ids))
                .group_by(Knowledgebase.tenant_id, Document.kb_id)
                .dicts()
            ):
                t = minio_by_tenant.setdefault(row["tid"], {"bytes": 0, "docs": 0})
                t["bytes"] += int(row["b"]); t["docs"] += int(row["n"])
                minio_by_kb[row["kb"]] = {"bytes": int(row["b"]), "docs": int(row["n"])}

        workspaces = [
            {
                "ws_id": ws.id,
                "name": ws.name,
                "minio_bytes": minio_by_tenant.get(ws.tenant_id, {}).get("bytes", 0),
                "doc_count": minio_by_tenant.get(ws.tenant_id, {}).get("docs", 0),
                "infinity_bytes": (
                    inf_by_tenant.get(ws.tenant_id, {}).get("bytes", 0)
                    if inf_by_tenant is not None else None
                ),
            }
            for ws in ws_list
        ]

        # « Par utilisateur » RETIRÉ (2026-09-02) : les documents sont créés
        # avec created_by = tenant_id (file_service.py) — le classement
        # groupait donc des TENANTS de workspaces déguisés en utilisateurs et
        # n'affichait que les comptes techniques @internal, toujours. Une
        # vraie attribution par humain exigerait de tracer l'uploadeur réel
        # (feature, pas un filtre) ; la vue « par workspace » ci-dessus porte
        # déjà l'information exacte.
        users = []

        # -- par groupe (ws_group → ws_group_dataset → KB) : somme des KB liés
        groups = []
        if ws_ids:
            group_list = list(
                WsGroup.select().where(
                    (WsGroup.workspace_id.in_(ws_ids)) & (WsGroup.status == "1")
                )
            )
            gids = [g.id for g in group_list]
            kb_links: dict = {}
            if gids:
                for link in WsGroupDataset.select().where(WsGroupDataset.group_id.in_(gids)):
                    kb_links.setdefault(link.group_id, []).append(link.dataset_id)
            ws_names = {ws.id: ws.name for ws in ws_list}
            for g in group_list:
                kbs = kb_links.get(g.id, [])
                groups.append({
                    "group_id": g.id,
                    "name": g.name,
                    "workspace": ws_names.get(g.workspace_id, g.workspace_id),
                    "dataset_count": len(kbs),
                    "minio_bytes": sum(minio_by_kb.get(k, {}).get("bytes", 0) for k in kbs),
                    "infinity_bytes": (
                        sum(inf_by_kb.get(k, {}).get("bytes", 0) for k in kbs)
                        if inf_by_kb is not None else None
                    ),
                })

    return {
        "workspaces": workspaces,
        "users": users,
        "groups": groups,
        "infinity_available": inf_by_tenant is not None,
    }
