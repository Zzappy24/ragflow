"""Template de modèles workspace : les nouveaux workspaces héritent du
workspace flaggé settings_json.model_template, pas du créateur."""
import pytest

pytestmark = pytest.mark.p1


@pytest.fixture()
def two_workspaces():
    from api.db.db_models import DB, Organisation, Workspace
    from common.misc_utils import get_uuid
    org_id = get_uuid()
    wa, wb = get_uuid(), get_uuid()
    ta, tb = get_uuid(), get_uuid()
    with DB.connection_context():
        Organisation.create(id=org_id, name=f"tmpl-{org_id[:6]}", slug=f"tmpl-{org_id[:6]}", created_by="t")
        Workspace.create(id=wa, org_id=org_id, tenant_id=ta, name="ws-a", created_by="t")
        Workspace.create(id=wb, org_id=org_id, tenant_id=tb, name="ws-b", created_by="t")
    yield org_id, (wa, ta), (wb, tb)
    with DB.connection_context():
        Workspace.delete().where(Workspace.org_id == org_id).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


def test_template_resolution_returns_flagged_tenant(two_workspaces):
    from api.db.db_models import DB, Workspace
    from management.server.services.provisioning import _model_template_tenant_id
    org_id, (wa, ta), (wb, tb) = two_workspaces

    # aucun flag -> None (fallback créateur dans provision_workspace)
    assert _model_template_tenant_id() is None

    # flag posé sur ws-b -> son tenant est la source
    with DB.connection_context():
        Workspace.update(settings_json={"model_template": True}).where(Workspace.id == wb).execute()
    assert _model_template_tenant_id() == tb

    # un workspace archivé flaggé ne compte pas
    with DB.connection_context():
        Workspace.update(settings_json={"model_template": True}, status="0").where(Workspace.id == wb).execute()
    assert _model_template_tenant_id() is None


def test_set_model_template_route_is_exclusive_and_superuser(panel_client_ws):
    """POST /model-template : superadmin only, exclusif (un seul à la fois)."""
    client, tokens, wsids = panel_client_ws
    wa, wb = wsids

    # non-superadmin refusé
    assert client.post(f"/api/admin/workspaces/{wa}/model-template",
                       headers={"Authorization": f"Bearer {tokens['org_admin']}"}).status_code == 403

    # superadmin pose sur A
    assert client.post(f"/api/admin/workspaces/{wa}/model-template",
                       headers={"Authorization": f"Bearer {tokens['superuser']}"}).status_code == 200
    # puis sur B -> A doit être désactivé (exclusif)
    assert client.post(f"/api/admin/workspaces/{wb}/model-template",
                       headers={"Authorization": f"Bearer {tokens['superuser']}"}).status_code == 200

    from api.db.db_models import DB, Workspace
    with DB.connection_context():
        a = Workspace.get_by_id(wa); b = Workspace.get_by_id(wb)
    assert not (a.settings_json or {}).get("model_template")
    assert (b.settings_json or {}).get("model_template") is True


@pytest.fixture()
def panel_client_ws(org_with_entitlement_and_users):
    from fastapi.testclient import TestClient
    from api.db.db_models import DB, Workspace
    from common.misc_utils import get_uuid
    from management.server.main import app
    org_id, tokens = org_with_entitlement_and_users
    wa, wb = get_uuid(), get_uuid()
    with DB.connection_context():
        Workspace.create(id=wa, org_id=org_id, tenant_id=get_uuid(), name="wsa", created_by="t")
        Workspace.create(id=wb, org_id=org_id, tenant_id=get_uuid(), name="wsb", created_by="t")
    yield TestClient(app), tokens, (wa, wb)
    with DB.connection_context():
        Workspace.delete().where(Workspace.id.in_([wa, wb])).execute()
