"""Tag BU des workspaces (settings_json, modifiable) + export CSV conso RAG."""
import datetime

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.p1


@pytest.fixture()
def panel(org_with_entitlement_and_users):
    from management.server.main import app
    return TestClient(app), org_with_entitlement_and_users


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def ws_in_org(org_with_entitlement_and_users):
    from api.db.db_models import DB, Workspace, TokenUsageDaily
    from common.misc_utils import get_uuid
    org_id, _ = org_with_entitlement_and_users
    ws_id, tenant_id = get_uuid(), get_uuid()
    with DB.connection_context():
        Workspace.create(id=ws_id, org_id=org_id, tenant_id=tenant_id,
                         name="ws-data", created_by="tester")
    yield org_id, ws_id, tenant_id
    with DB.connection_context():
        TokenUsageDaily.delete().where(TokenUsageDaily.tenant_id == tenant_id).execute()
        Workspace.delete().where(Workspace.id == ws_id).execute()


def test_bu_tag_set_update_and_remove(panel, ws_in_org):
    client, (org_id, tokens) = panel
    _, ws_id, _ = ws_in_org

    # membre simple : 403 (update = org admin)
    assert client.put(f"/api/admin/orgs/{org_id}/workspaces/{ws_id}",
                      json={"bu": "Digital"}, headers=_h(tokens["plain_member"])).status_code == 403

    r = client.put(f"/api/admin/orgs/{org_id}/workspaces/{ws_id}",
                   json={"bu": "Digital"}, headers=_h(tokens["org_admin"]))
    assert r.status_code == 200 and r.json()["bu"] == "Digital"

    # modifiable, et le tag survit à un update d'un autre champ
    client.put(f"/api/admin/orgs/{org_id}/workspaces/{ws_id}",
               json={"bu": "Industrie"}, headers=_h(tokens["org_admin"]))
    r = client.put(f"/api/admin/orgs/{org_id}/workspaces/{ws_id}",
                   json={"description": "desc"}, headers=_h(tokens["org_admin"]))
    assert r.json()["bu"] == "Industrie"

    # "" = retrait
    r = client.put(f"/api/admin/orgs/{org_id}/workspaces/{ws_id}",
                   json={"bu": ""}, headers=_h(tokens["org_admin"]))
    assert r.json()["bu"] == ""


def test_rag_usage_export_csv(panel, ws_in_org):
    from api.db.db_models import DB, TokenUsageDaily, Workspace
    client, (org_id, tokens) = panel
    _, ws_id, tenant_id = ws_in_org
    today = datetime.date.today()
    with DB.connection_context():
        Workspace.update(settings_json={"bu": "Digital"}).where(Workspace.id == ws_id).execute()
        TokenUsageDaily.create(tenant_id=tenant_id, llm_factory="Ollama", model_type="chat",
                               llm_name="qwen3", date=str(today), tokens=4321)

    month = today.strftime("%Y-%m")
    assert client.get(f"/api/admin/orgs/{org_id}/usage/export?month={month}",
                      headers=_h(tokens["plain_member"])).status_code == 403
    r = client.get(f"/api/admin/orgs/{org_id}/usage/export?month={month}",
                   headers=_h(tokens["org_admin"]))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "date;workspace;bu;modele;type;tokens" in r.text
    assert f"{today};ws-data;Digital;qwen3;chat;4321" in r.text

    assert client.get(f"/api/admin/orgs/{org_id}/usage/export?month=2026-13",
                      headers=_h(tokens["org_admin"])).status_code == 422
