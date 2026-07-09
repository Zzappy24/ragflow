"""Snapshots idempotents, courbe avec reset de cycle, housekeeping combiné.

These tests run against a real dev DB (see conftest.py:code_tests_db_guard)
that can already contain production-like data (other active code teams,
other housekeeping runs). Every assertion here must therefore be robust to
that pre-existing data:
  - counts coming out of snapshot_spend()/housekeeping() snapshot ALL active
    teams, not just the one this test created, so they're asserted as lower
    bounds (`>= 1`), never `== 1`.
  - row-level assertions are scoped to this test's own team_id/org_id.
  - the housekeeping run created by a test is looked up by matching the
    ran_at the call itself returned, never via last_run() (which would race
    against a concurrent/real housekeeping pass).
"""
import datetime
import pytest
from test.multitenant.test_code_provisioning import FakeLiteLLM, org_with_entitlement  # noqa: F401

pytestmark = pytest.mark.p1


@pytest.fixture()
def hk_org(org_with_entitlement):  # noqa: F811
    """org_with_entitlement, extended with housekeeping-test-safe teardown.

    org_with_entitlement's own teardown deletes CodeTeam/CodeKey/CodeTeamMember
    for this org but never touches CodeSpendSnapshot (plain provisioning tests
    never write snapshots) — so this fixture deletes this org's snapshot rows
    itself, BEFORE org_with_entitlement's teardown removes the teams they
    point at (fixture teardown runs in reverse dependency order, so this runs
    first).

    It also removes CodeHousekeepingRun rows, but ONLY the ones this test
    created: the set of existing run ids is captured at setup, and teardown
    deletes everything NOT in that set. This is exact regardless of clock
    skew between the test process and the DB server — a ran_at watermark
    comparison is not, since it can leave rows stranded (or delete rows it
    shouldn't) whenever the two clocks disagree. A bare
    `CodeHousekeepingRun.delete().execute()` would wipe real run history
    shared with production/other tests — never do that.
    """
    from api.db.db_models import DB, CodeSpendSnapshot, CodeTeam, CodeHousekeepingRun

    with DB.connection_context():
        pre_ids = {r.id for r in CodeHousekeepingRun.select(CodeHousekeepingRun.id)}

    yield org_with_entitlement

    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_with_entitlement)]
        if team_ids:
            CodeSpendSnapshot.delete().where(CodeSpendSnapshot.code_team_id.in_(team_ids)).execute()

        CodeHousekeepingRun.delete().where(
            CodeHousekeepingRun.id.not_in(list(pre_ids))).execute()


def test_snapshot_is_idempotent_per_day(hk_org):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 10.0
    # >= 1, not == 1: snapshot_spend snapshots ALL active teams, and the dev
    # DB can have other active teams (e.g. a real squad) already present.
    assert snapshot_spend(client=fake) >= 1
    fake.teams[team.litellm_team_id]["spend"] = 14.0
    assert snapshot_spend(client=fake) >= 1  # même jour → upsert, pas de doublon

    with DB.connection_context():
        rows = list(CodeSpendSnapshot.select().where(CodeSpendSnapshot.code_team_id == team.id))
    assert len(rows) == 1
    assert rows[0].spend == 14.0  # dernière valeur gagne


def test_snapshot_gateway_down_writes_nothing(hk_org):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                        model_access=[], created_by="tester", client=fake)
    fake.down = True
    assert snapshot_spend(client=fake) is None  # None = gateway down, distinct from "0 teams"
    with DB.connection_context():
        assert CodeSpendSnapshot.select().where(
            CodeSpendSnapshot.org_id == hk_org).count() == 0


def test_daily_series_handles_cycle_reset(hk_org):
    """J1: 10 → J2: 30 (delta 20) → J3: 5 (reset → delta 5)."""
    from management.server.services.code_housekeeping import daily_spend_series
    from api.db.db_models import DB, CodeSpendSnapshot
    from management.server.services import code_provisioning as cp
    from common.misc_utils import get_uuid

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    today = datetime.date.today()
    with DB.connection_context():
        for offset, spend in ((2, 10.0), (1, 30.0), (0, 5.0)):
            CodeSpendSnapshot.create(id=get_uuid(), snap_date=today - datetime.timedelta(days=offset),
                                     org_id=hk_org, code_team_id=team.id,
                                     spend=spend, max_budget=50.0)
    series = {p["date"]: p["spend"] for p in daily_spend_series([hk_org], days=5)}
    assert series[str(today - datetime.timedelta(days=2))] == 10.0  # 1er point = sa valeur
    assert series[str(today - datetime.timedelta(days=1))] == 20.0  # delta
    assert series[str(today)] == 5.0                                # reset → valeur du jour


