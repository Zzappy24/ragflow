"""CUSTOM B2B SaaS — purge physique d'un workspace (2026-09-06).

Avant : ``purge_workspace`` (panel) n'effaçait que des lignes SQL ; chunks,
vecteurs, index de métadonnées, fichiers MinIO, chats, agents, recherches,
mémoires et clés API du tenant restaient en place derrière une suppression
apparemment réussie. Contrat épinglé ici :

1. Le panel appelle la purge physique de l'API AVANT tout ``.delete()`` et
   n'efface rien si elle n'est pas confirmée (``TenantPurgeError`` → 502) —
   y compris quand l'env est absente (pas de « best effort » silencieux).
2. Le service API refuse un workspace encore actif, supprime l'index de
   chunks ENTIER et l'index de métadonnées, vide les buckets des bases, puis
   les lignes SQL de contenu ; toute erreur physique remonte.
3. La route interne exige le secret partagé.
"""
import ast
import pathlib
from types import SimpleNamespace

import pytest

import management.server.services.tenant_purge_client as purge_client
from api.db.joint_services import workspace_purge_service as svc

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROVISIONING = ROOT / "management/server/services/provisioning.py"
INTERNAL_API = ROOT / "api/apps/restful_apis/internal_api.py"
MGMT_MAIN = ROOT / "management/server/main.py"


def _func_source(path: pathlib.Path, name: str) -> str:
    src = path.read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} introuvable dans {path.name}")


# ---------------------------------------------------------------- panel → API
class TestPurgeClient:
    def _ok_response(self, code=0, status=200, data=None):
        return SimpleNamespace(status_code=status, json=lambda: {"code": code, "message": "m", "data": data or {"x": 1}})

    def test_missing_env_raises_and_never_calls_api(self, monkeypatch):
        monkeypatch.delenv("RAGFLOW_API_URL", raising=False)
        monkeypatch.setenv("INTERNAL_API_SECRET", "s")
        calls = []
        monkeypatch.setattr(purge_client.httpx, "post", lambda *a, **k: calls.append(a))
        with pytest.raises(purge_client.TenantPurgeError):
            purge_client.purge_tenant_content_via_api("t1")
        assert calls == []

    def test_success_returns_summary_and_sends_secret(self, monkeypatch):
        monkeypatch.setenv("RAGFLOW_API_URL", "http://api/")
        monkeypatch.setenv("INTERNAL_API_SECRET", "sekret")
        seen = {}

        def fake_post(url, headers=None, timeout=None):
            seen.update(url=url, headers=headers, timeout=timeout)
            return self._ok_response(data={"datasets": 2})

        monkeypatch.setattr(purge_client.httpx, "post", fake_post)
        assert purge_client.purge_tenant_content_via_api("t1") == {"datasets": 2}
        assert seen["url"] == "http://api/api/v1/internal/workspaces/t1/purge-data"
        assert seen["headers"] == {"X-Internal-Secret": "sekret"}
        assert seen["timeout"] and seen["timeout"] >= 60

    @pytest.mark.parametrize("status,code", [(500, 500), (200, 409), (200, 404), (401, 109)])
    def test_any_non_confirmation_raises(self, monkeypatch, status, code):
        monkeypatch.setenv("RAGFLOW_API_URL", "http://api")
        monkeypatch.setenv("INTERNAL_API_SECRET", "s")
        monkeypatch.setattr(purge_client.httpx, "post", lambda *a, **k: self._ok_response(code=code, status=status))
        with pytest.raises(purge_client.TenantPurgeError):
            purge_client.purge_tenant_content_via_api("t1")

    def test_network_error_raises(self, monkeypatch):
        monkeypatch.setenv("RAGFLOW_API_URL", "http://api")
        monkeypatch.setenv("INTERNAL_API_SECRET", "s")

        def boom(*a, **k):
            raise ConnectionError("down")

        monkeypatch.setattr(purge_client.httpx, "post", boom)
        with pytest.raises(purge_client.TenantPurgeError):
            purge_client.purge_tenant_content_via_api("t1")


