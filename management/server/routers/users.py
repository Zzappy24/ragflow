"""
Top-down user provisioning routes.

In our B2B SaaS model the admin panel is the *sole* identity source of truth.
Self-service ``/register`` on RAGFlow is disabled via ``REGISTER_ENABLED=0``;
users only enter the system through ``POST /api/admin/users``.

Flow:
  1. Org admin (or superuser) submits email + org + optional workspace.
  2. ``provision_user`` creates the RAGFlow ``User``, the ``OrgMember``, and
     optionally a ``WsMember`` + ``UserTenant`` — all atomically. The password
     is a random unguessable string and ``is_active='0'``, so the row is
     created but cannot be logged into.
  3. We mint an ``invite`` JWT, hand back an ``invite_url`` pointing at the
     RAGFlow ``/set-password?invite_code=<code>`` page. For dev we return the
     URL directly; in production this is what the mailer pushes into the
     outbound email.
  4. The user lands on RAGFlow, sets a password, and is auto-logged in.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import (
    get_current_user_id,
    require_org_admin,
    require_superuser,
)
from management.server.config import settings
from management.server.models.schemas import UserProvision, UserProvisionResponse
from management.server.services import audit as audit_svc

router = APIRouter()


@router.post(
    "/users",
    response_model=UserProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_user_route(
    request: Request,
    body: UserProvision,
    user_id: str = Depends(get_current_user_id),
):
    # Only org admins of the target org (or superusers) can invite into it.
    require_org_admin(body.org_id, user_id)

    # Quota check — counted against the org's max_users ceiling.
    from api.db.services.quota_service import check_quota
    allowed, msg = check_quota(body.org_id, "user")
    if not allowed:
        raise HTTPException(status_code=409, detail=msg)

    if body.ws_id and not body.ws_role:
        raise HTTPException(
            status_code=400,
            detail="ws_role is required when ws_id is set",
        )

    from management.server.services.provisioning import provision_user
    try:
        new_user_id = provision_user(
            email=body.email,
            nickname=body.nickname,
            org_id=body.org_id,
            org_role=body.org_role,
            ws_id=body.ws_id,
            ws_role=body.ws_role,
            invited_by=user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                # Path migrated in upstream RESTful API refactor: old /v1/user/internal/invite/prepare
                # → new /api/v1/internal/invite/prepare (registered in api/apps/restful_apis/user_api.py).
                f"{settings.RAGFLOW_API_URL}/api/v1/internal/invite/prepare",
                json={"user_id": new_user_id, "ttl": settings.INVITE_TOKEN_EXPIRE_SECONDS},
                timeout=10,
            )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise HTTPException(status_code=502, detail=data.get("message", "Invite prepare failed"))
        invite_code = data["data"]["code"]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RAGFlow invite prepare failed: {e}")

    invite_url = f"{settings.RAGFLOW_BASE_URL}/set-password?invite_code={invite_code}"

    audit_svc.record(
        request=request,
        actor_user_id=user_id,
        action=audit_svc.USER_INVITE,
        org_id=body.org_id,
        resource_type="user",
        resource_id=new_user_id,
        details={"target_display_name": body.email, "email": body.email, "org_role": body.org_role, "ws_id": body.ws_id},
    )

    return UserProvisionResponse(
        user_id=new_user_id,
        email=body.email,
        invite_url=invite_url,
        expires_in=settings.INVITE_TOKEN_EXPIRE_SECONDS,
    )


@router.delete("/users/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(request: Request, uid: str, user=Depends(require_superuser)):
    """
    Soft-delete a user (superuser only).

    - Revokes all workspace/org access (WsMember, OrgMember, UserTenant rows deleted)
    - Email gets ``___deleted___[timestamp]`` suffix → freed for re-invite,
      readable in DB for restore
    - is_active='0', status='0' → cannot log in
    - PII (nickname, avatar, password) is preserved for audit trail

    For full RGPD erasure, use ``DELETE /users/{uid}/purge``.
    """
    import time
    from api.db.services.user_service import UserService

    ok, target = UserService.get_by_id(uid)
    if not ok or not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.is_superuser:
        raise HTTPException(status_code=400, detail="Cannot delete a superuser account")

    if uid == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    from api.db.db_models import DB, User, UserTenant, WsGroupMember
    from api.db.services.workspace_service import WsMember
    from api.db.services.org_service import OrgMember, OrgMemberService

    # Capture org memberships before they are deleted
    memberships = OrgMemberService.list_orgs_for_user(uid)
    audit_org_ids = [m.org_id for m in memberships] if memberships else [None]

    ts = int(time.time())
    with DB.connection_context():
        # Revoke all access
        WsGroupMember.delete().where(WsGroupMember.user_id == uid).execute()
        WsMember.delete().where(WsMember.user_id == uid).execute()
        OrgMember.delete().where(OrgMember.user_id == uid).execute()
        UserTenant.delete().where(UserTenant.user_id == uid).execute()

        # Deactivate + free email for re-invite
        User.update({
            User.email: f"{target.email}___deleted___{ts}",
            User.is_active: "0",
            User.status: "0",
        }).where(User.id == uid).execute()

    for org_id in audit_org_ids:
        audit_svc.record(
            request=request,
            actor_user_id=user.id,
            action=audit_svc.USER_DELETE,
            org_id=org_id,
            resource_type="user",
            resource_id=uid,
            details={"target_display_name": target.email, "email": target.email},
        )


@router.delete("/users/{uid}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_user(request: Request, uid: str, confirm: str = "", user=Depends(require_superuser)):
    """
    RGPD hard-delete via the Tombstone pattern (superuser only).

    Full PII erasure: overwrites email, nickname, avatar, password.
    The User row stays as a tombstone so INNER JOINs on created_by/tenant_id
    don't break (enterprise data is preserved).
    Requires ``?confirm=DELETE`` query parameter.
    """
    if confirm != "DELETE":
        raise HTTPException(
            status_code=400,
            detail="Purge requires ?confirm=DELETE query parameter",
        )
    from api.db.services.user_service import UserService

    ok, target = UserService.get_by_id(uid)
    if not ok or not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.is_superuser:
        raise HTTPException(status_code=400, detail="Cannot purge a superuser account")

    if uid == user.id:
        raise HTTPException(status_code=400, detail="Cannot purge yourself")

    from api.db.db_models import DB, User, Tenant, UserTenant, WsGroupMember
    from api.db.services.workspace_service import WsMember
    from api.db.services.org_service import OrgMember, OrgMemberService

    # Capture org memberships before they are deleted
    memberships = OrgMemberService.list_orgs_for_user(uid)
    audit_org_ids = [m.org_id for m in memberships] if memberships else [None]

    with DB.connection_context():
        WsGroupMember.delete().where(WsGroupMember.user_id == uid).execute()
        WsMember.delete().where(WsMember.user_id == uid).execute()
        OrgMember.delete().where(OrgMember.user_id == uid).execute()
        UserTenant.delete().where(UserTenant.user_id == uid).execute()
        Tenant.delete().where(Tenant.id == uid).execute()

        # Full PII erasure — tombstone stays for referential integrity
        User.update({
            User.email: f"purged_{uid}@anonymized.local",
            User.nickname: "Utilisateur Supprimé",
            User.password: "",
            User.avatar: None,
            User.is_active: "0",
            User.status: "0",
        }).where(User.id == uid).execute()

    for org_id in audit_org_ids:
        audit_svc.record(
            request=request,
            actor_user_id=user.id,
            action=audit_svc.USER_PURGE,
            org_id=org_id,
            resource_type="user",
            resource_id=uid,
            details={"target_display_name": target.email, "email": target.email, "nickname": target.nickname},
        )