def test_housekeeping_combines_and_records_run(hk_org):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import housekeeping
    from api.db.db_models import DB, CodeHousekeepingRun

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 3.0
    report = housekeeping(client=fake)
    # >= 1, not == 1: housekeeping snapshots ALL active teams in the DB.
    assert report["teams_snapshotted"] >= 1

    # Fetch the run this call created by its own reported run_id — never by
    # matching ran_at (MySQL DATETIME rounds fractional seconds, so a naive
    # microsecond-stripped comparison can miss the row) and never last_run()
    # (which races a concurrent/real housekeeping pass).
    with DB.connection_context():
        run = CodeHousekeepingRun.get_by_id(report["run_id"])
    assert run is not None and run.teams_snapshotted == report["teams_snapshotted"]


def test_housekeeping_gateway_down_records_error(hk_org):
    """Gateway down must be visible in the returned report, not indistinguishable
    from 'no active teams' (teams_snapshotted=0 with errors=0)."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import housekeeping

    fake = FakeLiteLLM()
    cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                        model_access=[], created_by="tester", client=fake)
    fake.down = True
    report = housekeeping(client=fake)
    assert report["teams_snapshotted"] == 0
    assert report["errors"] >= 1


def test_daily_series_window_boundary_seeds_prev_before_since(hk_org):
    """Team has 40 days of +5/day history; querying only the last 30 days must
    not emit the boundary day's full cumulative value as its delta."""
    from management.server.services.code_housekeeping import daily_spend_series
    from api.db.db_models import DB, CodeSpendSnapshot
    from management.server.services import code_provisioning as cp
    from common.misc_utils import get_uuid

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    today = datetime.date.today()
    with DB.connection_context():
        for offset in range(39, -1, -1):  # 40 days of history, oldest first
            spend = 5.0 * (40 - offset)
            CodeSpendSnapshot.create(id=get_uuid(), snap_date=today - datetime.timedelta(days=offset),
                                     org_id=hk_org, code_team_id=team.id,
                                     spend=spend, max_budget=50.0)

    series = {p["date"]: p["spend"] for p in daily_spend_series([hk_org], days=30)}
    boundary_day = str(today - datetime.timedelta(days=30))
    assert series[boundary_day] == 5.0  # seeded by the day-31 snapshot, not the raw 50.0


def test_daily_series_aggregates_multiple_teams_same_day(hk_org):
    """Two teams in the same org, snapshots on the same days: the series must
    sum both teams' deltas per day, not just one."""
    from management.server.services.code_housekeeping import daily_spend_series
    from api.db.db_models import DB, CodeSpendSnapshot
    from management.server.services import code_provisioning as cp
    from common.misc_utils import get_uuid

    fake = FakeLiteLLM()
    team_a = cp.create_code_team(org_id=hk_org, name="a", max_budget=50.0,
                                 model_access=[], created_by="tester", client=fake)
    team_b = cp.create_code_team(org_id=hk_org, name="b", max_budget=50.0,
                                 model_access=[], created_by="tester", client=fake)
    today = datetime.date.today()
    with DB.connection_context():
        for offset, spend in ((2, 10.0), (1, 15.0), (0, 20.0)):
            CodeSpendSnapshot.create(id=get_uuid(), snap_date=today - datetime.timedelta(days=offset),
                                     org_id=hk_org, code_team_id=team_a.id,
                                     spend=spend, max_budget=50.0)
        for offset, spend in ((2, 100.0), (1, 106.0), (0, 115.0)):
            CodeSpendSnapshot.create(id=get_uuid(), snap_date=today - datetime.timedelta(days=offset),
                                     org_id=hk_org, code_team_id=team_b.id,
                                     spend=spend, max_budget=50.0)

    series = {p["date"]: p["spend"] for p in daily_spend_series([hk_org], days=5)}
    assert series[str(today - datetime.timedelta(days=1))] == 11.0  # 5 (a) + 6 (b)
    assert series[str(today)] == 14.0                                # 5 (a) + 9 (b)


def test_snapshot_captures_daily_tokens_and_errors(hk_org):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend, daily_spend_series
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 3.0
    fake.usage = {team.litellm_team_id: {"tokens": 1234, "errors": 2}}
    assert snapshot_spend(client=fake) >= 1

    with DB.connection_context():
        row = CodeSpendSnapshot.get(CodeSpendSnapshot.code_team_id == team.id)
    assert row.tokens == 1234 and row.errors == 2

    series = daily_spend_series([hk_org], days=2)
    today = series[-1]
    assert today["tokens"] >= 1234 and today["errors"] >= 2


def test_snapshot_tokens_null_when_usage_unavailable(hk_org, monkeypatch):
    """spend dispo mais usage KO (endpoint absent) → tokens/errors restent NULL, spend écrit."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from management.server.services.litellm_client import LiteLLMError
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    def broken_usage(day):
        raise LiteLLMError("spend-logs unavailable")
    fake.daily_usage = broken_usage
    assert snapshot_spend(client=fake) >= 1
    with DB.connection_context():
        row = CodeSpendSnapshot.get(CodeSpendSnapshot.code_team_id == team.id)
    assert row.tokens is None and row.errors is None and row.spend == 0.0
