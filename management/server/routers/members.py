"""
Member management routes for both org-level and workspace-level members.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import get_current_user_id, require_org_admin
from management.server.models.schemas import MemberAdd, MemberUpdateRole, MemberResponse

router = APIRouter()


def _member_to_response(member, user=None) -> dict:
    return {
        "id": member.id,
        "user_id": member.user_id,
        "email": getattr(user, "email", None) if user else None,
        "nickname": getattr(user, "nickname", None) if user else None,
        "role": member.role,
        "create_time": getattr(member, "create_time", None),
    }


def _load_user(user_id: str):
    from api.db.services.user_service import UserService
    ok, user = UserService.get_by_id(user_id)
    return user if ok else None


# -- Org Members -------------------------------------------------------------

@router.get("/orgs/{org_id}/members", response_model=list[MemberResponse])
def list_org_members(org_id: str, user_id: str = Depends(get_current_user_id)):
    """List all members of an organisation."""
    require_org_admin(org_id, user_id)
    from api.db.services.org_service import OrgMemberService
    members = OrgMemberService.list_by_org(org_id)
    result = []
    for m in members:
        user = _load_user(m.user_id)
        result.append(_member_to_response(m, user))
    return result


@router.post("/orgs/{org_id}/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
def add_org_member(org_id: str, body: MemberAdd, user_id: str = Depends(get_current_user_id)):
    """Add a member to an organisation by email."""
    require_org_admin(org_id, user_id)

    if body.role not in ("org_admin", "member"):
        raise HTTPException(status_code=400, detail="Org role must be 'org_admin' or 'member'")

    from api.db.services.user_service import UserService
    users = UserService.query(email=body.email)
    if not users:
        raise HTTPException(status_code=404, detail=f"User with email '{body.email}' not found in RAGFlow")
    target_user = users[0]

    from api.db.services.org_service import OrgMemberService
    existing = OrgMemberService.get_membership(org_id, target_user.id)
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member of this organisation")

    from common.misc_utils import get_uuid
    from api.db.services.quota_service import check_quota
    allowed, msg = check_quota(org_id, "user")
    if not allowed:
        raise HTTPException(status_code=409, detail=msg)

    member_id = get_uuid()
    OrgMemberService.save(**{
        "id": member_id,
        "org_id": org_id,
        "user_id": target_user.id,
        "role": body.role,
    })

    ok, member = OrgMemberService.get_by_id(member_id)
    return _member_to_response(member, target_user)


@router.put("/orgs/{org_id}/members/{uid}", response_model=MemberResponse)
def update_org_member_role(org_id: str, uid: str, body: MemberUpdateRole, user_id: str = Depends(get_current_user_id)):
    """Update an org member's role."""
    require_org_admin(org_id, user_id)

    if body.role not in ("org_admin", "member"):
        raise HTTPException(status_code=400, detail="Org role must be 'org_admin' or 'member'")

    from api.db.services.org_service import OrgMemberService
    membership = OrgMemberService.get_membership(org_id, uid)
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    OrgMemberService.update_by_id(membership.id, {"role": body.role})
    ok, member = OrgMemberService.get_by_id(membership.id)
    user = _load_user(uid)
    return _member_to_response(member, user)


@router.delete("/orgs/{org_id}/members/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def remove_org_member(org_id: str, uid: str, user_id: str = Depends(get_current_user_id)):
    """Remove a member from an organisation."""
    require_org_admin(org_id, user_id)

    if uid == user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself from the organisation")

    from api.db.services.org_service import OrgMemberService
    membership = OrgMemberService.get_membership(org_id, uid)
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    OrgMemberService.update_by_id(membership.id, {"status": "0"})


# -- Workspace Members -------------------------------------------------------

@router.get("/workspaces/{ws_id}/members", response_model=list[MemberResponse])
def list_ws_members(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """List all members of a workspace."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    from api.db.services.workspace_service import WsMemberService
    members = WsMemberService.list_by_workspace(ws_id)
    result = []
    for m in members:
        user = _load_user(m.user_id)
        result.append(_member_to_response(m, user))
    return result


@router.post("/workspaces/{ws_id}/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
def add_ws_member(ws_id: str, body: MemberAdd, user_id: str = Depends(get_current_user_id)):
    """Add a member to a workspace. User must already be an org member."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    if body.role not in ("ws_admin", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="WS role must be 'ws_admin', 'editor', or 'viewer'")

    from api.db.services.user_service import UserService
    users = UserService.query(email=body.email)
    if not users:
        raise HTTPException(status_code=404, detail=f"User with email '{body.email}' not found")
    target_user = users[0]

    # Verify user is org member
    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    from api.db.services.org_service import OrgMemberService
    org_membership = OrgMemberService.get_membership(ws.org_id, target_user.id)
    if not org_membership:
        raise HTTPException(status_code=400, detail="User must be an org member first")

    existing = WsMemberService.get_membership(ws_id, target_user.id)
    if existing:
        raise HTTPException(status_code=409, detail="User is already a workspace member")

    from common.misc_utils import get_uuid
    from management.server.services.provisioning import grant_workspace_access

    member_id = get_uuid()
    grant_workspace_access(ws=ws, target_user_id=target_user.id, role=body.role, member_id=member_id)

    ok, member = WsMemberService.get_by_id(member_id)
    return _member_to_response(member, target_user)


@router.put("/workspaces/{ws_id}/members/{uid}", response_model=MemberResponse)
def update_ws_member_role(ws_id: str, uid: str, body: MemberUpdateRole, user_id: str = Depends(get_current_user_id)):
    """Update a workspace member's role."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    if body.role not in ("ws_admin", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="WS role must be 'ws_admin', 'editor', or 'viewer'")

    from api.db.services.workspace_service import WsMemberService
    membership = WsMemberService.get_membership(ws_id, uid)
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    WsMemberService.update_by_id(membership.id, {"role": body.role})
    ok, member = WsMemberService.get_by_id(membership.id)
    user = _load_user(uid)
    return _member_to_response(member, user)


@router.delete("/workspaces/{ws_id}/members/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def remove_ws_member(ws_id: str, uid: str, user_id: str = Depends(get_current_user_id)):
    """Remove a member from a workspace."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    if uid == user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    from api.db.services.workspace_service import WsMemberService
    membership = WsMemberService.get_membership(ws_id, uid)
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    WsMemberService.update_by_id(membership.id, {"status": "0"})