class TestPanelOrdering:
    def test_purge_workspace_calls_api_before_any_sql_delete(self):
        body = _func_source(PROVISIONING, "purge_workspace")
        call = body.index("purge_tenant_content_via_api(tenant_id)")
        first_delete = body.index(".delete()")
        assert call < first_delete, "la purge physique doit précéder le premier .delete() SQL"
        # aucun try/except autour de l'appel : l'erreur DOIT remonter
        before = body[:call]
        assert "try:" not in before.split("tenant_id = ws.tenant_id")[-1]

    def test_panel_maps_purge_error_to_502(self):
        src = MGMT_MAIN.read_text()
        assert "@app.exception_handler(TenantPurgeError)" in src
        assert "status_code=502" in src


# ---------------------------------------------------------------- API service
class _Storage:
    def __init__(self):
        self.buckets = {"kb1", "kb2"}
        self.objects = {("folder1", "f.pdf")}
        self.removed_buckets = []
        self.removed_objects = []

    def bucket_exists(self, b):
        return b in self.buckets

    def remove_bucket(self, b):
        self.buckets.discard(b)
        self.removed_buckets.append(b)

    def obj_exist(self, b, n):
        return (b, n) in self.objects

    def rm(self, b, n):
        self.objects.discard((b, n))
        self.removed_objects.append((b, n))


class _DocStore:
    def __init__(self, fail=False):
        self.dropped = []
        self.fail = fail

    def delete_idx(self, name, dataset_id):
        if self.fail:
            raise RuntimeError("es down")
        assert dataset_id == "", "l'index doit être supprimé ENTIER (dataset_id vide)"
        self.dropped.append(name)


@pytest.fixture
def wired(monkeypatch):
    """Câble tous les collaborateurs du service sur des doubles en mémoire."""
    storage, store = _Storage(), _DocStore()
    ws = SimpleNamespace(id="ws1", status="0")
    monkeypatch.setattr(svc.WorkspaceService, "get_by_tenant_id", staticmethod(lambda t: (True, ws)))
    monkeypatch.setattr(svc.settings, "STORAGE_IMPL", storage, raising=False)
    monkeypatch.setattr(svc.settings, "docStoreConn", store, raising=False)
    monkeypatch.setattr(svc.KnowledgebaseService, "get_kb_ids", staticmethod(lambda t: ["kb1", "kb2"]))
    monkeypatch.setattr(svc.DocMetadataService, "_get_doc_meta_index_name", staticmethod(lambda t: f"ragflow_doc_meta_{t}"))
    monkeypatch.setattr(svc.MemoryService, "get_by_tenant_id", staticmethod(lambda t: []))
    monkeypatch.setattr(svc.MemoryService, "delete_by_ids", staticmethod(lambda ids: len(ids)))

    monkeypatch.setattr(svc, "_iter_tenant_files", lambda t: [
        SimpleNamespace(parent_id="folder1", location="f.pdf", type="pdf"),
        SimpleNamespace(parent_id="root", location="", type="folder"),
    ])
    deleted = {}
    monkeypatch.setattr(svc.DocumentService, "get_all_doc_ids_by_kb_ids", staticmethod(lambda kbs: [{"id": "d1"}, {"id": "d2"}]))
    monkeypatch.setattr(svc.FileService, "get_all_file_ids_by_tenant_id", staticmethod(lambda t: [{"id": "f1"}]))
    monkeypatch.setattr(svc.TaskService, "delete_by_doc_ids", staticmethod(lambda ids: deleted.setdefault("tasks", ids) and len(ids)))
    monkeypatch.setattr(svc.File2DocumentService, "delete_by_document_ids_or_file_ids", staticmethod(lambda d, f: 3))
    monkeypatch.setattr(svc.DocumentService, "delete_by_ids", staticmethod(lambda ids: deleted.setdefault("docs", ids) and len(ids)))
    monkeypatch.setattr(svc.FileService, "delete_by_ids", staticmethod(lambda ids: len(ids)))
    monkeypatch.setattr(svc.KnowledgebaseService, "delete_by_ids", staticmethod(lambda ids: deleted.setdefault("kbs", ids) and len(ids)))
    monkeypatch.setattr(svc, "delete_user_agents", lambda t: {"agents_deleted_count": 4})
    monkeypatch.setattr(svc, "delete_user_dialogs", lambda t: {"dialogs_deleted_count": 5})
    for cls, meth in ((svc.APITokenService, "delete_by_tenant_id"), (svc.SearchService, "delete_by_tenant_id"),
                      (svc.MCPServerService, "delete_by_tenant_id"), (svc.TenantLLMService, "delete_by_tenant_id"),
                      (svc.TenantModelProviderService, "delete_by_tenant_id"), (svc.TenantLangfuseService, "delete_ty_tenant_id")):
        monkeypatch.setattr(cls, meth, staticmethod(lambda t: 1))
    return SimpleNamespace(storage=storage, store=store, ws=ws, deleted=deleted)


