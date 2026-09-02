"""
FastAPI dependencies for authentication and authorization.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from management.server.auth.sessions import resolve_session

security = HTTPBearer()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Résout le Bearer token OPAQUE contre la table admin_session
    (modèle GitHub/Slack) : la row est la session, la supprimer révoque
    instantanément. Aucun JWT — aucun secret ne peut forger une session."""
    user_id = resolve_session(credentials.credentials)
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
    if not ok or not ws or ws.status != "1":
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
    if not ok or not ws or ws.status != "1":
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


def require_code_team_admin(team_id: str, user_id: str = Depends(get_current_user_id)):
    """Superuser, org_admin of the team's org, or delegated CodeTeamMember admin."""
    user = _load_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.is_superuser:
        return user

    from api.db.db_models import DB, CodeTeam, CodeTeamMember
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None or team.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code team not found")

    from api.db.services.org_service import OrgMemberService
    membership = OrgMemberService.get_membership(team.org_id, user_id)
    if membership and membership.role == "org_admin":
        return user

    with DB.connection_context():
        delegated = CodeTeamMember.get_or_none(
            (CodeTeamMember.code_team_id == team_id) &
            (CodeTeamMember.user_id == user_id) & (CodeTeamMember.role == "admin"))
    if not delegated:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Code team admin access required")
    return user
