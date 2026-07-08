"""Code product routes — entitlements (superuser), teams (org admin), keys (delegated)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import (
    get_current_user_id, require_superuser, require_org_admin, require_code_team_admin,
)
from management.server.models.schemas import (
    CodeEntitlementUpsert, CodeTeamCreate, CodeTeamUpdate, CodeTeamAdminAdd, CodeKeyCreate,
)
from management.server.services import audit as audit_svc

router = APIRouter()


def _team_to_dict(t) -> dict:
    return {"id": t.id, "org_id": t.org_id, "name": t.name, "max_budget": t.max_budget,
            "model_access": t.model_access or [], "status": t.status,
            "sync_status": t.sync_status, "litellm_team_id": t.litellm_team_id}


def _key_to_dict(k) -> dict:
    return {"id": k.id, "code_team_id": k.code_team_id, "label": k.label,
            "key_masked": k.key_masked, "owner_user_id": k.owner_user_id,
            "status": k.status, "sync_status": k.sync_status}


@router.put("/orgs/{org_id}/code/entitlement")
def set_entitlement(request: Request, org_id: str, body: CodeEntitlementUpsert,
                    user=Depends(require_superuser)):
    from management.server.services import code_provisioning as cp
    try:
        ent = cp.upsert_entitlement(org_id=org_id, status=body.status,
                                    org_code_budget=body.org_code_budget,
                                    budget_period=body.budget_period, actor_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id,
                     action=audit_svc.CODE_ENTITLEMENT_SET, org_id=org_id,
                     resource_type="code_entitlement", resource_id=ent.id,
                     details={"status": body.status, "org_code_budget": body.org_code_budget})
    return {"org_id": org_id, "status": ent.status,
            "org_code_budget": ent.org_code_budget, "budget_period": ent.budget_period}


@router.get("/orgs/{org_id}/code/overview")
def code_overview(org_id: str, user_id: str = Depends(get_current_user_id)):
    # visible to any org member (code:view); org admin check covers superuser
    from api.db.services.org_service import OrgMemberService
    from api.db.services.user_service import UserService
    ok, user = UserService.get_by_id(user_id)
    if not (ok and user) or (not user.is_superuser
                             and not OrgMemberService.get_membership(org_id, user_id)):
        raise HTTPException(status_code=403, detail="Org membership required")

    from api.db.db_models import DB, CodeTeam, CodeKey
    from management.server.services import code_provisioning as cp
    ent = cp.get_entitlement(org_id)
    with DB.connection_context():
        teams = list(CodeTeam.select().where(
            (CodeTeam.org_id == org_id) & (CodeTeam.status == "active")))
        keys_by_team = {}
        for t in teams:
            keys_by_team[t.id] = [_key_to_dict(k) for k in
                                  CodeKey.select().where(CodeKey.code_team_id == t.id)]
    return {
        "entitlement": None if ent is None else {
            "status": ent.status, "org_code_budget": ent.org_code_budget,
            "budget_period": ent.budget_period},
        "allocated": cp.allocated_budget(org_id),
        "teams": [{**_team_to_dict(t), "keys": keys_by_team[t.id]} for t in teams],
    }


@router.post("/orgs/{org_id}/code/teams", status_code=status.HTTP_201_CREATED)
def create_team(request: Request, org_id: str, body: CodeTeamCreate,
                user_id: str = Depends(get_current_user_id)):
    user = require_org_admin(org_id, user_id)
    from management.server.services import code_provisioning as cp
    try:
        team = cp.create_code_team(org_id=org_id, name=body.name, max_budget=body.max_budget,
                                   model_access=body.model_access, created_by=user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_TEAM_CREATE,
                     org_id=org_id, resource_type="code_team", resource_id=team.id,
                     details={"name": body.name, "max_budget": body.max_budget})
    return _team_to_dict(team)


@router.put("/code/teams/{team_id}")
def update_team(team_id: str, body: CodeTeamUpdate,
                user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    require_org_admin(team.org_id, user_id)
    from management.server.services import code_provisioning as cp
    try:
        team = cp.update_code_team_budget(code_team_id=team_id, new_budget=body.max_budget)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _team_to_dict(team)


@router.post("/code/teams/{team_id}/admins", status_code=status.HTTP_201_CREATED)
def add_team_admin(team_id: str, body: CodeTeamAdminAdd,
                   user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeTeam, CodeTeamMember
    from api.db.services.user_service import UserService
    from api.db.services.org_service import OrgMemberService
    from common.misc_utils import get_uuid
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    require_org_admin(team.org_id, user_id)

    users = UserService.query(email=body.email, status="1")
    if not users:
        raise HTTPException(status_code=404, detail=f"No active user with email {body.email}")
    target = users[0]
    if not OrgMemberService.get_membership(team.org_id, target.id):
        raise HTTPException(status_code=422, detail="User is not a member of this org")
    with DB.connection_context():
        existing = CodeTeamMember.get_or_none(
            (CodeTeamMember.code_team_id == team_id) & (CodeTeamMember.user_id == target.id))
        if existing is None:
            CodeTeamMember.create(id=get_uuid(), code_team_id=team_id,
                                  user_id=target.id, role="admin")
    return {"code_team_id": team_id, "user_id": target.id, "role": "admin"}


@router.post("/code/teams/{team_id}/keys", status_code=status.HTTP_201_CREATED)
def create_key(request: Request, team_id: str, body: CodeKeyCreate,
               user=Depends(require_code_team_admin)):
    from management.server.services import code_provisioning as cp
    try:
        key, plain = cp.create_code_key(code_team_id=team_id, label=body.label,
                                        owner_user_id=body.owner_user_id, created_by=user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_CREATE,
                     org_id=None, resource_type="code_key", resource_id=key.id,
                     details={"label": body.label, "team_id": team_id})
    # plain_key is returned EXACTLY once, never persisted, never logged
    return {"key": _key_to_dict(key), "plain_key": plain}


@router.post("/code/keys/{key_id}/revoke")
def revoke_key(request: Request, key_id: str, user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_or_none(CodeKey.id == key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    user = require_code_team_admin(key.code_team_id, user_id)
    from management.server.services import code_provisioning as cp
    key = cp.revoke_code_key(code_key_id=key_id)
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_REVOKE,
                     org_id=None, resource_type="code_key", resource_id=key_id,
                     details={"label": key.label})
    return _key_to_dict(key)


@router.post("/code/reconcile")
def reconcile(user=Depends(require_superuser)):
    from management.server.services.code_reconcile import reconcile_all
    return reconcile_all()
