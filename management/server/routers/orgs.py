"""
Organisation CRUD routes.
Only superusers can create/delete orgs. Org admins can update their own org.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import (
    get_current_user,
    get_current_user_id,
    require_superuser,
    require_org_admin,
)
from management.server.models.schemas import OrgCreate, OrgUpdate, OrgResponse

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
    return _org_to_response(org)


@router.get("/{org_id}", response_model=OrgResponse)
def get_org(org_id: str, user_id: str = Depends(get_current_user_id)):
    """Get organisation details."""
    user = require_org_admin(org_id, user_id)
    from api.db.services.org_service import OrgService
    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return _org_to_response(org)


@router.put("/{org_id}", response_model=OrgResponse)
def update_org(org_id: str, body: OrgUpdate, user_id: str = Depends(get_current_user_id)):
    """Update organisation (org_admin or superuser)."""
    user = require_org_admin(org_id, user_id)
    from api.db.services.org_service import OrgService

    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        return _org_to_response(org)

    OrgService.update_by_id(org_id, update_data)
    ok, org = OrgService.get_by_id(org_id)
    return _org_to_response(org)


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_org(org_id: str, user=Depends(require_superuser)):
    """Delete organisation (superuser only). Soft-delete."""
    from api.db.services.org_service import OrgService
    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    OrgService.update_by_id(org_id, {"status": "0"})


@router.get("/{org_id}/stats")
def org_stats(org_id: str, user_id: str = Depends(get_current_user_id)):
    """Get resource usage stats for an org."""
    user = require_org_admin(org_id, user_id)
    from api.db.services.org_service import OrgService
    ok, org = OrgService.get_by_id(org_id)
    if not ok or not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    counts = OrgService.get_resource_counts(org_id)
    return {
        "quotas": {
            "users": {"current": counts.get("users", 0), "max": org.max_users},
            "workspaces": {"current": counts.get("workspaces", 0), "max": org.max_workspaces},
            "datasets": {"current": counts.get("datasets", 0), "max": org.max_datasets},
            "documents": {"current": counts.get("documents", 0), "max": org.max_documents},
        }
    }
