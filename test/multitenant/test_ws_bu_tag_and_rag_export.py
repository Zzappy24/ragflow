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


def test_billing_summary_and_statement(panel, ws_in_org):
    """Relevé mensuel consolidé : le spend Code du mois est le DELTA des
    snapshots (seed pré-mois + reset de cycle géré), pas le cumul brut."""
    from api.db.db_models import DB, CodeSpendSnapshot, TokenUsageDaily, Workspace
    from common.misc_utils import get_uuid
    client, (org_id, tokens) = panel
    _, ws_id, tenant_id = ws_in_org

    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "bill-t", "max_budget": 50.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    today = datetime.date.today()
    first = today.replace(day=1)
    with DB.connection_context():
        Workspace.update(settings_json={"bu": "Digital"}).where(Workspace.id == ws_id).execute()
        TokenUsageDaily.create(tenant_id=tenant_id, llm_factory="Ollama", model_type="chat",
                               llm_name="qwen3", date=str(first), tokens=4321)
        # seed pré-mois : cumul 10.0 — le 1er delta du mois doit être 2.0, pas 12.0
        CodeSpendSnapshot.create(id=get_uuid(), snap_date=first - datetime.timedelta(days=1),
                                 org_id=org_id, code_team_id=team["id"], spend=10.0,
                                 max_budget=50.0, tokens=999, errors=0)
        for day, spend, tok in ((0, 12.0, 100),   # +2.0
                                (1, 3.0, 200),    # reset de cycle -> +3.0
                                (2, 5.5, None)):  # +2.5, tokens inconnus ce relevé
            CodeSpendSnapshot.create(id=get_uuid(), snap_date=first + datetime.timedelta(days=day),
                                     org_id=org_id, code_team_id=team["id"], spend=spend,
                                     max_budget=50.0, tokens=tok, errors=0)

    month = today.strftime("%Y-%m")
    # résumé JSON (carte Facturation)
    r = client.get(f"/api/admin/orgs/{org_id}/billing/summary?month={month}",
                   headers=_h(tokens["org_admin"]))
    assert r.status_code == 200
    data = r.json()
    assert data["code"]["total_eur"] == 7.5  # 2.0 + 3.0 + 2.5
    t = [x for x in data["code"]["teams"] if x["team_id"] == team["id"]][0]
    assert t["spend_eur"] == 7.5 and t["tokens"] == 300
    ws_row = [w for w in data["rag"]["workspaces"] if w["workspace_id"] == ws_id][0]
    assert ws_row["tokens"] == 4321 and ws_row["bu"] == "Digital"

    # relevé CSV (compta)
    assert client.get(f"/api/admin/orgs/{org_id}/billing/statement?month={month}",
                      headers=_h(tokens["plain_member"])).status_code == 403
    r = client.get(f"/api/admin/orgs/{org_id}/billing/statement?month={month}",
                   headers=_h(tokens["org_admin"]))
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    body = r.text
    assert "RELEVE MENSUEL" in body
    assert "produit;entite;bu;tokens;montant_eur" in body
    assert "code;bill-t;;300;7.5" in body
    assert "code;TOTAL;;300;7.5" in body
    assert "rag;conso ws-data (fair-use, incluse);Digital;4321;" in body
    assert "rag;TOTAL;;4321;" in body
    assert "TOTAL GENERAL;;;;7.5" in body  # pas de forfait posé -> conso Code seule

    with DB.connection_context():
        CodeSpendSnapshot.delete().where(CodeSpendSnapshot.code_team_id == team["id"]).execute()


def test_billing_statement_with_rag_fee(panel, ws_in_org):
    """Forfait RAG contractualisé : ligne forfait + TOTAL GENERAL = forfait + conso Code."""
    from api.db.db_models import DB, Organisation, CodeSpendSnapshot
    from common.misc_utils import get_uuid
    client, (org_id, tokens) = panel
    _, _, _tenant = ws_in_org

    # superuser pose le forfait via la route quota
    r = client.patch(f"/api/admin/orgs/{org_id}/quota",
                     json={"rag_monthly_fee_eur": 1500.0}, headers=_h(tokens["superuser"]))
    assert r.status_code == 200
    with DB.connection_context():
        assert Organisation.get_by_id(org_id).rag_monthly_fee_eur == 1500.0

    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "fee-t", "max_budget": 50.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    today = datetime.date.today()
    with DB.connection_context():
        CodeSpendSnapshot.create(id=get_uuid(), snap_date=today.replace(day=1), org_id=org_id,
                                 code_team_id=team["id"], spend=7.5, max_budget=50.0,
                                 tokens=100, errors=0)

    month = today.strftime("%Y-%m")
    data = client.get(f"/api/admin/orgs/{org_id}/billing/summary?month={month}",
                      headers=_h(tokens["org_admin"])).json()
    assert data["rag"]["monthly_fee_eur"] == 1500.0
    assert data["total_eur"] == 1507.5  # forfait + conso Code

    body = client.get(f"/api/admin/orgs/{org_id}/billing/statement?month={month}",
                      headers=_h(tokens["org_admin"])).text
    assert "rag;forfait mensuel;;;1500.0" in body
    assert "TOTAL GENERAL;;;;1507.5" in body

    # validation : forfait négatif refusé ; null = retrait
    assert client.patch(f"/api/admin/orgs/{org_id}/quota",
                        json={"rag_monthly_fee_eur": -5}, headers=_h(tokens["superuser"])).status_code == 400
    client.patch(f"/api/admin/orgs/{org_id}/quota",
                 json={"rag_monthly_fee_eur": None}, headers=_h(tokens["superuser"]))
    data = client.get(f"/api/admin/orgs/{org_id}/billing/summary?month={month}",
                      headers=_h(tokens["org_admin"])).json()
    assert data["rag"]["monthly_fee_eur"] is None

    with DB.connection_context():
        CodeSpendSnapshot.delete().where(CodeSpendSnapshot.code_team_id == team["id"]).execute()
