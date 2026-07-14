"""Code product routes — entitlements (superuser), teams (org admin), keys (delegated)."""
import datetime
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import (
    get_current_user, get_current_user_id, require_superuser, require_org_admin,
    require_code_team_admin,
)
from management.server.models.schemas import (
    CodeEntitlementUpsert, CodeTeamCreate, CodeTeamUpdate, CodeTeamAdminAdd, CodeKeyCreate,
    CodeKeyBulkCreate, CodeKeyLimitsUpdate, CodeClaimRequest,
)
from management.server.services import audit as audit_svc
from management.server.config import settings as admin_settings

router = APIRouter()

# In-process rate limiter for the unauthenticated claim route — mono-replica
# only (no shared state across pods). Good enough for the panel's current
# single-instance deployment; move to Redis if the admin panel is ever
# scaled horizontally.
_CLAIM_HITS: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    """Resolve the source IP for rate-limiting.

    Prefers X-Real-IP: our chart's nginx sets it to `$remote_addr`
    (overwrite, trustworthy — a client-supplied X-Real-IP is always clobbered
    by the proxy). Falls back to X-Forwarded-For's first hop for other
    front-proxy setups, then to the raw socket peer.

    X-Forwarded-For is NOT trusted first: our nginx builds it via
    `proxy_add_x_forwarded_for`, which APPENDS to any client-supplied value
    instead of overwriting it. A client can prepend an arbitrary IP to XFF
    and either dodge the rate limit or exhaust a victim IP's bucket
    (adversarial 429-DoS). If the panel is ever exposed without a proxy that
    sets X-Real-IP this way, revisit — both headers become spoofable.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str, limit: int = 10, window: float = 60.0) -> bool:
    now = time.time()
    # Prune every IP's bucket on each call (not just the caller's) so the
    # dict can't grow unboundedly as more distinct source IPs hit this
    # route — an unbounded dict is itself a memory-exhaustion vector.
    for other_ip in list(_CLAIM_HITS.keys()):
        fresh = [t for t in _CLAIM_HITS[other_ip] if now - t < window]
        if fresh:
            _CLAIM_HITS[other_ip] = fresh
        else:
            del _CLAIM_HITS[other_ip]

    hits = _CLAIM_HITS.get(ip, [])
    hits.append(now)
    _CLAIM_HITS[ip] = hits
    return len(hits) > limit


def _invite_email_body(url: str, gateway_url: str | None, *, reminder: bool = False) -> str:
    from management.server.services.code_invites import INVITE_TTL_HOURS
    intro = "Rappel : vous avez été invité(e)" if reminder else "Vous avez été invité(e)"
    body = (f"{intro} à rejoindre l'espace Code.\n\n"
           f"Cliquez sur ce lien pour activer votre clé (expire dans {INVITE_TTL_HOURS} heures) :\n{url}\n")
    if gateway_url:
        body += f"\nURL de la gateway : {gateway_url}\n"
    return body


def _team_to_dict(t) -> dict:
    return {"id": t.id, "org_id": t.org_id, "name": t.name, "max_budget": t.max_budget,
            "bu": t.bu or "",
            "model_access": t.model_access or [], "status": t.status,
            "sync_status": t.sync_status, "litellm_team_id": t.litellm_team_id}


def _key_to_dict(k) -> dict:
    return {"id": k.id, "code_team_id": k.code_team_id, "label": k.label,
            "key_masked": k.key_masked, "owner_user_id": k.owner_user_id,
            "status": k.status, "sync_status": k.sync_status, "max_budget": k.max_budget, "rpm_limit": k.rpm_limit}


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

    # Today's tokens per team, read from the day's SNAPSHOT rows (DB-only).
    # No gateway call in the request path: /spend/logs is unbounded and its
    # cost grows with client traffic — the scheduler (15 min) is the sole
    # reader. No row yet today / NULL column => None (pas encore relevé).
    from management.server.services.code_housekeeping import today_usage_from_snapshots
    snap_usage = today_usage_from_snapshots([t.id for t in teams])
    def _tokens_today(t):
        return snap_usage.get(t.id, {}).get("tokens")

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
    # Today's tokens per team from the day's SNAPSHOT rows (DB-only, no
    # gateway call in the request path — /spend/logs is unbounded and its
    # cost grows with client traffic; the 15-min scheduler is the sole
    # reader). No row yet today / NULL => None (pas encore relevé).
    from management.server.services.code_housekeeping import today_usage_from_snapshots
    snap_usage = today_usage_from_snapshots([t.id for t in teams])
    def _tokens_today(t):
        return snap_usage.get(t.id, {}).get("tokens")

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
        "gateway_url": admin_settings.CODE_GATEWAY_PUBLIC_URL or None,
        "entitlement": None if ent is None else {
            "status": ent.status, "org_code_budget": ent.org_code_budget,
            "budget_period": ent.budget_period},
        "allocated": cp.allocated_budget(org_id),
        "org_spend": sum(known) if spend_map is not None else None,
        "teams": team_dicts,
    }


@router.get("/orgs/{org_id}/code/export")
def export_code_usage(org_id: str, month: str | None = None,
                      user_id: str = Depends(get_current_user_id)):
    """Export CSV mensuel de la conso Code (facturation) — une ligne par
    team et par jour depuis les snapshots. spend = cumul du cycle au moment
    du relevé ; tokens/erreurs = valeurs du jour. Séparateur ';' + BOM UTF-8
    (Excel FR). month=YYYY-MM, défaut = mois courant."""
    import csv
    import io
    import re as _re
    from fastapi.responses import Response

    require_org_admin(org_id, user_id)
    if month is None:
        month = datetime.date.today().strftime("%Y-%m")
    if not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    from api.db.db_models import DB, CodeTeam, CodeSpendSnapshot
    with DB.connection_context():
        teams_by_id = {t.id: t for t in CodeTeam.select().where(CodeTeam.org_id == org_id)}
        first = datetime.date(int(month[:4]), int(month[5:7]), 1)
        nxt = (first + datetime.timedelta(days=32)).replace(day=1)
        rows = list(CodeSpendSnapshot.select().where(
            (CodeSpendSnapshot.org_id == org_id)
            & (CodeSpendSnapshot.snap_date >= first)
            & (CodeSpendSnapshot.snap_date < nxt)
        ).order_by(CodeSpendSnapshot.code_team_id, CodeSpendSnapshot.snap_date))

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["date", "team", "bu", "depense_cumulee_cycle_eur", "budget_team_eur",
                "tokens_jour", "erreurs_jour"])
    for r in rows:
        t = teams_by_id.get(r.code_team_id)
        w.writerow([str(r.snap_date), t.name if t else r.code_team_id,
                    (t.bu or "") if t else "",
                    r.spend, r.max_budget,
                    "" if r.tokens is None else r.tokens,
                    "" if r.errors is None else r.errors])
    csv_bytes = "\ufeff" + buf.getvalue()  # BOM: Excel ouvre l'UTF-8 proprement
    return Response(content=csv_bytes, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="code-usage-{org_id[:8]}-{month}.csv"'})


@router.post("/orgs/{org_id}/code/teams", status_code=status.HTTP_201_CREATED)
def create_team(request: Request, org_id: str, body: CodeTeamCreate,
                user_id: str = Depends(get_current_user_id)):
    user = require_org_admin(org_id, user_id)
    from management.server.services import code_provisioning as cp
    try:
        team = cp.create_code_team(org_id=org_id, name=body.name, max_budget=body.max_budget,
                                   model_access=body.model_access, created_by=user.id,
                                   bu=body.bu)
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
    # Tag BU : purement local (aucune synchro gateway). None = inchangé,
    # "" = retirer — mêmes sémantiques que le tag des workspaces.
    if body.bu is not None:
        from api.db.db_models import DB as _DB
        with _DB.connection_context():
            CodeTeam.update(bu=body.bu.strip() or None).where(CodeTeam.id == team_id).execute()
            team = CodeTeam.get_by_id(team_id)
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_TEAM_UPDATE,
                     org_id=team.org_id, resource_type="code_team", resource_id=team.id,
                     details={"name": team.name, "old_budget": old_budget, "new_budget": team.max_budget})
    return _team_to_dict(team)


@router.delete("/code/teams/{team_id}")
def delete_team(request: Request, team_id: str, user_id: str = Depends(get_current_user_id)):
    """Vierge (0 clé) -> hard delete ; sinon soft-archive (clés révoquées,
    invites annulées, spend/snapshots conservés). Budget libéré immédiatement."""
    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None or team.status != "active":
        raise HTTPException(status_code=404, detail="Code team not found")
    user = require_org_admin(team.org_id, user_id)
    from management.server.services import code_provisioning as cp
    try:
        mode, _row = cp.delete_code_team(code_team_id=team_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_TEAM_DELETE,
                     org_id=team.org_id, resource_type="code_team", resource_id=team_id,
                     details={"name": team.name, "mode": mode})
    return {"team_id": team_id, "deleted": mode}


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


@router.get("/code/teams/{team_id}/admins")
def list_team_admins(team_id: str, user_id: str = Depends(get_current_user_id)):
    """Délégations actives de la team — email joint pour l'affichage."""
    from api.db.db_models import DB, CodeTeam, CodeTeamMember, User
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    require_org_admin(team.org_id, user_id)
    with DB.connection_context():
        rows = list(CodeTeamMember.select(CodeTeamMember, User.email)
                    .join(User, on=(CodeTeamMember.user_id == User.id))
                    .where(CodeTeamMember.code_team_id == team_id))
    return [{"user_id": r.user_id, "email": r.user.email, "role": r.role} for r in rows]


