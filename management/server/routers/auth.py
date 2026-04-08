"""
Auth routes: login, me, refresh.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from werkzeug.security import check_password_hash

from management.server.auth.jwt import create_access_token, create_refresh_token, decode_token
from management.server.auth.dependencies import get_current_user
from management.server.models.schemas import LoginRequest, TokenResponse, RefreshRequest, UserInfo

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    """Authenticate with email/password. Only superusers and org_admins can log in.

    The password arrives RSA-wrapped by the frontend (``rsaPsw`` helper in
    ``management/web/src/utils/crypto.ts``), mirroring RAGFlow's ``/v1/user/login``
    convention. This is obfuscation, not real crypto — the keypair ships with
    the repo — but it keeps plaintext passwords out of reverse-proxy logs and
    browser history, and it keeps both login endpoints aligned on the same
    posture so middleware changes can't accidentally leak one side.
    """
    from api.db.services.user_service import UserService
    from api.utils.crypt import decrypt

    try:
        plaintext_password = decrypt(body.password)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed password payload",
        )

    user = UserService.query_user(body.email, plaintext_password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Only allow superusers or users who are org_admin somewhere
    if not user.is_superuser:
        from api.db.services.org_service import OrgMemberService
        orgs = OrgMemberService.list_orgs_for_user(user.id)
        has_admin_role = any(m.role == "org_admin" for m in orgs)
        if not has_admin_role:
            # Also allow ws_admins
            from api.db.services.workspace_service import WsMemberService
            ws_memberships = WsMemberService.list_workspaces_for_user(user.id)
            has_ws_admin = any(m.role == "ws_admin" for m in ws_memberships)
            if not has_ws_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin access required",
                )

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserInfo)
def me(user=Depends(get_current_user)):
    """Get current user info with org memberships."""
    from api.db.services.org_service import OrgMemberService, OrgService
    org_memberships = OrgMemberService.list_orgs_for_user(user.id)
    orgs = []
    for m in org_memberships:
        ok, org = OrgService.get_by_id(m.org_id)
        orgs.append({
            "org_id": m.org_id,
            "org_name": org.name if ok and org else "?",
            "role": m.role,
        })

    return UserInfo(
        id=user.id,
        email=user.email,
        nickname=getattr(user, "nickname", None),
        is_superuser=bool(user.is_superuser),
        orgs=orgs,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest):
    """Get a new access token using a refresh token."""
    user_id = decode_token(body.refresh_token, expected_type="refresh")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )
