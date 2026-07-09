"""Code product routes — entitlements (superuser), teams (org admin), keys (delegated)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import (
    get_current_user, get_current_user_id, require_superuser, require_org_admin,
    require_code_team_admin,
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


@router.get("/code/orgs-summary")
def orgs_summary(user=Depends(get_current_user)):
    """Landing de l'onglet Code : récap par org visible (statut, budget, alloué, compteurs).

    Visibilité identique à GET /orgs : superuser → toutes les orgs actives,
    sinon les orgs où l'appelant détient une membership (peu importe le rôle).
    """
    from peewee import fn
    from api.db.db_models import DB, CodeEntitlement, CodeTeam, CodeKey
    from api.db.services.org_service import OrgService, OrgMemberService

    if user.is_superuser:
        orgs = OrgService.query(status="1")
    else:
        memberships = OrgMemberService.list_orgs_for_user(user.id)
        org_ids_visible = {m.org_id for m in memberships}
        orgs = [o for o in OrgService.query(status="1") if o.id in org_ids_visible] if org_ids_visible else []
    org_ids = [o.id for o in orgs]
    if not org_ids:
        return []

    with DB.connection_context():
        ents = {e.org_id: e for e in CodeEntitlement.select().where(CodeEntitlement.org_id.in_(org_ids))}
        team_agg = {}
        for d in (CodeTeam.select(CodeTeam.org_id,
                                  fn.COALESCE(fn.SUM(CodeTeam.max_budget), 0.0).alias("allocated"),
                                  fn.COUNT(CodeTeam.id).alias("teams"))
                  .where((CodeTeam.org_id.in_(org_ids)) & (CodeTeam.status == "active"))
                  .group_by(CodeTeam.org_id).dicts()):
            team_agg[d["org_id"]] = (float(d["allocated"]), int(d["teams"]))
        key_agg = {}
        for d in (CodeKey.select(CodeTeam.org_id, fn.COUNT(CodeKey.id).alias("keys"))
                  .join(CodeTeam, on=(CodeKey.code_team_id == CodeTeam.id))
                  .where((CodeTeam.org_id.in_(org_ids)) & (CodeTeam.status == "active"))
                  .group_by(CodeTeam.org_id).dicts()):
            key_agg[d["org_id"]] = int(d["keys"])
        # litellm_team_id -> org attribution for real-spend aggregation
        team_org = {d["litellm_team_id"]: d["org_id"] for d in (
            CodeTeam.select(CodeTeam.litellm_team_id, CodeTeam.org_id)
            .where((CodeTeam.org_id.in_(org_ids)) & (CodeTeam.status == "active") &
                   (CodeTeam.litellm_team_id.is_null(False))).dicts())}

    # Real consumption (single /team/list). None = gateway unreachable → front shows "—".
    from management.server.services import code_provisioning as cp
    spend_map = cp.spend_by_litellm_team()
    spend_by_org: dict[str, float] = {}
    if spend_map is not None:
        for llm_tid, org in team_org.items():
            spend_by_org[org] = spend_by_org.get(org, 0.0) + spend_map.get(llm_tid, 0.0)

    out = []
    for o in orgs:
        ent = ents.get(o.id)
        allocated, teams = team_agg.get(o.id, (0.0, 0))
        out.append({
            "org_id": o.id,
            "org_name": o.name,
            "code_status": ent.status if ent else None,
            "org_code_budget": float(ent.org_code_budget) if ent else 0.0,
            "allocated": allocated,
            "spend": spend_by_org.get(o.id, 0.0) if spend_map is not None else None,
            "teams_count": teams,
            "keys_count": key_agg.get(o.id, 0),
        })
    return out


@router.get("/code/dashboard")
def code_dashboard(user=Depends(get_current_user)):
    """KPIs + courbe 30j + tops. Lecture pure (live LiteLLM + snapshots), n'écrit jamais."""
    from api.db.db_models import DB, CodeTeam, CodeKey, Organisation
    from api.db.services.org_service import OrgMemberService
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import daily_spend_series, last_run

    if user.is_superuser:
        org_ids = None
    else:
        org_ids = [m.org_id for m in OrgMemberService.list_orgs_for_user(user.id)]
        if not org_ids:
            raise HTTPException(status_code=403, detail="Org membership required")

    with DB.connection_context():
        tq = CodeTeam.select().where(CodeTeam.status == "active")
        if org_ids is not None:
            tq = tq.where(CodeTeam.org_id.in_(org_ids))
        teams = list(tq)
        team_ids = [t.id for t in teams]
        active_keys = (CodeKey.select().where(
            (CodeKey.code_team_id.in_(team_ids)) & (CodeKey.status == "active")).count()
            if team_ids else 0)
        org_names = {o.id: o.name for o in Organisation.select(Organisation.id, Organisation.name)
                     .where(Organisation.id.in_(list({t.org_id for t in teams})))} if teams else {}

    spend_map = cp.spend_by_litellm_team()
    def _spend(t):
        if spend_map is None or not t.litellm_team_id:
            return None
        return spend_map.get(t.litellm_team_id, 0.0)

    team_spends = [(t, _spend(t)) for t in teams]
    known = [(t, s) for t, s in team_spends if s is not None]
    cycle_spend = round(sum(s for _, s in known), 4) if spend_map is not None else None
    alerts = sum(1 for t, s in known if t.max_budget > 0 and s >= 0.8 * t.max_budget)

    by_org: dict[str, float] = {}
    for t, s in known:
        by_org[t.org_id] = by_org.get(t.org_id, 0.0) + s
    top_orgs = [{"org_id": oid, "org_name": org_names.get(oid, oid), "spend": round(sp, 4)}
                for oid, sp in sorted(by_org.items(), key=lambda x: -x[1])[:5]]

    # Today's tokens per team, single daily_usage(today) call. None (gateway
    # unreachable) propagates to every team; a team absent from a non-None
    # usage map is a real zero (reachable, no traffic today).
    usage = cp.usage_by_litellm_team()
    def _tokens_today(t):
        if usage is None or not t.litellm_team_id:
            return None
        u = usage.get(t.litellm_team_id)
        return u["tokens"] if u else 0

    top_teams = [{"code_team_id": t.id, "name": t.name,
                  "org_name": org_names.get(t.org_id, t.org_id),
                  "spend": round(s, 4), "max_budget": t.max_budget,
                  "tokens": _tokens_today(t)}
                 for t, s in sorted(known, key=lambda x: -x[1])[:5]]

    run = last_run()
    if run is not None:
        # MySQL DATETIME drops the UTC offset, so peewee reads ran_at back as
        # a naive datetime. Without re-attaching tzinfo, isoformat() emits no
        # +00:00 and browsers parse the string as local time — showing the
        # last housekeeping run as hours stale.
        from datetime import timezone
        last_housekeeping_at = run.ran_at.replace(tzinfo=timezone.utc).isoformat()
    else:
        last_housekeeping_at = None

    daily = daily_spend_series(org_ids, days=30)
    # None-safe sums over the series: None only if every day is None
    # (usage was unavailable at snapshot time for the whole window).
    tokens_vals = [d["tokens"] for d in daily]
    errors_vals = [d["errors"] for d in daily]
    tokens_30d = (sum(v for v in tokens_vals if v is not None)
                  if any(v is not None for v in tokens_vals) else None)
    errors_30d = (sum(v for v in errors_vals if v is not None)
                  if any(v is not None for v in errors_vals) else None)

    return {
        "kpis": {"cycle_spend": cycle_spend,
                 "active_orgs": len({t.org_id for t in teams}),
                 "teams": len(teams), "active_keys": active_keys,
                 "budget_alerts": alerts,
                 "tokens_30d": tokens_30d, "errors_30d": errors_30d},
        "daily": daily,
        "top_orgs": top_orgs, "top_teams": top_teams,
        "last_housekeeping_at": last_housekeeping_at,
    }


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

    # Real consumption from the gateway (single /team/list call).
    # None = gateway unreachable — the front renders "—", never 0.
    spend_map = cp.spend_by_litellm_team()
    # Today's tokens per team, single daily_usage(today) call (same
    # None/real-zero distinction as spend_map above).
    usage = cp.usage_by_litellm_team()
    def _tokens_today(t):
        if usage is None or not t.litellm_team_id:
            return None
        u = usage.get(t.litellm_team_id)
        return u["tokens"] if u else 0

    # Fetch key rows from the DB first, release the connection, THEN make the
    # (potentially slow) HTTP calls to the gateway — holding a pooled DB
    # connection idle across an outbound HTTP request starves the pool under
    # concurrent load.
    with DB.connection_context():
        key_rows_by_team = {t.id: list(CodeKey.select().where(CodeKey.code_team_id == t.id))
                            for t in teams}

    from management.server.services.code_provisioning import _client
    keys_by_team = {}
    for t in teams:
        key_rows = key_rows_by_team[t.id]
        key_spend = None
        if key_rows and spend_map is not None and t.litellm_team_id:
            try:
                key_spend = {k.get("token"): float(k.get("spend") or 0.0)
                             for k in _client().list_keys(t.litellm_team_id)}
            except Exception:
                key_spend = None
        keys_by_team[t.id] = [
            {**_key_to_dict(k),
             "spend": (key_spend or {}).get(k.litellm_key_id) if key_spend is not None else None}
            for k in key_rows]

    def _spend(t):
        if spend_map is None or not t.litellm_team_id:
            return None
        return spend_map.get(t.litellm_team_id, 0.0)
    team_dicts = [{**_team_to_dict(t), "spend": _spend(t), "tokens_today": _tokens_today(t),
                  "keys": keys_by_team[t.id]} for t in teams]
    known = [d["spend"] for d in team_dicts if d["spend"] is not None]
    return {
        "entitlement": None if ent is None else {
            "status": ent.status, "org_code_budget": ent.org_code_budget,
            "budget_period": ent.budget_period},
        "allocated": cp.allocated_budget(org_id),
        "org_spend": sum(known) if spend_map is not None else None,
        "teams": team_dicts,
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
def update_team(request: Request, team_id: str, body: CodeTeamUpdate,
                user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    user = require_org_admin(team.org_id, user_id)
    old_budget = team.max_budget
    from management.server.services import code_provisioning as cp
    try:
        team = cp.update_code_team_budget(code_team_id=team_id, new_budget=body.max_budget)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_TEAM_UPDATE,
                     org_id=team.org_id, resource_type="code_team", resource_id=team.id,
                     details={"name": team.name, "old_budget": old_budget, "new_budget": team.max_budget})
    return _team_to_dict(team)


@router.post("/code/teams/{team_id}/admins", status_code=status.HTTP_201_CREATED)
def add_team_admin(request: Request, team_id: str, body: CodeTeamAdminAdd,
                   user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeTeam, CodeTeamMember
    from api.db.services.user_service import UserService
    from api.db.services.org_service import OrgMemberService
    from common.misc_utils import get_uuid
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    user = require_org_admin(team.org_id, user_id)

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
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_TEAM_ADMIN_ADD,
                     org_id=team.org_id, resource_type="code_team", resource_id=team_id,
                     details={"email": body.email, "user_id": target.id, "team": team.name})
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
    try:
        key = cp.revoke_code_key(code_key_id=key_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_REVOKE,
                     org_id=None, resource_type="code_key", resource_id=key_id,
                     details={"label": key.label})
    return _key_to_dict(key)


@router.post("/code/housekeeping")
def run_housekeeping(user=Depends(require_superuser)):
    """Force un passage reconcile + snapshot (le scheduler in-process le fait toutes les heures)."""
    from management.server.services.code_housekeeping import housekeeping
    return housekeeping()


@router.post("/code/reconcile")
def reconcile(user=Depends(require_superuser)):
    """Alias rétro-compatible de /code/housekeeping."""
    from management.server.services.code_housekeeping import housekeeping
    return housekeeping()