@router.delete("/code/teams/{team_id}/admins/{target_user_id}")
def remove_team_admin(request: Request, team_id: str, target_user_id: str,
                      user_id: str = Depends(get_current_user_id)):
    """Révoque une délégation. La cible reperd immédiatement l'accès de
    gestion (les dépendances RBAC relisent code_team_member à chaque requête,
    aucun cache) ; ses clés/sièges éventuels ne sont PAS touchés."""
    from api.db.db_models import DB, CodeTeam, CodeTeamMember
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    user = require_org_admin(team.org_id, user_id)
    with DB.connection_context():
        deleted = CodeTeamMember.delete().where(
            (CodeTeamMember.code_team_id == team_id)
            & (CodeTeamMember.user_id == target_user_id)).execute()
    if not deleted:
        raise HTTPException(status_code=404, detail="No delegation for this user on this team")
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_TEAM_ADMIN_REMOVE,
                     org_id=team.org_id, resource_type="code_team", resource_id=team_id,
                     details={"user_id": target_user_id, "team": team.name})
    return {"code_team_id": team_id, "user_id": target_user_id, "removed": True}


@router.post("/code/teams/{team_id}/keys", status_code=status.HTTP_201_CREATED)
def create_key(request: Request, team_id: str, body: CodeKeyCreate,
               user=Depends(require_code_team_admin)):
    from management.server.services import code_provisioning as cp
    try:
        key, plain = cp.create_code_key(code_team_id=team_id, label=body.label,
                                        owner_user_id=body.owner_user_id, created_by=user.id,
                                        max_budget=body.max_budget, rpm_limit=body.rpm_limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_CREATE,
                     org_id=None, resource_type="code_key", resource_id=key.id,
                     details={"label": body.label, "team_id": team_id})
    # plain_key is returned EXACTLY once, never persisted, never logged
    return {"key": _key_to_dict(key), "plain_key": plain}


