"""Unit tests for the code provisioning service — fake LiteLLM client, real dev DB."""
import pytest

pytestmark = pytest.mark.p1


class FakeLiteLLM:
    """In-memory stand-in for LiteLLMClient. Records calls; can simulate downtime."""
    def __init__(self, down=False):
        self.down = down
        self.teams: dict[str, dict] = {}
        self.blocked: set[str] = set()
        self.calls: list[tuple] = []
        self.keys: dict[str, dict] = {}
        self.usage: dict[str, dict] = {}

    @staticmethod
    def team_alias(org_id, code_team_id):
        return f"org:{org_id}:team:{code_team_id}"

    @staticmethod
    def key_alias(org_id, code_key_id):
        return f"org:{org_id}:key:{code_key_id}"

    def _maybe_down(self):
        if self.down:
            from management.server.services.litellm_client import LiteLLMError
            raise LiteLLMError("simulated: LiteLLM down")

    def list_teams(self):
        self._maybe_down()
        return [{"team_id": tid, "team_alias": t["alias"], "spend": t.get("spend", 0.0)}
                for tid, t in self.teams.items()]

    def find_team_by_alias(self, alias):
        self._maybe_down()
        for tid, t in self.teams.items():
            if t["alias"] == alias:
                return tid
        return None

    def create_team(self, *, alias, max_budget, budget_duration, models):
        self._maybe_down()
        tid = f"llm-{alias}"
        self.teams[tid] = {"alias": alias, "max_budget": max_budget, "spend": 0.0}
        self.calls.append(("create_team", alias))
        return tid

    def update_team(self, *, team_id, max_budget=None, models=None):
        self._maybe_down()
        self.teams[team_id]["max_budget"] = max_budget
        self.calls.append(("update_team", team_id, max_budget))

    def delete_team(self, team_id):
        self._maybe_down()
        self.teams.pop(team_id, None)
        self.calls.append(("delete_team", team_id))

    def generate_key(self, *, team_id, alias, max_budget=None, budget_duration=None, rpm_limit=None):
        self._maybe_down()
        self.calls.append(("generate_key", alias, max_budget, budget_duration, rpm_limit))
        self.keys[f"hash-{alias}"] = {"team_id": team_id, "key_alias": alias, "spend": 0.0,
                                      "max_budget": max_budget, "rpm_limit": rpm_limit}
        return {"plain_key": f"sk-{alias}-secret", "token": f"hash-{alias}", "masked": "sk-...cret"}

    def list_keys(self, team_id):
        self._maybe_down()
        return [{"token": tok, "key_alias": k["key_alias"], "spend": k.get("spend", 0.0)}
                for tok, k in self.keys.items() if k["team_id"] == team_id]

    def block_key(self, token):
        self._maybe_down()
        self.blocked.add(token)
        self.calls.append(("block_key", token))

    def unblock_key(self, token):
        self._maybe_down()
        self.blocked.discard(token)
        self.calls.append(("unblock_key", token))

    def daily_usage(self, day):
        self._maybe_down()
        return self.usage


@pytest.fixture()
def org_with_entitlement():
    """Minimal org + active entitlement (100 EUR / 1mo). Cleaned up after."""
    from api.db.db_models import DB, Organisation, CodeEntitlement, CodeTeam, CodeTeamMember, CodeKey
    from common.misc_utils import get_uuid
    org_id = get_uuid()
    with DB.connection_context():
        Organisation.create(id=org_id, name=f"code-test-{org_id[:6]}",
                            slug=f"code-test-{org_id[:6]}", created_by="tester")
        CodeEntitlement.create(id=get_uuid(), org_id=org_id, status="active",
                               org_code_budget=100.0, budget_period="1mo", created_by="tester")
    yield org_id
    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_id)]
        if team_ids:
            CodeKey.delete().where(CodeKey.code_team_id.in_(team_ids)).execute()
            CodeTeamMember.delete().where(CodeTeamMember.code_team_id.in_(team_ids)).execute()
        CodeTeam.delete().where(CodeTeam.org_id == org_id).execute()
        CodeEntitlement.delete().where(CodeEntitlement.org_id == org_id).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


def test_create_team_within_budget_syncs_to_litellm(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="squad-front",
                               max_budget=60.0, model_access=[], created_by="tester", client=fake)
    assert team.sync_status == "synced"
    assert team.litellm_team_id == f"llm-org:{org_with_entitlement}:team:{team.id}"


