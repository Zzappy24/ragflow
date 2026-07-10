"""Housekeeping du produit Code : reconcile + snapshot de spend, + courbe quotidienne.

Appelé par le scheduler in-process (management/server/main.py) et par
POST /api/admin/code/housekeeping. Protégé par DB.lock -> multi-replica safe.
"""
import datetime
import logging

from playhouse.pool import PooledMySQLDatabase

from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)


def snapshot_spend(client=None) -> int | None:
    """Upsert le snapshot du jour pour chaque team active. None si gateway down."""
    from api.db.db_models import DB, CodeTeam, CodeSpendSnapshot
    from management.server.services.code_provisioning import _client, spend_by_litellm_team
    from management.server.services.litellm_client import LiteLLMError

    spend_map = spend_by_litellm_team(client=client)
    if spend_map is None:
        logger.warning("snapshot_spend: gateway injoignable, aucun snapshot écrit")
        return None

    today = datetime.datetime.now(datetime.timezone.utc).date()
    try:
        usage = _client(client).daily_usage(today)
    except LiteLLMError as e:
        logger.warning("snapshot_spend: usage (tokens/erreurs) indisponible: %s", e)
        usage = None

    count = 0
    with DB.connection_context():
        teams = list(CodeTeam.select().where(
            (CodeTeam.status == "active") & (CodeTeam.litellm_team_id.is_null(False))))
        for t in teams:
            if t.litellm_team_id not in spend_map:
                # The gateway's own team listing doesn't mention this team
                # (deleted out-of-band, a transient listing gap, or — in
                # tests — a stand-in client that only knows about the teams
                # it created). Writing spend=0.0 here would fabricate a
                # reading and clobber the team's real cumulative history;
                # skip it, same principle as spend_map is None distinguishing
                # "gateway unreachable" from "genuinely zero" above, just at
                # per-team granularity.
                continue
            spend = spend_map[t.litellm_team_id]
            # usage is None -> gateway unreachable for spend-logs -> NULL (unknown).
            # usage is a dict but the team is absent from it -> reachable, no
            # traffic today -> 0 (a real, known zero). These are different facts;
            # do not collapse them.
            u = (usage or {}).get(t.litellm_team_id)
            tokens = u["tokens"] if u else (0 if usage is not None else None)
            errors = u["errors"] if u else (0 if usage is not None else None)
            update = {
                CodeSpendSnapshot.spend: spend,
                CodeSpendSnapshot.max_budget: t.max_budget,
            }
            # Only touch tokens/errors on the UPDATE path when this run has
            # fresh usage data. usage is None means daily_usage() itself
            # failed (transient spend-logs outage) — omitting the columns
            # here means ON DUPLICATE KEY / ON CONFLICT leaves the existing
            # row's tokens/errors untouched instead of clobbering a value a
            # prior, successful run in the same day already captured back to
            # NULL. The INSERT branch below still writes tokens=None for a
            # brand new row on a day where the very first run has no usage.
            if usage is not None:
                update[CodeSpendSnapshot.tokens] = tokens
                update[CodeSpendSnapshot.errors] = errors
            insert = CodeSpendSnapshot.insert(
                id=get_uuid(), snap_date=today, org_id=t.org_id,
                code_team_id=t.id, spend=spend, max_budget=t.max_budget,
                tokens=tokens, errors=errors)
            # Cross-DB on_conflict: MySQL infers the conflicting unique key from
            # the row, Postgres requires an explicit conflict_target. Mirrors
            # api/db/db_utils.py:bulk_insert_into_db.
            if isinstance(DB, PooledMySQLDatabase):
                insert = insert.on_conflict(update=update)
            else:
                insert = insert.on_conflict(
                    conflict_target=(CodeSpendSnapshot.snap_date, CodeSpendSnapshot.code_team_id),
                    update=update)
            insert.execute()
            count += 1
    return count


def _housekeeping_impl(client=None) -> dict:
    from api.db.db_models import DB, CodeHousekeepingRun
    from management.server.services.code_reconcile import reconcile_all

    rec = reconcile_all(client=client)
    snapped = snapshot_spend(client=client)
    # snapped is None when the gateway is unreachable — that must be visible
    # in the run row as an error, not silently reported as teams_snapshotted=0
    # (which is indistinguishable from "no active teams").
    teams_snapshotted = snapped if snapped is not None else 0
    errors = rec["errors"] + (1 if snapped is None else 0)
    ran_at = datetime.datetime.now(datetime.timezone.utc)
    run_id = get_uuid()
    with DB.connection_context():
        CodeHousekeepingRun.create(id=run_id, ran_at=ran_at,
                                   teams_snapshotted=teams_snapshotted,
                                   teams_synced=rec["teams_synced"],
                                   keys_synced=rec["keys_synced"], errors=errors)
    return {"run_id": run_id, "teams_snapshotted": teams_snapshotted,
            "keys_synced": rec["keys_synced"], "teams_synced": rec["teams_synced"],
            "errors": errors, "ran_at": ran_at.isoformat()}