@router.post("/code/teams/{team_id}/keys/bulk", status_code=status.HTTP_201_CREATED)
async def bulk_invite_keys(request: Request, team_id: str, body: CodeKeyBulkCreate,
                           user=Depends(require_code_team_admin)):
    """Bulk seat invites: one CodeKeyInvite per email, no CodeKey created yet
    (the seat is provisioned at claim time). Best-effort email per invite —
    email_sent tells the admin whether they need to hand out the link
    themselves (claim_url is only returned in that case, see item shaping below)."""
    from management.server.services import code_invites as ci
    from management.server.services.mailer import send_mail

    try:
        invites = ci.create_invites(code_team_id=team_id, emails=body.emails, created_by=user.id,
                                    max_budget=body.max_budget, rpm_limit=body.rpm_limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    gateway_url = admin_settings.CODE_GATEWAY_PUBLIC_URL or None
    out = []
    for inv in invites:
        url = ci.claim_url(inv["claim_token"])
        # PANEL_PUBLIC_URL vide => le lien de claim serait RELATIF, donc cassé
        # dans un email. On n'envoie pas : l'admin reçoit le lien en fallback
        # (le front l'absolutise avec window.location.origin).
        email_sent = bool(admin_settings.PANEL_PUBLIC_URL) and await send_mail(inv["email"], "Invitation — Code product",
                                     _invite_email_body(url, gateway_url))
        item = {"email": inv["email"], "invite_id": inv["invite_id"], "email_sent": email_sent}
        if not email_sent:
            item["claim_url"] = url
        out.append(item)
        audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_SEAT_INVITE,
                         org_id=None, resource_type="code_key_invite", resource_id=inv["invite_id"],
                         details={"email": inv["email"], "team_id": team_id, "email_sent": email_sent})
    return out