def test_allocation_invariant_rejects_overcommit(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    cp.create_code_team(org_id=org_with_entitlement, name="a", max_budget=60.0,
                        model_access=[], created_by="tester", client=fake)
    with pytest.raises(ValueError, match="allocation"):
        cp.create_code_team(org_id=org_with_entitlement, name="b", max_budget=50.0,
                            model_access=[], created_by="tester", client=fake)  # 60+50 > 100


def test_create_team_litellm_down_stays_pending(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM(down=True)
    team = cp.create_code_team(org_id=org_with_entitlement, name="squad-back",
                               max_budget=10.0, model_access=[], created_by="tester", client=fake)
    assert team.sync_status == "pending"
    assert team.litellm_team_id is None
    assert team.status == "active"  # desired state persisted first


def test_create_key_returns_plaintext_once_and_stores_only_hash(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    key_row, plain = cp.create_code_key(code_team_id=team.id, label="dev-alice",
                                        owner_user_id=None, created_by="tester", client=fake)
    assert plain and plain.startswith("sk-")
    assert key_row.litellm_key_id.startswith("hash-")
    assert plain not in (key_row.key_masked or "")
    assert key_row.sync_status == "synced"


def test_revoke_key_blocks_in_litellm(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    key_row, _ = cp.create_code_key(code_team_id=team.id, label="dev",
                                    owner_user_id=None, created_by="tester", client=fake)
    revoked = cp.revoke_code_key(code_key_id=key_row.id, client=fake)
    assert revoked.status == "revoked"
    assert revoked.sync_status == "synced"
    assert key_row.litellm_key_id in fake.blocked


def test_concurrent_team_creates_cannot_overcommit(org_with_entitlement):
    """Two concurrent creates whose sum exceeds the org budget: exactly one must win."""
    import concurrent.futures
    from management.server.services import code_provisioning as cp

    def attempt(name):
        fake = FakeLiteLLM()
        try:
            return cp.create_code_team(org_id=org_with_entitlement, name=name,
                                       max_budget=60.0, model_access=[],
                                       created_by="tester", client=fake)
        except ValueError as e:
            return e

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(attempt, ["race-a", "race-b"]))  # 60+60 > 100

    winners = [r for r in results if not isinstance(r, Exception)]
    losers = [r for r in results if isinstance(r, Exception)]
    assert len(winners) == 1 and len(losers) == 1
    assert "allocation" in str(losers[0])


def test_upsert_entitlement_rejects_budget_period_change_with_active_teams(org_with_entitlement):
    """Changing budget_period while active teams exist would desync team
    budget_duration (always == entitlement.budget_period) from what LiteLLM
    already has scheduled — must be rejected until teams are removed/recreated."""
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                        model_access=[], created_by="tester", client=fake)
    with pytest.raises(ValueError, match="p.riode"):
        cp.upsert_entitlement(org_id=org_with_entitlement, status="active",
                              org_code_budget=100.0, budget_period="7d",
                              actor_id="tester", client=fake)


def test_upsert_entitlement_allows_same_budget_period_with_active_teams(org_with_entitlement):
    """Re-saving the entitlement with the SAME budget_period must not be
    blocked by the active-teams guard (it's a no-op on the cycle)."""
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                        model_access=[], created_by="tester", client=fake)
    ent = cp.upsert_entitlement(org_id=org_with_entitlement, status="active",
                                org_code_budget=100.0, budget_period="1mo",
                                actor_id="tester", client=fake)
    assert ent.budget_period == "1mo"


def test_suspend_entitlement_fans_out_blocks(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    k1, _ = cp.create_code_key(code_team_id=team.id, label="d1", owner_user_id=None,
                               created_by="tester", client=fake)
    k2, _ = cp.create_code_key(code_team_id=team.id, label="d2", owner_user_id=None,
                               created_by="tester", client=fake)
    cp.upsert_entitlement(org_id=org_with_entitlement, status="suspended",
                          org_code_budget=100.0, budget_period="1mo",
                          actor_id="tester", client=fake)
    assert {k1.litellm_key_id, k2.litellm_key_id} <= fake.blocked
    # re-activate → unblock
    cp.upsert_entitlement(org_id=org_with_entitlement, status="active",
                          org_code_budget=100.0, budget_period="1mo",
                          actor_id="tester", client=fake)
    assert not fake.blocked


def test_delete_virgin_team_hard_deletes(org_with_entitlement):
    """0 clé jamais créée => hard delete : row DB supprimée, team LiteLLM aussi,
    et le budget alloué est libéré immédiatement."""
    from api.db.db_models import DB, CodeTeam
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="oops", max_budget=60.0,
                               model_access=[], created_by="tester", client=fake)
    assert cp.allocated_budget(org_with_entitlement) == 60.0

    mode, row = cp.delete_code_team(code_team_id=team.id, client=fake)
    assert mode == "hard" and row is None
    with DB.connection_context():
        assert CodeTeam.get_or_none(CodeTeam.id == team.id) is None
    assert team.litellm_team_id not in fake.teams
    assert cp.allocated_budget(org_with_entitlement) == 0.0


def test_delete_team_with_keys_soft_archives(org_with_entitlement):
    """Clés existantes => soft-archive : status='deleted', clés révoquées et
    bloquées upstream, invites pendantes supprimées, team LiteLLM CONSERVÉE
    (historique de spend), budget libéré."""
    import datetime
    from api.db.db_models import DB, CodeKey, CodeKeyInvite
    from common.misc_utils import get_uuid
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="squad", max_budget=60.0,
                               model_access=[], created_by="tester", client=fake)
    key, _ = cp.create_code_key(code_team_id=team.id, label="dev", owner_user_id=None,
                                created_by="tester", client=fake)
    with DB.connection_context():
        CodeKeyInvite.create(id=get_uuid(), code_team_id=team.id, email="a@b.co",
                             token_hash=get_uuid() + get_uuid(),
                             expires_at=datetime.datetime.now() + datetime.timedelta(hours=72),
                             created_by="tester")

    mode, row = cp.delete_code_team(code_team_id=team.id, client=fake)
    assert mode == "soft"
    assert row.status == "deleted" and row.sync_status == "synced"
    with DB.connection_context():
        assert CodeKey.get_by_id(key.id).status == "revoked"
        assert not CodeKeyInvite.select().where(CodeKeyInvite.code_team_id == team.id).exists()
    assert key.litellm_key_id in fake.blocked
    assert team.litellm_team_id in fake.teams  # spend history preserved upstream
    assert cp.allocated_budget(org_with_entitlement) == 0.0
    # une team archivée ne peut pas être re-supprimée
    with pytest.raises(ValueError, match="not found"):
        cp.delete_code_team(code_team_id=team.id, client=fake)


