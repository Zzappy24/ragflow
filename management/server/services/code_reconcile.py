"""Reconciler — converges panel desired state into LiteLLM (spec §5).

Order matters: revocations/blocks FIRST (security), then team create/update,
then unfinishable pending_create keys are flagged 'error' (their plaintext
only ever existed in the lost /key/generate response — admin must recreate).
Run via POST /api/admin/code/reconcile (Task 5) — cron it in K8s later.

Notes / known limitations:
- Phase 2 skips teams whose org entitlement is missing OR not "active": a
  suspended org's pending teams stay pending and converge once the org is
  reactivated (we don't want to create LiteLLM teams for a suspended org).
- Phase 3 catches ANY key with sync_status=="pending" and litellm_key_id
  IS NULL, regardless of the key's own status. A key whose create failed
  (litellm_key_id=None) can later be swept into status="blocked"/"revoked"
  by an org-suspension fan-out (upsert_entitlement) — it must still be
  flagged here, or it would never match phase 1 (needs litellm_key_id) nor
  the old phase-3 predicate (needed status=="active") and would be stuck
  forever with no admin visibility.
- reconcile_all() assumes a single concurrent runner (single-replica
  CronJob). There is no cross-run lock; overlapping runs could double-create
  a team despite the alias lookup being racy across processes.
- Phase 3's IS NULL check can flag a create that is genuinely in-flight
  (another thread mid-way through create_code_key, between the desired-state
  insert and the /key/generate call) during its seconds-wide window. Fine at
  minutes-cadence cron; if hit, create_code_key's later _mark overwrites the
  row and the 'error' flag needs manual cleanup — rare in practice.
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
        if ent is None or ent.status != "active":
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
            (CodeKey.sync_status == "pending") & (CodeKey.litellm_key_id.is_null(True))))
    for key in orphan_creates:
        _mark(CodeKey, key.id, sync_status="error",
              sync_error="generate lost mid-flight; plaintext unrecoverable — recreate the key")
        report["errors"] += 1
        logger.warning("code_key %s flagged for manual recreate", key.id)

    # --- 4. alias-diff sweep: gateway -> panel (l'inverse des phases 1-3) ---
    try:
        sweep = sweep_gateway_orphans(client=cl)
        report.update(sweep)
    except LiteLLMError as e:
        logger.warning("alias-diff sweep skipped (gateway): %s", e)
        report["errors"] += 1

    return report


def _parse_alias(alias: str, kind: str) -> str | None:
    """Extrait l'id local d'un alias déterministe 'org:<org>:team|key:<id>'.
    None si l'alias ne suit pas notre convention (objet étranger — jamais touché)."""
    parts = (alias or "").split(":")
    if len(parts) == 4 and parts[0] == "org" and parts[2] == kind and parts[3]:
        return parts[3]
    return None


def sweep_gateway_orphans(client=None) -> dict:
    """Balayage d'intégrité gateway -> panel, sur NOS objets uniquement
    (identifiés par la convention d'alias). Couvre les angles morts du
    desired-state (create réussi côté LiteLLM mais réponse perdue avant
    l'écriture locale, double-création par race du reconciler, claim
    re-tenté après sentinel stale) :

    - clé avec notre alias mais SANS row locale -> /key/block (une clé sans
      gouvernance panel est un accès fantôme ; le plaintext est peut-être
      distribué). Comptée dans orphan_keys_blocked.
    - clé dont la row locale dit revoked/blocked mais que la gateway liste
      débloquée -> /key/block (re-convergence sécurité).
    - team avec notre alias mais sans row locale -> comptée (orphan_teams)
      et loggée, JAMAIS supprimée automatiquement (le spend upstream est de
      l'historique de facturation ; décision humaine).

    Les objets sans notre convention d'alias ne sont jamais touchés.
    """
    from api.db.db_models import DB, CodeTeam, CodeKey
    cl = _client(client)
    report = {"orphan_teams": 0, "orphan_keys_blocked": 0}

    gateway_teams = cl.list_teams()

    with DB.connection_context():
        known_team_ids = {t.id for t in CodeTeam.select(CodeTeam.id)}
        active_llm_ids = {t.litellm_team_id for t in CodeTeam.select(CodeTeam.litellm_team_id)
                          .where((CodeTeam.status == "active")
                                 & (CodeTeam.litellm_team_id.is_null(False)))}

    for gt in gateway_teams:
        local_id = _parse_alias(gt.get("team_alias"), "team")
        if local_id is None:
            continue  # objet étranger (autre produit / manuel) — pas à nous
        if local_id not in known_team_ids:
            report["orphan_teams"] += 1
            logger.warning("alias-diff: team LiteLLM %s (alias %s) sans row panel — "
                           "spend upstream conservé, traiter manuellement",
                           gt.get("team_id"), gt.get("team_alias"))

    # Clés : un /key/list par team ACTIVE connue (les teams archivées ont
    # déjà toutes leurs clés révoquées par delete_code_team).
    for llm_team_id in active_llm_ids:
        try:
            gateway_keys = cl.list_keys(llm_team_id)
        except LiteLLMError as e:
            logger.warning("alias-diff: key list indisponible pour %s: %s", llm_team_id, e)
            continue
        for gk in gateway_keys:
            local_id = _parse_alias(gk.get("key_alias"), "key")
            if local_id is None:
                continue
            with DB.connection_context():
                row = CodeKey.get_or_none(CodeKey.id == local_id)
            should_block = (row is None
                            or (row.status in ("revoked", "blocked")
                                and gk.get("blocked") is not True))
            if not should_block:
                continue
            try:
                cl.block_key(gk.get("token"))
                report["orphan_keys_blocked"] += 1
                logger.warning("alias-diff: clé %s (%s) bloquée — %s",
                               gk.get("key_alias"), gk.get("token"),
                               "aucune row panel" if row is None
                               else "révoquée localement mais active gateway")
            except LiteLLMError as e:
                logger.warning("alias-diff: block failed for %s: %s", gk.get("token"), e)

    return report
