"""
Auth routes: login, me, refresh.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from werkzeug.security import check_password_hash

from management.server.auth.jwt import create_access_token, create_refresh_token, decode_refresh, decode_token
from management.server.auth.dependencies import get_current_user
from management.server.models.schemas import LoginRequest, TokenResponse, RefreshRequest, UserInfo

router = APIRouter()


def _open_session(user_id: str, request) -> str:
    """Crée la row admin_session et retourne son jti."""
    from datetime import datetime, timedelta
    from common.misc_utils import get_uuid
    from common.time_utils import current_timestamp
    from api.db.db_models import DB, AdminSession
    jti = get_uuid()
    with DB.connection_context():
        AdminSession.create(
            id=jti, user_id=user_id,
            expires_at=datetime.now() + timedelta(minutes=settings.JWT_REFRESH_TOKEN_EXPIRE_MINUTES),
            ip=(request.client.host if request and request.client else None),
            user_agent=(request.headers.get("user-agent", "")[:255] if request else None),
            create_time=current_timestamp(), update_time=current_timestamp(),
        )
    return jti


@router.post("/login", response_model=TokenResponse)
def login(request: Request, body: LoginRequest):
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

    jti = _open_session(user.id, request)
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id, jti),
    )


@router.get("/me", response_model=UserInfo)
def me(user=Depends(get_current_user)):
    """Get current user info with org and workspace memberships."""
    from api.db.services.org_service import OrgMemberService, OrgService
    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    org_memberships = OrgMemberService.list_orgs_for_user(user.id)
    orgs = []
    for m in org_memberships:
        ok, org = OrgService.get_by_id(m.org_id)
        if not ok or not org or getattr(org, "status", "1") != "1":
            continue  # skip deleted orgs
        orgs.append({
            "org_id": m.org_id,
            "org_name": org.name,
            "role": m.role,
        })

    workspaces = []
    ws_memberships = WsMemberService.list_workspaces_for_user(user.id)
    for m in ws_memberships:
        ok, ws = WorkspaceService.get_by_id(m.workspace_id)
        if not ok or not ws or getattr(ws, "status", "1") != "1":
            continue
        workspaces.append({
            "ws_id": ws.id,
            "ws_name": ws.name,
            "org_id": ws.org_id,
            "role": m.role,
        })

    return UserInfo(
        id=user.id,
        email=user.email,
        nickname=getattr(user, "nickname", None),
        is_superuser=bool(user.is_superuser),
        orgs=orgs,
        workspaces=workspaces,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, body: RefreshRequest):
    """Refresh STATEFUL : le jti du token doit correspondre à une row
    admin_session vivante — supprimer la row révoque la session (effet
    <= 60 min, la durée de l'access token). Rotation systématique :
    l'ancien jti est détruit, un refresh rejoué échoue."""
    from datetime import datetime
    from api.db.db_models import DB, AdminSession

    decoded = decode_refresh(body.refresh_token)
    if not decoded:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user_id, jti = decoded
    with DB.connection_context():
        row = AdminSession.get_or_none(AdminSession.id == jti)
        if row is None or row.user_id != user_id or (row.expires_at and row.expires_at < datetime.now()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session revoked or expired",
            )
        row.delete_instance()  # rotation : l'ancien refresh est mort
    new_jti = _open_session(user_id, request)
    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id, new_jti),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest):
    """Révoque la session côté serveur (best-effort : 204 même si le token
    est déjà invalide — l'objectif est que la row meure)."""
    from api.db.db_models import DB, AdminSession
    decoded = decode_refresh(body.refresh_token)
    if decoded:
        _, jti = decoded
        with DB.connection_context():
            row = AdminSession.get_or_none(AdminSession.id == jti)
            if row:
                row.delete_instance()
