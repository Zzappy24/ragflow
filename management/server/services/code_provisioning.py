"""Code product provisioning — panel is the source of truth, LiteLLM the enforcer.

Desired-state-first (spec §5): every mutation persists the DESIRED state in
MariaDB inside a transaction (status + sync_status='pending'), then tries the
LiteLLM call synchronously. Success -> sync_status='synced'. LiteLLM down ->
row stays 'pending' and the reconciler (code_reconcile.py) converges later.

Allocation invariant (spec §3): sum(active team budgets) <= org_code_budget.
Team budget_duration is ALWAYS entitlement.budget_period (cycle alignment).
"""
import logging

from common.misc_utils import get_uuid
from management.server.services.litellm_client import LiteLLMClient, LiteLLMError

logger = logging.getLogger(__name__)


def _client(client=None) -> LiteLLMClient:
    return client if client is not None else LiteLLMClient()


def get_entitlement(org_id: str):
    from api.db.db_models import DB, CodeEntitlement
    with DB.connection_context():
        return CodeEntitlement.get_or_none(CodeEntitlement.org_id == org_id)


def _allocated_budget_locked(org_id: str, exclude_team_id: str | None = None) -> float:
    """Same computation as allocated_budget(), but assumes the caller already
    holds a connection/transaction context (used inside a locked DB.atomic()
    block so the read is consistent with the FOR UPDATE lock on the org's
    entitlement row)."""
    from api.db.db_models import CodeTeam
    from peewee import fn
    q = CodeTeam.select(fn.COALESCE(fn.SUM(CodeTeam.max_budget), 0.0)).where(
        (CodeTeam.org_id == org_id) & (CodeTeam.status == "active"))
    if exclude_team_id:
        q = q.where(CodeTeam.id != exclude_team_id)
    return float(q.scalar() or 0.0)


def allocated_budget(org_id: str, exclude_team_id: str | None = None) -> float:
    from api.db.db_models import DB
    with DB.connection_context():
        return _allocated_budget_locked(org_id, exclude_team_id)


def upsert_entitlement(*, org_id: str, status: str, org_code_budget: float,
                       budget_period: str, actor_id: str, client=None):
    """Create/update the org's entitlement. Suspension fans out key blocks."""
    from api.db.db_models import DB, CodeEntitlement, CodeTeam, CodeKey
    if status not in ("active", "suspended"):
        raise ValueError(f"invalid status: {status}")

    with DB.connection_context():
        with DB.atomic():
            # Lock the org's entitlement row first so concurrent mutators
            # (create/update team, upsert entitlement) for this org queue up
            # instead of racing on the allocation-invariant check.
            row = CodeEntitlement.select().where(
                CodeEntitlement.org_id == org_id).for_update().first()
            if org_code_budget < _allocated_budget_locked(org_id):
                raise ValueError("org_code_budget below current allocation — reduce team budgets first")

            was_active = row.status == "active" if row else True
            if row is None:
                row = CodeEntitlement.create(id=get_uuid(), org_id=org_id, status=status,
                                             org_code_budget=org_code_budget,
                                             budget_period=budget_period, created_by=actor_id)
            else:
                CodeEntitlement.update(status=status, org_code_budget=org_code_budget,
                                       budget_period=budget_period).where(
                    CodeEntitlement.id == row.id).execute()
                row = CodeEntitlement.get_by_id(row.id)

            # Fan-out on transition (desired state first, then best-effort sync)
            desired_key_status = None
            if was_active and status == "suspended":
                desired_key_status = "blocked"
            elif not was_active and status == "active":
                desired_key_status = "active"
            if desired_key_status is not None:
                team_ids = [t.id for t in CodeTeam.select(CodeTeam.id).where(
                    (CodeTeam.org_id == org_id) & (CodeTeam.status == "active"))]
                if team_ids:
                    # only touch keys not individually revoked
                    CodeKey.update(status=desired_key_status, sync_status="pending").where(
                        (CodeKey.code_team_id.in_(team_ids)) & (CodeKey.status != "revoked")).execute()

    if desired_key_status is not None:
        _sync_pending_keys_for_org(org_id, client=client)
    return get_entitlement(org_id)


def _sync_pending_keys_for_org(org_id: str, client=None) -> None:
    """Best-effort immediate convergence of pending key blocks/unblocks."""
    from api.db.db_models import DB, CodeTeam, CodeKey
    cl = _client(client)
    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select(CodeTeam.id).where(CodeTeam.org_id == org_id)]
        pending = list(CodeKey.select().where(
            (CodeKey.code_team_id.in_(team_ids)) & (CodeKey.sync_status == "pending") &
            (CodeKey.litellm_key_id.is_null(False)))) if team_ids else []
    for key in pending:
        try:
            if key.status in ("blocked", "revoked"):
                cl.block_key(key.litellm_key_id)
            else:
                cl.unblock_key(key.litellm_key_id)
            _mark(key.__class__, key.id, sync_status="synced", sync_error=None)
        except LiteLLMError as e:
            logger.warning("key %s sync deferred: %s", key.id, e)
            _mark(key.__class__, key.id, sync_error=str(e)[:1000])


def _mark(model, row_id: str, **fields) -> None:
    from api.db.db_models import DB
    with DB.connection_context():
        model.update(**fields).where(model.id == row_id).execute()


