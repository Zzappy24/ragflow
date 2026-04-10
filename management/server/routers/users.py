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
     RAGFlow ``/set-password?invite_token=<jwt>`` page. For dev we return the
     URL directly; in production this is what the mailer pushes into the
     outbound email.
  4. The user lands on RAGFlow, sets a password, and is auto-logged in.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import (
    get_current_user_id,
    require_org_admin,
    require_superuser,
)
from management.server.auth.jwt import create_invite_token
from management.server.config import settings
from management.server.models.schemas import UserProvision, UserProvisionResponse

router = APIRouter()


@router.post(
    "/users",
    response_model=UserProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def provision_user_route(
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

    token = create_invite_token(new_user_id)
    invite_url = f"{settings.RAGFLOW_BASE_URL}/set-password?invite_token={token}"

    return UserProvisionResponse(
        user_id=new_user_id,
        email=body.email,
        invite_url=invite_url,
        expires_in=settings.INVITE_TOKEN_EXPIRE_SECONDS,
    )


@router.delete("/users/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(uid: str, user=Depends(require_superuser)):
    """
    GDPR hard-delete via the Tombstone pattern (superuser only).

    Instead of deleting the ``User`` row (which would break INNER JOINs on
    ``tenant_id`` / ``created_by`` throughout RAGFlow and make enterprise
    datasets, chats, and agents vanish from the UI), we:

    1. **Revoke all access** — physically delete WsMember, WsGroupMember,
       OrgMember, the personal Tenant, and UserTenant rows.
    2. **Anonymise PII** — overwrite email, nickname, avatar, password on the
       User row so no personal data remains.
    3. **Deactivate** — set ``is_active='0'`` so the login flow rejects the
       account, and ``status='0'`` for good measure.

    The User row stays as a tombstone. INNER JOINs resolve to
    "Utilisateur Supprimé" in the UI, enterprise data is preserved, and the
    RGPD right-to-erasure is satisfied.
    """
    from api.db.services.user_service import UserService

    ok, target = UserService.get_by_id(uid)
    if not ok or not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.is_superuser:
        raise HTTPException(status_code=400, detail="Cannot delete a superuser account")

    if uid == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    from api.db.db_models import DB, User, Tenant, UserTenant, WsGroupMember
    from api.db.services.workspace_service import WsMember
    from api.db.services.org_service import OrgMember

    with DB.connection_context():
        # 1. Revoke all access
        WsGroupMember.delete().where(WsGroupMember.user_id == uid).execute()
        WsMember.delete().where(WsMember.user_id == uid).execute()
        OrgMember.delete().where(OrgMember.user_id == uid).execute()
        UserTenant.delete().where(UserTenant.user_id == uid).execute()
        Tenant.delete().where(Tenant.id == uid).execute()

        # 2. Anonymise PII + 3. Deactivate
        User.update({
            User.email: f"deleted_{uid}@anonymized.local",
            User.nickname: "Utilisateur Supprimé",
            User.password: "",
            User.avatar: None,
            User.is_active: "0",
            User.status: "0",
        }).where(User.id == uid).execute()
