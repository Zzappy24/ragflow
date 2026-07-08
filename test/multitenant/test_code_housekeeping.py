"""Snapshots idempotents, courbe avec reset de cycle, housekeeping combiné."""
import datetime
import pytest
from test.multitenant.test_code_provisioning import FakeLiteLLM, org_with_entitlement  # noqa: F401

pytestmark = pytest.mark.p1


def _cleanup_snapshots(org_id):
    from api.db.db_models import DB, CodeSpendSnapshot, CodeHousekeepingRun, CodeTeam, CodeTeamMember, CodeKey
    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_id)]
        if team_ids:
            CodeSpendSnapshot.delete().where(CodeSpendSnapshot.code_team_id.in_(team_ids)).execute()
            CodeKey.delete().where(CodeKey.code_team_id.in_(team_ids)).execute()
            CodeTeamMember.delete().where(CodeTeamMember.code_team_id.in_(team_ids)).execute()
            CodeTeam.delete().where(CodeTeam.id.in_(team_ids)).execute()
        CodeHousekeepingRun.delete().execute()


def test_snapshot_is_idempotent_per_day(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 10.0
    assert snapshot_spend(client=fake) == 1
    fake.teams[team.litellm_team_id]["spend"] = 14.0
    assert snapshot_spend(client=fake) == 1  # même jour → upsert, pas de doublon

    with DB.connection_context():
        rows = list(CodeSpendSnapshot.select().where(CodeSpendSnapshot.code_team_id == team.id))
    assert len(rows) == 1
    assert rows[0].spend == 14.0  # dernière valeur gagne
    _cleanup_snapshots(org_with_entitlement)


def test_snapshot_gateway_down_writes_nothing(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                        model_access=[], created_by="tester", client=fake)
    fake.down = True
    assert snapshot_spend(client=fake) == 0
    with DB.connection_context():
        assert CodeSpendSnapshot.select().where(
            CodeSpendSnapshot.org_id == org_with_entitlement).count() == 0


def test_daily_series_handles_cycle_reset(org_with_entitlement):  # noqa: F811
    """J1: 10 → J2: 30 (delta 20) → J3: 5 (reset → delta 5)."""
    from management.server.services.code_housekeeping import daily_spend_series
    from api.db.db_models import DB, CodeSpendSnapshot
    from management.server.services import code_provisioning as cp
    from common.misc_utils import get_uuid

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    today = datetime.date.today()
    with DB.connection_context():
        for offset, spend in ((2, 10.0), (1, 30.0), (0, 5.0)):
            CodeSpendSnapshot.create(id=get_uuid(), snap_date=today - datetime.timedelta(days=offset),
                                     org_id=org_with_entitlement, code_team_id=team.id,
                                     spend=spend, max_budget=50.0)
    series = {p["date"]: p["spend"] for p in daily_spend_series([org_with_entitlement], days=5)}
    assert series[str(today - datetime.timedelta(days=2))] == 10.0  # 1er point = sa valeur
    assert series[str(today - datetime.timedelta(days=1))] == 20.0  # delta
    assert series[str(today)] == 5.0                                # reset → valeur du jour
    _cleanup_snapshots(org_with_entitlement)


def test_housekeeping_combines_and_records_run(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import housekeeping, last_run

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 3.0
    report = housekeeping(client=fake)
    assert report["teams_snapshotted"] == 1
    run = last_run()
    assert run is not None and run.teams_snapshotted == 1
    _cleanup_snapshots(org_with_entitlement)
