"""Reconciler — converges panel desired state into LiteLLM (spec §5).

Order matters: revocations/blocks FIRST (security), then team create/update,
then unfinishable pending_create keys are flagged 'error' (their plaintext
only ever existed in the lost /key/generate response — admin must recreate).
Run via POST /api/admin/code/reconcile (Task 5) — cron it in K8s later.
"""
import logging

from management.server.services.code_provisioning import _client, _mark, get_entitlement
from management.server.services.litellm_client import LiteLLMError

logger = logging.getLogger(__name__)


def reconcile_all(client=None) -> dict:
    from api.db.db_models import DB, CodeTeam, CodeKey
    cl = _client(client)
    report = {"keys_synced": 0, "teams_synced": 0, "errors": 0}

    # --- 1. security first: pending blocks/revocations/unblocks on existing keys ---
    with DB.connection_context():
        pending_keys = list(CodeKey.select().where(
            (CodeKey.sync_status == "pending") & (CodeKey.litellm_key_id.is_null(False))))
    for key in pending_keys:
        try:
            if key.status in ("revoked", "blocked"):
                cl.block_key(key.litellm_key_id)
            else:
                cl.unblock_key(key.litellm_key_id)
            _mark(CodeKey, key.id, sync_status="synced", sync_error=None)
            report["keys_synced"] += 1
        except LiteLLMError as e:
            _mark(CodeKey, key.id, sync_error=str(e)[:1000])
            report["errors"] += 1

    # --- 2. teams: create-if-missing (idempotent via alias) or push budget ---
    with DB.connection_context():
        pending_teams = list(CodeTeam.select().where(
            (CodeTeam.sync_status == "pending") & (CodeTeam.status == "active")))
    for team in pending_teams:
        ent = get_entitlement(team.org_id)
        if ent is None:
            continue
        alias = cl.team_alias(team.org_id, team.id)
        try:
            llm_id = team.litellm_team_id or cl.find_team_by_alias(alias)
            if llm_id is None:
                llm_id = cl.create_team(alias=alias, max_budget=team.max_budget,
                                        budget_duration=ent.budget_period,
                                        models=team.model_access or [])
            else:
                cl.update_team(team_id=llm_id, max_budget=team.max_budget)
            _mark(CodeTeam, team.id, litellm_team_id=llm_id,
                  sync_status="synced", sync_error=None)
            report["teams_synced"] += 1
        except LiteLLMError as e:
            _mark(CodeTeam, team.id, sync_error=str(e)[:1000])
            report["errors"] += 1

    # --- 3. unfinishable creates: key row exists but generate response was lost ---
    with DB.connection_context():
        orphan_creates = list(CodeKey.select().where(
            (CodeKey.sync_status == "pending") & (CodeKey.litellm_key_id.is_null(True)) &
            (CodeKey.status == "active")))
    for key in orphan_creates:
        _mark(CodeKey, key.id, sync_status="error",
              sync_error="generate lost mid-flight; plaintext unrecoverable — recreate the key")
        logger.warning("code_key %s flagged for manual recreate", key.id)

    return report