class TestPurgeTenantContent:
    def test_refuses_active_workspace_without_touching_anything(self, wired):
        wired.ws.status = "1"
        with pytest.raises(svc.PurgeRefused):
            svc.purge_tenant_content("t1")
        assert wired.storage.removed_buckets == [] and wired.store.dropped == []

    def test_refuses_unknown_tenant(self, wired, monkeypatch):
        monkeypatch.setattr(svc.WorkspaceService, "get_by_tenant_id", staticmethod(lambda t: (False, None)))
        with pytest.raises(svc.PurgeRefused):
            svc.purge_tenant_content("t1")

    def test_full_purge_drops_indexes_buckets_files_then_sql(self, wired):
        summary = svc.purge_tenant_content("t1")
        assert wired.storage.removed_buckets == ["kb1", "kb2"]
        assert wired.storage.removed_objects == [("folder1", "f.pdf")]
        assert wired.store.dropped == ["ragflow_t1", "ragflow_doc_meta_t1"]
        assert wired.deleted["docs"] == ["d1", "d2"] and wired.deleted["kbs"] == ["kb1", "kb2"]
        assert summary["datasets"] == 2 and summary["agents_deleted"] == 4 and summary["dialogs_deleted"] == 5
        assert "_memory_ids" not in summary

    def test_doc_store_failure_propagates_before_sql(self, wired):
        wired.store.fail = True
        with pytest.raises(RuntimeError, match="es down"):
            svc.purge_tenant_content("t1")
        assert "docs" not in wired.deleted, "aucune ligne SQL de contenu ne doit partir si l'index n'a pas été supprimé"

    def test_idempotent_on_already_purged_storage(self, wired):
        wired.storage.buckets.clear()
        wired.storage.objects.clear()
        summary = svc.purge_tenant_content("t1")
        assert summary["buckets_removed"] == 0 and summary["file_blobs_removed"] == 0
        assert wired.store.dropped == ["ragflow_t1", "ragflow_doc_meta_t1"]


class TestInternalRoute:
    def test_route_checks_shared_secret_first(self):
        body = _func_source(INTERNAL_API, "internal_purge_workspace_data")
        assert body.index("_check_internal_secret()") < body.index("purge_tenant_content")
        assert "thread_pool_exec(purge_tenant_content" in body, "la purge est bloquante : hors event loop"

    def test_internal_prefix_is_blocked_at_the_edge(self):
        """Le nginx du front (seul chemin depuis internet) répond 404 sur
        /api/v1/internal/ : le secret partagé n'est plus la seule barrière."""
        conf = (ROOT / "helm/ragflow/charts/ragflow-frontend/templates/configmap-nginx.yaml").read_text()
        block = conf.index("location ^~ /api/v1/internal/")
        assert "return 404;" in conf[block:block + 200]
        assert block < conf.index("location /api/ {"), "le blocage doit précéder le proxy générique"


class TestPurgeIsAudited:
    @pytest.mark.parametrize("path,func", [
        (ROOT / "management/server/routers/workspaces.py", "purge_workspace_route"),
        (ROOT / "management/server/routers/archives.py", "purge_archived_workspace"),
    ])
    def test_single_workspace_purge_records_ws_purge(self, path, func):
        body = _func_source(path, func)
        assert "audit_svc.WS_PURGE" in body, f"{func} doit tracer WS_PURGE (comme ORG_PURGE)"
        assert body.index("purge_workspace(ws_id)") < body.index("audit_svc.record("), "auditer après une purge réussie"