@router.get("/code/teams/{team_id}/invites")
def list_invites(team_id: str, user=Depends(require_code_team_admin)):
    """Pending (unclaimed) invites for a team — no token, no claim_url."""
    from api.db.db_models import DB, CodeKeyInvite
    with DB.connection_context():
        rows = list(CodeKeyInvite.select().where(
            (CodeKeyInvite.code_team_id == team_id) & (CodeKeyInvite.claimed_key_id.is_null(True))))
    return [{"id": r.id, "email": r.email, "expires_at": r.expires_at.isoformat(),
            "created_by": r.created_by} for r in rows]


@router.post("/code/invites/{invite_id}/resend")
async def resend_invite(request: Request, invite_id: str, user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeKeyInvite
    with DB.connection_context():
        inv = CodeKeyInvite.get_or_none(CodeKeyInvite.id == invite_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    user = require_code_team_admin(inv.code_team_id, user_id)
    if inv.claimed_key_id is not None:
        raise HTTPException(status_code=409, detail="Invite already claimed")

    from management.server.services import code_invites as ci
    from management.server.services.mailer import send_mail

    token = ci.regenerate_token(invite_id)
    if token is None:
        # Raced with a concurrent claim/resend between the lookup above and here.
        raise HTTPException(status_code=409, detail="Invite already claimed")
    url = ci.claim_url(token)
    email_sent = bool(admin_settings.PANEL_PUBLIC_URL) and await send_mail(inv.email, "Rappel — Invitation Code product",
                                 _invite_email_body(url, admin_settings.CODE_GATEWAY_PUBLIC_URL or None,
                                                    reminder=True))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_SEAT_INVITE,
                     org_id=None, resource_type="code_key_invite", resource_id=invite_id,
                     details={"email": inv.email, "resend": True, "email_sent": email_sent})
    item = {"invite_id": invite_id, "email": inv.email, "email_sent": email_sent}
    if not email_sent:
        item["claim_url"] = url
    return item


@router.delete("/code/invites/{invite_id}")
def cancel_invite(request: Request, invite_id: str, user_id: str = Depends(get_current_user_id)):
    """Annule une invitation NON consommée : le lien tombe en 404 immédiatement.
    Le DELETE est conditionné à claimed_key_id IS NULL — une invite consommée
    (ou un claim qui gagne la course) n'est jamais effacée (trace d'audit)."""
    from api.db.db_models import DB, CodeKeyInvite
    with DB.connection_context():
        inv = CodeKeyInvite.get_or_none(CodeKeyInvite.id == invite_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    user = require_code_team_admin(inv.code_team_id, user_id)
    with DB.connection_context():
        deleted = CodeKeyInvite.delete().where(
            (CodeKeyInvite.id == invite_id)
            & (CodeKeyInvite.claimed_key_id.is_null(True))).execute()
    if not deleted:
        raise HTTPException(status_code=409, detail="Invite already claimed — revoke the key instead")
    audit_svc.record(request=request, actor_user_id=user.id,
                     action=audit_svc.CODE_SEAT_INVITE_CANCELLED,
                     org_id=None, resource_type="code_key_invite", resource_id=invite_id,
                     details={"email": inv.email, "team_id": inv.code_team_id})
    return {"invite_id": invite_id, "cancelled": True}


@router.post("/code/teams/{team_id}/invites/resend-all")
async def resend_all_invites(request: Request, team_id: str, user=Depends(require_code_team_admin)):
    """Re-mint + renvoie TOUTES les invitations pendantes de la team (y compris
    expirées — regenerate_token rafraîchit le TTL, c'est le cas d'usage : un
    lot envoyé vendredi et mort lundi, ou un SMTP en panne au premier envoi)."""
    from api.db.db_models import DB, CodeKeyInvite
    from management.server.services import code_invites as ci
    from management.server.services.mailer import send_mail

    with DB.connection_context():
        rows = list(CodeKeyInvite.select().where(
            (CodeKeyInvite.code_team_id == team_id) & (CodeKeyInvite.claimed_key_id.is_null(True))))

    results = []
    for inv in rows:
        token = ci.regenerate_token(inv.id)
        if token is None:  # claimed entre-temps — on l'ignore proprement
            continue
        url = ci.claim_url(token)
        email_sent = bool(admin_settings.PANEL_PUBLIC_URL) and await send_mail(
            inv.email, "Rappel — Invitation Code product",
            _invite_email_body(url, admin_settings.CODE_GATEWAY_PUBLIC_URL or None, reminder=True))
        item = {"invite_id": inv.id, "email": inv.email, "email_sent": email_sent}
        if not email_sent:
            item["claim_url"] = url
        results.append(item)

    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_SEAT_INVITE,
                     org_id=None, resource_type="code_team", resource_id=team_id,
                     details={"resend_all": True, "count": len(results)})
    return results


@router.put("/code/keys/{key_id}")
def update_key_limits(request: Request, key_id: str, body: CodeKeyLimitsUpdate,
                      user_id: str = Depends(get_current_user_id)):
    """Modifie les limites d'une clé VIVANTE (le secret ne change pas —
    pour changer le secret, c'est la rotation). null = supprime la limite."""
    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_or_none(CodeKey.id == key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    user = require_code_team_admin(key.code_team_id, user_id)
    from management.server.services import code_provisioning as cp
    try:
        key = cp.update_code_key_limits(code_key_id=key_id, max_budget=body.max_budget,
                                        rpm_limit=body.rpm_limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_UPDATE,
                     org_id=None, resource_type="code_key", resource_id=key_id,
                     details={"label": key.label, "max_budget": body.max_budget,
                              "rpm_limit": body.rpm_limit})
    return _key_to_dict(key)


@router.post("/code/keys/{key_id}/rotate", status_code=status.HTTP_201_CREATED)
async def rotate_key(request: Request, key_id: str, user_id: str = Depends(get_current_user_id)):
    """Revoke the existing key and re-invite the same email (a fresh seat is
    provisioned at claim time, never re-using the revoked key's secret).

    Order matters: validate the label -> create the replacement invite ->
    ONLY THEN revoke the existing key -> email -> audit. A key must never be
    destroyed before we know a valid replacement invite can be created
    (invalid label, or invite creation failing for any reason) — the old
    key stays active and untouched in both of those cases."""
    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_or_none(CodeKey.id == key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    user = require_code_team_admin(key.code_team_id, user_id)

    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci
    from management.server.services.mailer import send_mail

    try:
        ci.validate_email(key.label)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"key label is not a valid email address: {key.label}")

    try:
        invites = ci.create_invites(code_team_id=key.code_team_id, emails=[key.label],
                                    created_by=user.id,
                                    max_budget=key.max_budget, rpm_limit=key.rpm_limit)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    inv = invites[0]

    try:
        cp.revoke_code_key(code_key_id=key_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_ROTATED,
                     org_id=None, resource_type="code_key", resource_id=key_id,
                     details={"label": key.label})

    url = ci.claim_url(inv["claim_token"])
    email_sent = bool(admin_settings.PANEL_PUBLIC_URL) and await send_mail(inv["email"], "Nouvelle invitation — Code product (rotation de clé)",
                                 _invite_email_body(url, admin_settings.CODE_GATEWAY_PUBLIC_URL or None))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_SEAT_INVITE,
                     org_id=None, resource_type="code_key_invite", resource_id=inv["invite_id"],
                     details={"email": inv["email"], "team_id": key.code_team_id,
                              "rotated_from_key": key_id, "email_sent": email_sent})
    item = {"invite_id": inv["invite_id"], "email": inv["email"], "email_sent": email_sent,
           "revoked_key_id": key_id}
    if not email_sent:
        item["claim_url"] = url
    return item


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


@router.post("/public/code/claim")
async def public_claim(request: Request, body: CodeClaimRequest):
    """Unauthenticated: the invite link itself is the credential. Rate-limited
    per source IP (mono-replica, in-process — see _CLAIM_HITS). Errors are
    intentionally generic (404/503) — never distinguish "unknown token" from
    "expired" or "already claimed" to avoid token enumeration."""
    ip = _client_ip(request)
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="Too many attempts, please retry later")

    from management.server.services import code_invites as ci
    try:
        result = ci.claim(body.token)
    except ci.InviteNotFound:
        raise HTTPException(status_code=404, detail="Invite not found or expired")
    except ci.GatewayDown:
        raise HTTPException(status_code=503, detail="Gateway unavailable, please retry")

    # No audit of the token itself — only the resulting identity/email and
    # the invite it resolved to.
    audit_svc.record(request=request, actor_user_id="", action=audit_svc.CODE_SEAT_CLAIMED,
                     org_id=None, resource_type="code_key_invite", resource_id=result["invite_id"],
                     details={"email": result["email"]})
    # Best-effort : les noms publics des modèles alimentent la section
    # « bien démarrer » (config Kilo/OpenCode/Cline) de la page de claim.
    # Un échec ici ne doit jamais faire échouer un claim réussi.
    models: list[str] = []
    try:
        from management.server.services.code_provisioning import _client
        models = _client().list_models()
    except Exception:
        pass
    return {"models": models,
            "plain_key": result["plain_key"], "label": result["label"],
            "gateway_url": admin_settings.CODE_GATEWAY_PUBLIC_URL or None}


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