def housekeeping(client=None) -> dict:
    """Reconcile + snapshot, sérialisé cross-replicas par un lock DB."""
    from api.db.db_models import DB

    @DB.lock("code_housekeeping", 10)
    def _locked():
        return _housekeeping_impl(client=client)
    # The lock's GET_LOCK/RELEASE_LOCK calls execute_sql() directly (bypassing
    # the ORM), which checks out a thread-local pooled connection that nothing
    # else closes when this runs off the main request thread (scheduler /
    # to_thread). Wrapping in connection_context() ensures it's returned to
    # the pool once the call completes.
    with DB.connection_context():
        return _locked()


def last_run():
    from api.db.db_models import DB, CodeHousekeepingRun
    with DB.connection_context():
        return (CodeHousekeepingRun.select()
                .order_by(CodeHousekeepingRun.ran_at.desc(),
                         CodeHousekeepingRun.id.desc()).first())


def today_usage_from_snapshots(code_team_ids: list[str]) -> dict[str, dict]:
    """Tokens/erreurs du jour (UTC) par code_team_id, lus depuis les snapshots.

    Lecture DB pure pour le chemin requête (dashboard/overview) : aucun appel
    gateway — /spend/logs est non-borné et son coût croît avec le trafic des
    clients ; seul le scheduler (15 min) le lit. Une team sans row aujourd'hui
    (pas encore relevée) est absente du résultat => les appelants rendent None.
    Colonnes NULL (usage indisponible au relevé) => valeurs None.
    """
    if not code_team_ids:
        return {}
    from api.db.db_models import DB, CodeSpendSnapshot
    today = datetime.datetime.now(datetime.timezone.utc).date()
    with DB.connection_context():
        rows = list(CodeSpendSnapshot.select().where(
            (CodeSpendSnapshot.snap_date == today) &
            (CodeSpendSnapshot.code_team_id.in_(code_team_ids))))
    return {r.code_team_id: {"tokens": r.tokens, "errors": r.errors} for r in rows}


def daily_spend_series(org_ids: list[str] | None, days: int = 30) -> list[dict]:
    """Courbe spend/jour agrégée sur les orgs visibles. Delta négatif = reset -> valeur du jour."""
    from api.db.db_models import DB, CodeSpendSnapshot

    since = datetime.date.today() - datetime.timedelta(days=days)
    with DB.connection_context():
        # Seed prev_by_team with each team's last snapshot strictly BEFORE the
        # window: without this, the first in-window row for a team older than
        # `since` has no `prev` and its full cumulative value is emitted as
        # that day's delta instead of the actual day-over-day increment.
        prev_q = CodeSpendSnapshot.select().where(CodeSpendSnapshot.snap_date < since)
        if org_ids is not None:
            prev_q = prev_q.where(CodeSpendSnapshot.org_id.in_(org_ids))
        prev_rows = list(prev_q.order_by(CodeSpendSnapshot.code_team_id, CodeSpendSnapshot.snap_date))

        q = CodeSpendSnapshot.select().where(CodeSpendSnapshot.snap_date >= since)
        if org_ids is not None:
            q = q.where(CodeSpendSnapshot.org_id.in_(org_ids))
        rows = list(q.order_by(CodeSpendSnapshot.code_team_id, CodeSpendSnapshot.snap_date))

    prev_by_team: dict[str, float] = {}
    for r in prev_rows:
        prev_by_team[r.code_team_id] = r.spend

    daily: dict[str, float] = {}
    # None-safe per-day sums: a day stays None while every row seen so far had
    # NULL tokens/errors (usage unavailable at snapshot time); it becomes a
    # real running total as soon as one non-NULL row for that day is seen.
    daily_tokens: dict[str, int | None] = {}
    daily_errors: dict[str, int | None] = {}
    for r in rows:
        prev = prev_by_team.get(r.code_team_id)
        delta = r.spend if prev is None or r.spend < prev else r.spend - prev
        prev_by_team[r.code_team_id] = r.spend
        key = str(r.snap_date)
        daily[key] = daily.get(key, 0.0) + delta
        if r.tokens is not None:
            daily_tokens[key] = (daily_tokens.get(key) or 0) + r.tokens
        elif key not in daily_tokens:
            daily_tokens[key] = None
        if r.errors is not None:
            daily_errors[key] = (daily_errors.get(key) or 0) + r.errors
        elif key not in daily_errors:
            daily_errors[key] = None
    return [{"date": d, "spend": round(daily[d], 4),
             "tokens": daily_tokens.get(d), "errors": daily_errors.get(d)}
            for d in sorted(daily)]
