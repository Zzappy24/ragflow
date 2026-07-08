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

    def generate_key(self, *, team_id, alias):
        self._maybe_down()
        self.calls.append(("generate_key", alias))
        return {"plain_key": f"sk-{alias}-secret", "token": f"hash-{alias}", "masked": "sk-...cret"}

    def block_key(self, token):
        self._maybe_down()
        self.blocked.add(token)
        self.calls.append(("block_key", token))

    def unblock_key(self, token):
        self._maybe_down()
        self.blocked.discard(token)
        self.calls.append(("unblock_key", token))


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
