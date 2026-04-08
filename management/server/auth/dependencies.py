"""
FastAPI dependencies for authentication and authorization.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from management.server.auth.jwt import decode_token

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Extract and validate user_id from Bearer token."""
    user_id = decode_token(credentials.credentials, expected_type="access")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return user_id


def _load_user(user_id: str):
    """Load user by ID using CommonService.get_by_id (returns (bool, obj))."""
    from api.db.services.user_service import UserService
    ok, user = UserService.get_by_id(user_id)
    return user if ok else None


def get_current_user(user_id: str = Depends(get_current_user_id)):
    """Load full user object from DB. Raises 401 if not found."""
    user = _load_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


def require_superuser(user_id: str = Depends(get_current_user_id)):
    """Require the current user to be a superuser."""
    user = _load_user(user_id)
    if not user or not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superuser access required",
        )
    return user


def require_org_admin(org_id: str, user_id: str = Depends(get_current_user_id)):
    """Require the current user to be org_admin of the given org, or superuser."""
    from api.db.services.user_service import UserService
    user = _load_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.is_superuser:
        return user

    from api.db.services.org_service import OrgMemberService
    membership = OrgMemberService.get_membership(org_id, user_id)
    if not membership or membership.role != "org_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Org admin access required",
        )
    return user


def require_ws_admin(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Require ws_admin of the given workspace, or org_admin of its org, or superuser."""
    user = _load_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.is_superuser:
        return user

    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    # Check org admin
    from api.db.services.org_service import OrgMemberService
    org_membership = OrgMemberService.get_membership(ws.org_id, user_id)
    if org_membership and org_membership.role == "org_admin":
        return user

    # Check ws admin
    ws_membership = WsMemberService.get_membership(ws_id, user_id)
    if not ws_membership or ws_membership.role != "ws_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace admin access required",
        )
    return user


def require_ws_member(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Require any membership in the workspace, or org_admin of its org, or superuser.

    Used by endpoints any workspace member can hit (e.g. launching the workspace
    in the RAGFlow UI), as opposed to admin-only management actions.
    """
    user = _load_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.is_superuser:
        return user

    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found")

    # Org admin bypass
    from api.db.services.org_service import OrgMemberService
    org_membership = OrgMemberService.get_membership(ws.org_id, user_id)
    if org_membership and org_membership.role == "org_admin":
        return user

    # Any ws membership is sufficient
    ws_membership = WsMemberService.get_membership(ws_id, user_id)
    if not ws_membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace membership required",
        )
    return user
