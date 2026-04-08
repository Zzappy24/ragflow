"""
Group CRUD + group members + group datasets routes.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import get_current_user_id
from management.server.models.schemas import GroupCreate, GroupUpdate, GroupResponse

router = APIRouter()


def _require_ws_admin(ws_id: str, user_id: str):
    from management.server.auth.dependencies import require_ws_admin
    return require_ws_admin(ws_id, user_id)


def _group_to_response(group) -> dict:
    return {
        "id": group.id,
        "workspace_id": group.workspace_id,
        "name": group.name,
        "description": getattr(group, "description", ""),
        "create_time": getattr(group, "create_time", None),
    }


# -- Group CRUD --------------------------------------------------------------

@router.get("/workspaces/{ws_id}/groups", response_model=list[GroupResponse])
def list_groups(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """List all groups in a workspace."""
    _require_ws_admin(ws_id, user_id)
    from api.db.services.group_service import GroupService
    groups = GroupService.list_by_workspace(ws_id)
    return [_group_to_response(g) for g in groups]


@router.post("/workspaces/{ws_id}/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(ws_id: str, body: GroupCreate, user_id: str = Depends(get_current_user_id)):
    """Create a new group in a workspace."""
    _require_ws_admin(ws_id, user_id)

    from api.db.services.group_service import GroupService
    from common.misc_utils import get_uuid

    # Check name uniqueness within workspace
    existing = GroupService.query(workspace_id=ws_id, name=body.name, status="1")
    if existing:
        raise HTTPException(status_code=409, detail=f"Group '{body.name}' already exists")

    group_id = get_uuid()
    GroupService.save(**{
        "id": group_id,
        "workspace_id": ws_id,
        "name": body.name,
        "description": body.description,
    })

    ok, group = GroupService.get_by_id(group_id)
    return _group_to_response(group)


@router.put("/workspaces/{ws_id}/groups/{gid}", response_model=GroupResponse)
def update_group(ws_id: str, gid: str, body: GroupUpdate, user_id: str = Depends(get_current_user_id)):
    """Update a group."""
    _require_ws_admin(ws_id, user_id)
    from api.db.services.group_service import GroupService

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    update_data = body.model_dump(exclude_none=True)
    if update_data:
        GroupService.update_by_id(gid, update_data)

    ok, group = GroupService.get_by_id(gid)
    return _group_to_response(group)


@router.delete("/workspaces/{ws_id}/groups/{gid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(ws_id: str, gid: str, user_id: str = Depends(get_current_user_id)):
    """Delete a group (soft-delete)."""
    _require_ws_admin(ws_id, user_id)
    from api.db.services.group_service import GroupService

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    GroupService.update_by_id(gid, {"status": "0"})


# -- Group Members ------------------------------------------------------------

@router.get("/workspaces/{ws_id}/groups/{gid}/members")
def list_group_members(ws_id: str, gid: str, user_id: str = Depends(get_current_user_id)):
    """List all members of a group."""
    _require_ws_admin(ws_id, user_id)
    from api.db.services.group_service import GroupService, GroupMemberService

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    members = GroupMemberService.list_by_group(gid)
    result = []
    for m in members:
        from api.db.services.user_service import UserService
        ok_u, user = UserService.get_by_id(m.user_id)
        result.append({
            "id": m.id,
            "user_id": m.user_id,
            "email": user.email if ok_u and user else None,
            "nickname": getattr(user, "nickname", None) if ok_u and user else None,
        })
    return result


@router.post("/workspaces/{ws_id}/groups/{gid}/members/{uid}", status_code=status.HTTP_201_CREATED)
def add_group_member(ws_id: str, gid: str, uid: str, user_id: str = Depends(get_current_user_id)):
    """Add a user to a group. User must be a workspace member."""
    _require_ws_admin(ws_id, user_id)

    from api.db.services.group_service import GroupService, GroupMemberService
    from api.db.services.workspace_service import WsMemberService
    from common.misc_utils import get_uuid

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    ws_membership = WsMemberService.get_membership(ws_id, uid)
    if not ws_membership:
        raise HTTPException(status_code=400, detail="User must be a workspace member first")

    existing = GroupMemberService.query(group_id=gid, user_id=uid, status="1")
    if existing:
        raise HTTPException(status_code=409, detail="User is already in this group")

    GroupMemberService.save(**{
        "id": get_uuid(),
        "group_id": gid,
        "user_id": uid,
    })
    return {"message": "Member added"}


@router.delete("/workspaces/{ws_id}/groups/{gid}/members/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def remove_group_member(ws_id: str, gid: str, uid: str, user_id: str = Depends(get_current_user_id)):
    """Remove a user from a group."""
    _require_ws_admin(ws_id, user_id)

    from api.db.services.group_service import GroupService, GroupMemberService

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    GroupMemberService.remove(gid, uid)


# -- Group Datasets -----------------------------------------------------------

@router.get("/workspaces/{ws_id}/groups/{gid}/datasets")
def list_group_datasets(ws_id: str, gid: str, user_id: str = Depends(get_current_user_id)):
    """List datasets assigned to a group."""
    _require_ws_admin(ws_id, user_id)

    from api.db.services.group_service import GroupService, GroupDatasetService

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    assignments = GroupDatasetService.list_by_group(gid)
    return [{"id": a.id, "dataset_id": a.dataset_id} for a in assignments]


@router.post("/workspaces/{ws_id}/groups/{gid}/datasets/{did}", status_code=status.HTTP_201_CREATED)
def add_group_dataset(ws_id: str, gid: str, did: str, user_id: str = Depends(get_current_user_id)):
    """Assign a dataset to a group."""
    _require_ws_admin(ws_id, user_id)

    from api.db.services.group_service import GroupService, GroupDatasetService
    from common.misc_utils import get_uuid

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    existing = GroupDatasetService.query(group_id=gid, dataset_id=did, status="1")
    if existing:
        raise HTTPException(status_code=409, detail="Dataset is already in this group")

    GroupDatasetService.save(**{
        "id": get_uuid(),
        "group_id": gid,
        "dataset_id": did,
    })
    return {"message": "Dataset assigned"}


@router.delete("/workspaces/{ws_id}/groups/{gid}/datasets/{did}", status_code=status.HTTP_204_NO_CONTENT)
def remove_group_dataset(ws_id: str, gid: str, did: str, user_id: str = Depends(get_current_user_id)):
    """Remove a dataset from a group."""
    _require_ws_admin(ws_id, user_id)

    from api.db.services.group_service import GroupService, GroupDatasetService

    ok, group = GroupService.get_by_id(gid)
    if not ok or not group or group.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Group not found")

    GroupDatasetService.remove(gid, did)
