"""Route-level RBAC for the Code section — FastAPI TestClient, LiteLLM faked."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.p1


@pytest.fixture()
def panel_client(monkeypatch, org_with_entitlement_and_users):
    """TestClient over management.server.main:app with LiteLLM faked at module level.

    Also cleans up any CodeHousekeepingRun rows created by tests hitting
    POST /code/housekeeping — the same exact-by-id pattern as hk_org in
    test_code_housekeeping.py (a ran_at watermark is not robust to clock
    skew between the test process and the DB server).
    """
    from api.db.db_models import DB, CodeHousekeepingRun
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    from management.server.services import code_provisioning, code_reconcile
    fake = FakeLiteLLM()
    monkeypatch.setattr(code_provisioning, "_client", lambda client=None: client or fake)
    # code_reconcile imports `_client` by name, so it must be patched separately —
    # otherwise reconcile_all() would construct a real LiteLLMClient().
    monkeypatch.setattr(code_reconcile, "_client", lambda client=None: client or fake)
    # Disable the scheduler for tests (sleep-first means no real run on boot, but disable to be safe)
    monkeypatch.setenv("ADMIN_CODE_SCHEDULER", "0")
    from management.server.main import app

    with DB.connection_context():
        pre_ids = {r.id for r in CodeHousekeepingRun.select(CodeHousekeepingRun.id)}

    yield TestClient(app), fake

    with DB.connection_context():
        CodeHousekeepingRun.delete().where(
            CodeHousekeepingRun.id.not_in(list(pre_ids))).execute()


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_entitlement_upsert_superuser_only(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    body = {"status": "active", "org_code_budget": 100.0, "budget_period": "1mo"}
    assert client.put(f"/api/admin/orgs/{org_id}/code/entitlement",
                      json=body, headers=_h(tokens["org_admin"])).status_code == 403
    assert client.put(f"/api/admin/orgs/{org_id}/code/entitlement",
                      json=body, headers=_h(tokens["superuser"])).status_code == 200


def test_team_create_org_admin_only(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    body = {"name": "squad", "max_budget": 10.0, "model_access": []}
    assert client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json=body, headers=_h(tokens["plain_member"])).status_code == 403
    r = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                    json=body, headers=_h(tokens["org_admin"]))
    assert r.status_code == 201


def test_key_create_delegated_to_team_admin(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()

    # plain member: 403 before delegation
    assert client.post(f"/api/admin/code/teams/{team['id']}/keys",
                       json={"label": "dev"}, headers=_h(tokens["plain_member"])).status_code == 403

    # delegate, then member can create a key and sees the plaintext ONCE
    client.post(f"/api/admin/code/teams/{team['id']}/admins",
                json={"email": tokens["plain_member_email"]}, headers=_h(tokens["org_admin"]))
    r = client.post(f"/api/admin/code/teams/{team['id']}/keys",
                    json={"label": "dev"}, headers=_h(tokens["plain_member"]))
    assert r.status_code == 201
    assert r.json()["plain_key"].startswith("sk-")

    # overview endpoint never leaks plaintext
    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview",
                    headers=_h(tokens["org_admin"])).json()
    assert "plain_key" not in str(ov)


def test_allocation_violation_returns_422(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    client.post(f"/api/admin/orgs/{org_id}/code/teams",
                json={"name": "a", "max_budget": 80.0, "model_access": []},
                headers=_h(tokens["org_admin"]))
    r = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                    json={"name": "b", "max_budget": 30.0, "model_access": []},
                    headers=_h(tokens["org_admin"]))
    assert r.status_code == 422
    assert "allocation" in r.json()["detail"]


def test_cross_org_admin_cannot_update_or_revoke(panel_client, org_with_entitlement_and_users,
                                                 second_org_admin):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    _, org_b_token, _ = second_org_admin

    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "a-team", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    key = client.post(f"/api/admin/code/teams/{team['id']}/keys",
                      json={"label": "dev"}, headers=_h(tokens["org_admin"])).json()["key"]

    # org B's admin has no relationship to org A's team/key -> 403 on both routes
    r = client.put(f"/api/admin/code/teams/{team['id']}",
                   json={"max_budget": 5.0}, headers=_h(org_b_token))
    assert r.status_code == 403

    r = client.post(f"/api/admin/code/keys/{key['id']}/revoke", headers=_h(org_b_token))
    assert r.status_code == 403

    # verify nothing changed, as seen by org A's own admin
    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview",
                    headers=_h(tokens["org_admin"])).json()
    t = next(t for t in ov["teams"] if t["id"] == team["id"])
    assert t["max_budget"] == 10.0
    k = next(k for k in t["keys"] if k["id"] == key["id"])
    assert k["status"] == "active"


def test_housekeeping_superuser_only_and_records_run(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    assert client.post("/api/admin/code/housekeeping",
                       headers=_h(tokens["org_admin"])).status_code == 403
    r = client.post("/api/admin/code/housekeeping", headers=_h(tokens["superuser"]))
    assert r.status_code == 200
    assert "teams_snapshotted" in r.json()
    # alias rétro-compatible
    assert client.post("/api/admin/code/reconcile",
                       headers=_h(tokens["superuser"])).status_code == 200


def test_add_team_admin_rejects_non_org_member(panel_client, org_with_entitlement_and_users,
                                               second_org_admin):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    _, _, org_b_admin_email = second_org_admin

    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "a-team2", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()

    r = client.post(f"/api/admin/code/teams/{team['id']}/admins",
                    json={"email": org_b_admin_email}, headers=_h(tokens["org_admin"]))
    assert r.status_code == 422


def test_orgs_summary_superuser_sees_org_with_status(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    client.post(f"/api/admin/orgs/{org_id}/code/teams",
                json={"name": "s", "max_budget": 40.0, "model_access": []},
                headers=_h(tokens["org_admin"]))
    rows = client.get("/api/admin/code/orgs-summary", headers=_h(tokens["superuser"])).json()
    mine = next(r for r in rows if r["org_id"] == org_id)
    assert mine["code_status"] == "active"
    assert mine["org_code_budget"] == 100.0
    assert mine["allocated"] == 40.0
    assert mine["teams_count"] == 1
    assert "plain" not in str(rows)


def test_orgs_summary_member_sees_only_their_orgs(panel_client, org_with_entitlement_and_users, second_org_admin):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    org_b_id, org_b_token, _ = second_org_admin
    rows = client.get("/api/admin/code/orgs-summary", headers=_h(tokens["plain_member"])).json()
    ids = {r["org_id"] for r in rows}
    assert org_id in ids and org_b_id not in ids


def test_orgs_summary_org_without_entitlement_is_null(panel_client, org_with_entitlement_and_users, second_org_admin):
    client, _ = panel_client
    org_b_id, org_b_token, _ = second_org_admin
    rows = client.get("/api/admin/code/orgs-summary", headers=_h(org_b_token)).json()
    mine = next(r for r in rows if r["org_id"] == org_b_id)
    assert mine["code_status"] is None
    assert mine["teams_count"] == 0 and mine["keys_count"] == 0


def test_overview_and_summary_expose_real_spend(panel_client, org_with_entitlement_and_users):
    client, fake = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 40.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    fake.teams[team["litellm_team_id"]]["spend"] = 12.5

    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview", headers=_h(tokens["org_admin"])).json()
    assert ov["org_spend"] == 12.5
    assert ov["teams"][0]["spend"] == 12.5

    rows = client.get("/api/admin/code/orgs-summary", headers=_h(tokens["superuser"])).json()
    mine = next(r for r in rows if r["org_id"] == org_id)
    assert mine["spend"] == 12.5


def test_spend_is_null_not_zero_when_gateway_down(panel_client, org_with_entitlement_and_users):
    client, fake = panel_client
    org_id, tokens = org_with_entitlement_and_users
    client.post(f"/api/admin/orgs/{org_id}/code/teams",
                json={"name": "s", "max_budget": 40.0, "model_access": []},
                headers=_h(tokens["org_admin"]))
    fake.down = True
    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview", headers=_h(tokens["org_admin"])).json()
    assert ov["org_spend"] is None
    assert ov["teams"][0]["spend"] is None
    rows = client.get("/api/admin/code/orgs-summary", headers=_h(tokens["superuser"])).json()
    mine = next(r for r in rows if r["org_id"] == org_id)
    assert mine["spend"] is None


def test_overview_exposes_per_key_spend(panel_client, org_with_entitlement_and_users):
    client, fake = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 40.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    key = client.post(f"/api/admin/code/teams/{team['id']}/keys",
                      json={"label": "dev-x"}, headers=_h(tokens["org_admin"])).json()["key"]
    token = next(iter(fake.keys))
    fake.keys[token]["spend"] = 4.2

    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview", headers=_h(tokens["org_admin"])).json()
    key_row = next(k for k in ov["teams"][0]["keys"] if k["id"] == key["id"])
    assert key_row["spend"] == 4.2


def test_dashboard_forbidden_for_user_with_no_org_membership(panel_client):
    """A user that belongs to zero orgs (no OrgMember row at all, not even in
    another org) must be rejected — org_ids ends up empty and the route must
    403 rather than silently falling back to a superuser-like "all orgs" view."""
    client, _ = panel_client
    from api.db.db_models import DB, User
    from common.misc_utils import get_uuid
    from management.server.auth.jwt import create_access_token

    uid = get_uuid()
    email = f"code-rbac-no-org-{uid[:6]}@example.com"
    with DB.connection_context():
        User.create(id=uid, nickname="code-rbac-no-org", email=email,
                   password="x", is_superuser=False)
    token = create_access_token(uid)
    try:
        r = client.get("/api/admin/code/dashboard", headers=_h(token))
        assert r.status_code == 403
    finally:
        with DB.connection_context():
            User.delete().where(User.id == uid).execute()


def test_dashboard_rbac_and_shape(panel_client, org_with_entitlement_and_users, second_org_admin):
    client, fake = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 40.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    fake.teams[team["litellm_team_id"]]["spend"] = 39.0  # ≥ 80% de 40 → alerte

    d = client.get("/api/admin/code/dashboard", headers=_h(tokens["superuser"])).json()
    # Robust to real data already in the DB (other teams' spend): only assert
    # this fixture's contribution is present, not that it's the whole total.
    assert d["kpis"]["cycle_spend"] >= 39.0
    assert d["kpis"]["teams"] >= 1 and d["kpis"]["budget_alerts"] >= 1
    assert isinstance(d["daily"], list)
    assert any(t["code_team_id"] == team["id"] for t in d["top_teams"])

    # scoped : l'admin de l'org B ne voit pas le spend de l'org A
    org_b_id, org_b_token, _ = second_org_admin
    db = client.get("/api/admin/code/dashboard", headers=_h(org_b_token)).json()
    assert all(t["code_team_id"] != team["id"] for t in db["top_teams"])
    assert db["kpis"]["cycle_spend"] in (0.0, None)
