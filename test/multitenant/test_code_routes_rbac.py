"""Route-level RBAC for the Code section — FastAPI TestClient, LiteLLM faked."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.p1


@pytest.fixture()
def panel_client(monkeypatch, org_with_entitlement_and_users):
    """TestClient over management.server.main:app with LiteLLM faked at module level."""
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    from management.server.services import code_provisioning
    fake = FakeLiteLLM()
    monkeypatch.setattr(code_provisioning, "_client", lambda client=None: client or fake)
    from management.server.main import app
    return TestClient(app), fake


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
