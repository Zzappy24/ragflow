"""The reconciler converges pending rows once LiteLLM is back up."""
import pytest
from test.multitenant.test_code_provisioning import FakeLiteLLM, org_with_entitlement  # noqa: F401

pytestmark = pytest.mark.p1


def test_reconciler_repairs_team_and_revocation_after_downtime(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import reconcile_all

    down = FakeLiteLLM(down=True)
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=down)
    assert team.sync_status == "pending"

    up = FakeLiteLLM()  # LiteLLM back up
    report = reconcile_all(client=up)
    assert report["teams_synced"] == 1

    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_by_id(team.id)
    assert team.sync_status == "synced"
    assert team.litellm_team_id is not None

    # key created fine, then revocation fails mid-flight -> reconciler must block it
    key, _ = cp.create_code_key(code_team_id=team.id, label="d", owner_user_id=None,
                                created_by="tester", client=up)
    up.down = True
    revoked = cp.revoke_code_key(code_key_id=key.id, client=up)
    assert revoked.sync_status == "pending"
    up.down = False
    report = reconcile_all(client=up)
    assert report["keys_synced"] == 1
    assert key.litellm_key_id in up.blocked


def test_reconciler_marks_unfinishable_pending_create_keys_as_error(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import reconcile_all

    up = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=up)
    up.down = True
    key, plain = cp.create_code_key(code_team_id=team.id, label="d", owner_user_id=None,
                                    created_by="tester", client=up)
    assert plain is None and key.sync_status == "pending"
    up.down = False
    report = reconcile_all(client=up)
    assert report["errors"] >= 1

    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_by_id(key.id)
    assert key.sync_status == "error"  # plaintext unrecoverable -> manual recreate


def test_reconciler_flags_orphan_create_swept_to_blocked_by_suspension(org_with_entitlement):  # noqa: F811
    """A key whose create failed (litellm_key_id=None, sync pending) then gets
    swept to status='blocked' by an org-suspension fan-out. It matches neither
    phase 1 (needs litellm_key_id) nor the old phase-3 predicate (needed
    status=='active') — it must still be flagged 'error' and counted."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import reconcile_all

    up = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=up)
    down = FakeLiteLLM(down=True)
    key, plain = cp.create_code_key(code_team_id=team.id, label="d", owner_user_id=None,
                                    created_by="tester", client=down)
    assert plain is None and key.sync_status == "pending" and key.litellm_key_id is None

    # suspend the org with a working client -> fan-out sweeps the key to blocked
    cp.upsert_entitlement(org_id=org_with_entitlement, status="suspended",
                          org_code_budget=100.0, budget_period="1mo",
                          actor_id="tester", client=up)

    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_by_id(key.id)
    assert key.status == "blocked"
    assert key.litellm_key_id is None
    assert key.sync_status == "pending"

    report = reconcile_all(client=up)
    assert report["errors"] >= 1

    with DB.connection_context():
        key = CodeKey.get_by_id(key.id)
    assert key.sync_status == "error"


def test_reconciler_skips_pending_team_of_suspended_entitlement(org_with_entitlement):  # noqa: F811
    """A team created while LiteLLM was down, whose org is then suspended,
    must NOT be created in LiteLLM by the reconciler — it stays pending until
    the org is reactivated."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import reconcile_all

    down = FakeLiteLLM(down=True)
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=down)
    assert team.sync_status == "pending"

    up = FakeLiteLLM()
    cp.upsert_entitlement(org_id=org_with_entitlement, status="suspended",
                          org_code_budget=100.0, budget_period="1mo",
                          actor_id="tester", client=up)

    report = reconcile_all(client=up)
    assert report["teams_synced"] == 0

    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_by_id(team.id)
    assert team.sync_status == "pending"
    assert team.litellm_team_id is None


# ---------------------------------------------------------------------------
# Phase 4 — alias-diff sweep (gateway -> panel)
# ---------------------------------------------------------------------------

def test_sweep_blocks_gateway_key_without_panel_row(org_with_entitlement):
    """Une clé LiteLLM portant NOTRE alias mais sans row locale (réponse de
    generate perdue avant l'écriture, double-claim post-sentinel…) est un
    accès fantôme : le sweep la bloque."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import sweep_gateway_orphans
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    # clé fantôme : alias à notre convention, id inexistant en base
    ghost_alias = f"org:{org_with_entitlement}:key:{'f' * 32}"
    fake.generate_key(team_id=team.litellm_team_id, alias=ghost_alias)

    report = sweep_gateway_orphans(client=fake)
    assert report["orphan_keys_blocked"] == 1
    assert f"hash-{ghost_alias}" in fake.blocked


def test_sweep_reblocks_locally_revoked_but_gateway_active(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import sweep_gateway_orphans
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    key, _ = cp.create_code_key(code_team_id=team.id, label="dev", owner_user_id=None,
                                created_by="tester", client=fake)
    cp.revoke_code_key(code_key_id=key.id, client=fake)
    # incident côté gateway : la clé se retrouve débloquée sans que le panel le sache
    fake.blocked.discard(key.litellm_key_id)

    report = sweep_gateway_orphans(client=fake)
    assert report["orphan_keys_blocked"] == 1
    assert key.litellm_key_id in fake.blocked

    # run suivant : la gateway la liste bloquée -> plus rien à faire
    assert sweep_gateway_orphans(client=fake)["orphan_keys_blocked"] == 0


def test_sweep_counts_orphan_team_but_never_deletes(org_with_entitlement):
    from management.server.services.code_reconcile import sweep_gateway_orphans
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    fake = FakeLiteLLM()
    ghost_alias = f"org:{org_with_entitlement}:team:{'e' * 32}"
    fake.create_team(alias=ghost_alias, max_budget=5.0, budget_duration="1mo", models=[])

    report = sweep_gateway_orphans(client=fake)
    assert report["orphan_teams"] == 1
    assert f"llm-{ghost_alias}" in fake.teams  # jamais supprimée automatiquement


def test_sweep_never_touches_foreign_objects(org_with_entitlement):
    """Objets hors convention d'alias (autre produit, créés à la main) :
    intouchables, quoi qu'il arrive."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import sweep_gateway_orphans
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    fake.create_team(alias="damien-test-manuel", max_budget=1.0, budget_duration="1mo", models=[])
    fake.generate_key(team_id=team.litellm_team_id, alias="cle-externe-sans-convention")

    report = sweep_gateway_orphans(client=fake)
    assert report == {"orphan_teams": 0, "orphan_keys_blocked": 0}
    assert "hash-cle-externe-sans-convention" not in fake.blocked


def test_sweep_leaves_healthy_keys_alone(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import sweep_gateway_orphans
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    key, _ = cp.create_code_key(code_team_id=team.id, label="dev", owner_user_id=None,
                                created_by="tester", client=fake)
    report = sweep_gateway_orphans(client=fake)
    assert report == {"orphan_teams": 0, "orphan_keys_blocked": 0}
    assert key.litellm_key_id not in fake.blocked