def test_delete_virgin_team_gateway_down_falls_back_to_soft(org_with_entitlement):
    """LiteLLM down pendant un hard delete => on n'orpheline pas la team
    upstream : la row reste en soft-archive avec sync_error, budget libéré."""
    from api.db.db_models import DB, CodeTeam
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    fake.down = True
    mode, row = cp.delete_code_team(code_team_id=team.id, client=fake)
    assert mode == "soft"
    with DB.connection_context():
        kept = CodeTeam.get_by_id(team.id)
    assert kept.status == "deleted" and kept.sync_error
    assert cp.allocated_budget(org_with_entitlement) == 0.0


def test_create_key_with_seat_limits_passes_them_to_litellm(org_with_entitlement):
    """Limites par siège : stockées sur la row ET transmises au /key/generate
    avec budget_duration = entitlement.budget_period (cycle aligné)."""
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    key, plain = cp.create_code_key(code_team_id=team.id, label="dev-bob",
                                    owner_user_id=None, created_by="tester",
                                    max_budget=10.0, rpm_limit=60, client=fake)
    assert plain
    assert key.max_budget == 10.0 and key.rpm_limit == 60
    gen = [c for c in fake.calls if c[0] == "generate_key"][0]
    assert gen[2] == 10.0          # max_budget transmis
    assert gen[3] == "1mo"         # budget_duration = cycle de l'entitlement
    assert gen[4] == 60            # rpm_limit transmis
    # sans limites: rien n'est transmis (None), la clé hérite juste de la team
    key2, _ = cp.create_code_key(code_team_id=team.id, label="dev-nolimit",
                                 owner_user_id=None, created_by="tester", client=fake)
    assert key2.max_budget is None and key2.rpm_limit is None
    gen2 = [c for c in fake.calls if c[0] == "generate_key"][1]
    assert gen2[2] is None and gen2[4] is None


def test_create_key_rejects_invalid_seat_limits(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    with pytest.raises(ValueError, match="max_budget"):
        cp.create_code_key(code_team_id=team.id, label="d", owner_user_id=None,
                           created_by="tester", max_budget=0, client=fake)
    with pytest.raises(ValueError, match="rpm_limit"):
        cp.create_code_key(code_team_id=team.id, label="d", owner_user_id=None,
                           created_by="tester", rpm_limit=-5, client=fake)