def create_code_team(*, org_id: str, name: str, max_budget: float,
                     model_access: list[str], created_by: str, client=None):
    from api.db.db_models import DB, CodeEntitlement, CodeTeam
    if max_budget <= 0:
        raise ValueError("max_budget must be > 0")

    team_id = get_uuid()
    with DB.connection_context():
        with DB.atomic():
            # Lock the org's entitlement row first so concurrent team
            # creates/updates for this org queue up on the row lock instead
            # of racing on the allocation-invariant check.
            ent = CodeEntitlement.select().where(
                CodeEntitlement.org_id == org_id).for_update().first()
            if ent is None or ent.status != "active":
                raise ValueError("code entitlement is not active for this org")
            allocated = _allocated_budget_locked(org_id)
            if allocated + max_budget > ent.org_code_budget:
                raise ValueError(
                    f"allocation exceeded: {allocated} + {max_budget} "
                    f"> org budget {ent.org_code_budget}")
            # desired state FIRST
            CodeTeam.create(id=team_id, org_id=org_id, name=name, max_budget=max_budget,
                            model_access=model_access or [], status="active",
                            sync_status="pending", created_by=created_by)

    cl = _client(client)
    alias = cl.team_alias(org_id, team_id)
    try:
        llm_team_id = cl.find_team_by_alias(alias) or cl.create_team(
            alias=alias, max_budget=max_budget,
            budget_duration=ent.budget_period, models=model_access or [])
        _mark(CodeTeam, team_id, litellm_team_id=llm_team_id, sync_status="synced", sync_error=None)
    except LiteLLMError as e:
        logger.warning("code_team %s creation deferred to reconciler: %s", team_id, e)
        _mark(CodeTeam, team_id, sync_error=str(e)[:1000])

    with DB.connection_context():
        return CodeTeam.get_by_id(team_id)


def update_code_team_budget(*, code_team_id: str, new_budget: float, client=None):
    from api.db.db_models import DB, CodeEntitlement, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == code_team_id)
    if team is None or team.status != "active":
        raise ValueError("code team not found")
    if new_budget <= 0:
        raise ValueError("max_budget must be > 0")

    with DB.connection_context():
        with DB.atomic():
            # Lock the org's entitlement row first so concurrent team
            # creates/updates for this org queue up on the row lock instead
            # of racing on the allocation-invariant check.
            ent = CodeEntitlement.select().where(
                CodeEntitlement.org_id == team.org_id).for_update().first()
            if ent is None:
                raise ValueError("no entitlement for org")
            allocated = _allocated_budget_locked(team.org_id, exclude_team_id=code_team_id)
            if allocated + new_budget > ent.org_code_budget:
                raise ValueError("allocation exceeded")
            CodeTeam.update(max_budget=new_budget, sync_status="pending").where(
                CodeTeam.id == code_team_id).execute()

    cl = _client(client)
    try:
        if team.litellm_team_id:
            cl.update_team(team_id=team.litellm_team_id, max_budget=new_budget)
            _mark(CodeTeam, code_team_id, sync_status="synced", sync_error=None)
    except LiteLLMError as e:
        _mark(CodeTeam, code_team_id, sync_error=str(e)[:1000])

    with DB.connection_context():
        return CodeTeam.get_by_id(code_team_id)


def create_code_key(*, code_team_id: str, label: str, owner_user_id: str | None,
                    created_by: str, client=None):
    """Returns (row, plain_key). plain_key is None when LiteLLM is down —
    the UI must tell the admin to retry (a pending_create key can NOT be
    completed by the reconciler: the plaintext only exists in the generate
    response, so the reconciler marks such rows sync_status='error' instead)."""
    from api.db.db_models import DB, CodeTeam, CodeKey
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == code_team_id)
    if team is None or team.status != "active":
        raise ValueError("code team not found")
    ent = get_entitlement(team.org_id)
    if ent is None or ent.status != "active":
        raise ValueError("code entitlement is not active for this org")
    if team.litellm_team_id is None:
        raise ValueError("team not yet synced to LiteLLM — retry in a moment")

    key_id = get_uuid()
    with DB.connection_context():  # desired state FIRST
        CodeKey.create(id=key_id, code_team_id=code_team_id, label=label,
                       owner_user_id=owner_user_id, status="active",
                       sync_status="pending", created_by=created_by)

    cl = _client(client)
    try:
        out = cl.generate_key(team_id=team.litellm_team_id,
                              alias=cl.key_alias(team.org_id, key_id))
        _mark(CodeKey, key_id, litellm_key_id=out["token"], key_masked=out["masked"],
              sync_status="synced", sync_error=None)
        plain = out["plain_key"]
    except LiteLLMError as e:
        logger.warning("code_key %s generation failed (LiteLLM down): %s", key_id, e)
        _mark(CodeKey, key_id, sync_error=str(e)[:1000])
        plain = None

    with DB.connection_context():
        return CodeKey.get_by_id(key_id), plain


def revoke_code_key(*, code_key_id: str, client=None):
    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_or_none(CodeKey.id == code_key_id)
    if key is None:
        raise ValueError("key not found")

    _mark(CodeKey, code_key_id, status="revoked", sync_status="pending")
    cl = _client(client)
    try:
        if key.litellm_key_id:
            cl.block_key(key.litellm_key_id)
        _mark(CodeKey, code_key_id, sync_status="synced", sync_error=None)
    except LiteLLMError as e:
        _mark(CodeKey, code_key_id, sync_error=str(e)[:1000])

    with DB.connection_context():
        return CodeKey.get_by_id(code_key_id)
