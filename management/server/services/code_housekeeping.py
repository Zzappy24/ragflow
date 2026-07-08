"""Housekeeping du produit Code : reconcile + snapshot de spend, + courbe quotidienne.

Appelé par le scheduler in-process (management/server/main.py) et par
POST /api/admin/code/housekeeping. Protégé par DB.lock -> multi-replica safe.
"""
import datetime
import logging

from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)


def snapshot_spend(client=None) -> int:
    """Upsert le snapshot du jour pour chaque team active. 0 si gateway down."""
    from api.db.db_models import DB, CodeTeam, CodeSpendSnapshot
    from management.server.services.code_provisioning import spend_by_litellm_team

    spend_map = spend_by_litellm_team(client=client)
    if spend_map is None:
        logger.warning("snapshot_spend: gateway injoignable, aucun snapshot écrit")
        return 0

    today = datetime.date.today()
    count = 0
    with DB.connection_context():
        teams = list(CodeTeam.select().where(
            (CodeTeam.status == "active") & (CodeTeam.litellm_team_id.is_null(False))))
        for t in teams:
            spend = spend_map.get(t.litellm_team_id, 0.0)
            (CodeSpendSnapshot.insert(
                id=get_uuid(), snap_date=today, org_id=t.org_id,
                code_team_id=t.id, spend=spend, max_budget=t.max_budget)
             .on_conflict(update={CodeSpendSnapshot.spend: spend,
                                  CodeSpendSnapshot.max_budget: t.max_budget})
             .execute())
            count += 1
    return count


def _housekeeping_impl(client=None) -> dict:
    from api.db.db_models import DB, CodeHousekeepingRun
    from management.server.services.code_reconcile import reconcile_all

    rec = reconcile_all(client=client)
    snapped = snapshot_spend(client=client)
    ran_at = datetime.datetime.now()
    with DB.connection_context():
        CodeHousekeepingRun.create(id=get_uuid(), ran_at=ran_at,
                                   teams_snapshotted=snapped,
                                   teams_synced=rec["teams_synced"],
                                   keys_synced=rec["keys_synced"], errors=rec["errors"])
    return {"teams_snapshotted": snapped, "ran_at": ran_at.isoformat(), **rec}


def housekeeping(client=None) -> dict:
    """Reconcile + snapshot, sérialisé cross-replicas par un lock DB."""
    from api.db.db_models import DB

    @DB.lock("code_housekeeping", 10)
    def _locked():
        return _housekeeping_impl(client=client)
    return _locked()


def last_run():
    from api.db.db_models import DB, CodeHousekeepingRun
    with DB.connection_context():
        return (CodeHousekeepingRun.select()
                .order_by(CodeHousekeepingRun.ran_at.desc()).first())


def daily_spend_series(org_ids: list[str] | None, days: int = 30) -> list[dict]:
    """Courbe spend/jour agrégée sur les orgs visibles. Delta négatif = reset -> valeur du jour."""
    from api.db.db_models import DB, CodeSpendSnapshot

    since = datetime.date.today() - datetime.timedelta(days=days)
    with DB.connection_context():
        q = CodeSpendSnapshot.select().where(CodeSpendSnapshot.snap_date >= since)
        if org_ids is not None:
            q = q.where(CodeSpendSnapshot.org_id.in_(org_ids))
        rows = list(q.order_by(CodeSpendSnapshot.code_team_id, CodeSpendSnapshot.snap_date))

    daily: dict[str, float] = {}
    prev_by_team: dict[str, float] = {}
    for r in rows:
        prev = prev_by_team.get(r.code_team_id)
        delta = r.spend if prev is None or r.spend < prev else r.spend - prev
        prev_by_team[r.code_team_id] = r.spend
        key = str(r.snap_date)
        daily[key] = daily.get(key, 0.0) + delta
    return [{"date": d, "spend": round(daily[d], 4)} for d in sorted(daily)]
