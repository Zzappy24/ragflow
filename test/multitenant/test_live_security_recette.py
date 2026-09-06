"""CUSTOM B2B SaaS — recette sécurité EN DIRECT (serveur + panel + moteurs).

Portage de la recette manuelle du 2026-09-07 qui a trouvé trois régressions
invisibles pour les 60+ pins statiques (purge aveugle aux workspaces
archivés, ``size`` non déclaré nonlocal dans le retrieval beta, NameError
dans la liste des modèles du panel). Ces tests parlent au vrai serveur :

- API   : ``HOST_ADDRESS`` (défaut http://127.0.0.1:9380), comptes dérivés
  par ``RAGFLOW_TEST_LOCAL_AUTH=1`` (``CI_EMAIL`` = ws_admin du workspace de
  recette, ``EDITOR_EMAIL``, ``VIEWER_EMAIL`` — cf. conftest.py) ;
- panel : ``MANAGEMENT_HOST`` (défaut http://127.0.0.1:9381), session opaque
  ouverte en base pour le premier superuser ;
- un modèle d'embedding configuré sur le workspace de recette (copié sur les
  workspaces jetables créés ici). Sans modèle de chat, seuls les contrôles
  qui appellent le LLM sont sautés.

Chaque workspace jetable est archivé puis purgé en fin de module.
"""
import io
import os
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
API = f"{HOST_ADDRESS}/api/v1"
MANAGEMENT_HOST = os.getenv("MANAGEMENT_HOST", "http://127.0.0.1:9381")
PANEL = f"{MANAGEMENT_HOST}/api/admin"
CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")
SECRET_T1, SECRET_T2 = "PERROQUET-UN", "PERROQUET-DEUX"


def _reachable(url: str) -> bool:
    p = urlparse(url)
    try:
        with socket.create_connection((p.hostname or "127.0.0.1", p.port or 80), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(os.getenv("RAGFLOW_TEST_LOCAL_AUTH") != "1", reason="recette live : RAGFLOW_TEST_LOCAL_AUTH=1 requis"),
    pytest.mark.skipif(not _reachable(HOST_ADDRESS), reason=f"API injoignable : {HOST_ADDRESS}"),
    pytest.mark.skipif(not _reachable(MANAGEMENT_HOST), reason=f"panel injoignable : {MANAGEMENT_HOST}"),
]


# --------------------------------------------------------------------- outils
def _code(r):
    try:
        return r.json().get("code", r.status_code)
    except Exception:
        return r.status_code


def _refused(r) -> bool:
    return _code(r) not in (0, 200)


class _Panel:
    """Session panel opaque ouverte en base pour le premier superuser."""

    def __init__(self):
        from api.db.db_models import DB, User
        from management.server.auth.sessions import open_session
        with DB.connection_context():
            su = User.select().where(User.is_superuser == True).first()  # noqa: E712
        if su is None:
            pytest.skip("aucun superuser en base pour piloter le panel")
        self.token = open_session(su.id)

    def __call__(self, method, path, **kw):
        return requests.request(method, PANEL + path, headers={"Authorization": f"Bearer {self.token}"}, timeout=120, **kw)


def _api(auth, method, path, **kw):
    return requests.request(method, API + path, auth=auth, timeout=180, **kw)


def _beta_auth(beta: str):
    class _B(requests.auth.AuthBase):
        def __call__(self, r):
            r.headers["Authorization"] = f"Bearer {beta}"
            return r
    return _B()


def _bearer(token: str, ws_id: str | None = None):
    class _K(requests.auth.AuthBase):
        def __call__(self, r):
            r.headers["Authorization"] = f"Bearer {token}"
            if ws_id:
                r.headers["X-Workspace-Id"] = ws_id
            return r
    return _K()


def _wait_parsed(auth, kb, doc, timeout=240) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = _api(auth, "GET", f"/datasets/{kb}/documents", params={"id": doc})
        docs = ((r.json().get("data") or {}).get("docs") or []) if r.ok else []
        if docs:
            d = docs[0]
            if d.get("run") == "3" or float(d.get("progress") or 0) >= 1:
                return True
            if d.get("run") == "4" or float(d.get("progress") or 0) < 0:
                pytest.skip(f"parse en échec (modèle d'embedding absent ?) : {str(d.get('progress_msg', ''))[-200:]}")
        time.sleep(3)
    pytest.skip("parse trop long (>240 s) : executor absent ?")


def _setup_content(auth, tag: str, secret: str) -> dict:
    """Base + document parsé + chat (le chat exige une base parsée)."""
    run = int(time.time()) % 100000
    r = _api(auth, "POST", "/datasets", json={"name": f"recette-live {tag} {run}"})
    assert _code(r) == 0, r.text
    kb = r.json()["data"]["id"]
    files = {"file": (f"note-{tag}.txt", io.BytesIO(f"Le mot de passe du coffre {tag} est {secret}. Le contrat expire en 2027.".encode()), "text/plain")}
    r = _api(auth, "POST", f"/datasets/{kb}/documents", files=files)
    assert _code(r) == 0, r.text
    doc = r.json()["data"][0]["id"]
    _api(auth, "POST", f"/datasets/{kb}/documents/parse", json={"document_ids": [doc]})
    _wait_parsed(auth, kb, doc)
    r = _api(auth, "POST", "/chats", json={"name": f"recette-live chat {tag} {run}", "dataset_ids": [kb]})
    chat = r.json()["data"]["id"] if _code(r) == 0 else None
    return {"kb": kb, "doc": doc, "chat": chat, "secret": secret}


def _tenant_of_workspace(ws_id: str):
    from api.db.db_models import DB, Workspace
    with DB.connection_context():
        ws = Workspace.get_or_none(Workspace.id == ws_id)
    assert ws is not None, f"workspace {ws_id} introuvable"
    return ws


def _copy_models(src_tenant: str, dst_tenant: str) -> dict:
    """Copie les modèles configurés (tenant_llm) et renvoie les défauts source."""
    from api.db.db_models import DB, Tenant, TenantLLM
    from management.server.services.sync_tenant_model_tables import sync_tenant_llm_to_new_tables
    with DB.connection_context():
        rows = list(TenantLLM.select().where(TenantLLM.tenant_id == src_tenant).dicts())
        src = Tenant.get_or_none(Tenant.id == src_tenant)
        for row in rows:
            row = {k: v for k, v in row.items() if k not in ("id", "create_time", "create_date", "update_time", "update_date")}
            row["tenant_id"] = dst_tenant
            TenantLLM.insert(**row).on_conflict_ignore().execute()
    factories = {r["llm_factory"] for r in rows}
    for f in factories:
        sync_tenant_llm_to_new_tables(dst_tenant, f)
    return {"llm_id": src.llm_id or "", "embd_id": src.embd_id or ""} if src else {}


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def panel():
    return _Panel()


@pytest.fixture(scope="module")
def primary(ws_auth, workspace_id):
    ws = _tenant_of_workspace(workspace_id)
    return {"auth": ws_auth, "ws_id": workspace_id, "tenant_id": ws.tenant_id, "org_id": ws.org_id}


def _make_disposable_workspace(panel, primary, name: str) -> dict:
    r = panel("POST", f"/orgs/{primary['org_id']}/workspaces", json={"name": name})
    assert r.status_code == 201, r.text
    ws = r.json()
    r = panel("POST", f"/workspaces/{ws['id']}/members", json={"email": CI_EMAIL, "role": "ws_admin"})
    assert r.status_code in (201, 409, 400), r.text
    defaults = _copy_models(primary["tenant_id"], ws["tenant_id"])
    defaults = {k: v for k, v in defaults.items() if v}
    if defaults:
        r = panel("PUT", f"/workspaces/{ws['id']}/models/defaults", json=defaults)
        assert r.status_code == 200, r.text
    auth = type(primary["auth"])(primary["auth"]._token, ws["id"])
    return {"auth": auth, "ws_id": ws["id"], "tenant_id": ws["tenant_id"], "org_id": primary["org_id"]}


def _purge_workspace(panel, org_id: str, ws_id: str):
    panel("DELETE", f"/orgs/{org_id}/workspaces/{ws_id}")
    return panel("DELETE", f"/orgs/{org_id}/workspaces/{ws_id}/purge", params={"confirm": "DELETE"})


@pytest.fixture(scope="module")
def second(panel, primary):
    ws = _make_disposable_workspace(panel, primary, f"recette-live IDOR {int(time.time()) % 100000}")
    yield ws
    _purge_workspace(panel, ws["org_id"], ws["ws_id"])


@pytest.fixture(scope="module")
def content1(primary):
    c = _setup_content(primary["auth"], "T1", SECRET_T1)
    yield c
    _api(primary["auth"], "DELETE", "/chats", json={"ids": [c["chat"]]}) if c["chat"] else None
    _api(primary["auth"], "DELETE", "/datasets", json={"ids": [c["kb"]]})


@pytest.fixture(scope="module")
def content2(second):
    return _setup_content(second["auth"], "T2", SECRET_T2)


@pytest.fixture(scope="module")
def beta1(primary):
    r = _api(primary["auth"], "POST", "/system/tokens", json={})
    assert _code(r) == 0, r.text
    return r.json()["data"]["beta"]


# ------------------------------------------------- 1. jetons : éditeurs / viewers
class TestEmbedTokens:
    def test_editor_gets_only_a_beta(self, editor_auth, content1, beta1):
        r = _api(editor_auth, "GET", "/system/tokens")
        assert _refused(r), "la liste des clés API (clés en clair) doit rester admin"
        r = _api(editor_auth, "GET", "/system/tokens/beta", params={"dialog_id": content1["chat"] or "x"})
        assert _code(r) == 0 and set(r.json()["data"]) == {"beta"} and r.json()["data"]["beta"], r.text

    def test_viewer_gets_nothing(self, viewer_auth, content1):
        assert _refused(_api(viewer_auth, "GET", "/system/tokens"))
        assert _refused(_api(viewer_auth, "GET", "/system/tokens/beta", params={"dialog_id": content1["chat"] or "x"}))


# ------------------------------------------------------ 2. portée du jeton beta
class TestBetaScope:
    def test_other_tenant_chatbot_refused(self, beta1, content2):
        if not content2["chat"]:
            pytest.skip("pas de modèle de chat sur le workspace de recette")
        b = _beta_auth(beta1)
        assert _refused(_api(b, "GET", f"/chatbots/{content2['chat']}/info"))
        r = _api(b, "POST", f"/chatbots/{content2['chat']}/completions", json={"question": "mot de passe ?", "stream": False})
        assert _refused(r) and SECRET_T2 not in r.text, r.text[:200]

    def test_retrieval_only_on_own_bases(self, beta1, content1, content2):
        b = _beta_auth(beta1)
        r = _api(b, "POST", "/searchbots/retrieval_test", json={"question": "mot de passe du coffre", "kb_id": [content2["kb"]], "page": 1, "size": 5})
        assert _refused(r) and SECRET_T2 not in r.text, r.text[:200]
        r = _api(b, "POST", "/searchbots/retrieval_test", json={"question": "mot de passe du coffre", "kb_id": [content1["kb"]], "page": 1, "size": 5})
        assert _code(r) == 0, r.text[:300]  # UnboundLocalError('size') le 2026-09-07

    def test_download_only_own_documents(self, beta1, content1, content2):
        b = _beta_auth(beta1)
        r = _api(b, "GET", f"/documents/{content2['doc']}")
        assert r.status_code != 200 or SECRET_T2 not in r.text
        r = _api(b, "GET", f"/documents/{content1['doc']}")
        assert r.status_code == 200 and SECRET_T1 in r.text, r.text[:120]

    def test_beta_cannot_use_login_routes(self, beta1):
        r = _api(_beta_auth(beta1), "GET", "/datasets")
        assert r.status_code in (401, 403) or _code(r) in (401, 403), r.text[:120]

    def test_own_chatbot_answers_from_its_base(self, beta1, content1):
        if not content1["chat"]:
            pytest.skip("pas de modèle de chat sur le workspace de recette")
        b = _beta_auth(beta1)
        r = _api(b, "POST", f"/chatbots/{content1['chat']}/completions", json={"question": "bonjour", "stream": False})
        assert _code(r) == 0, r.text[:200]
        sid = None
        try:
            import json as _json
            sid = _json.loads(r.json()["data"].split("data:", 1)[1])["data"]["session_id"]
        except Exception:
            pass
        r = _api(b, "POST", f"/chatbots/{content1['chat']}/completions", json={"question": "Quel est le mot de passe du coffre ? Réponds avec le mot exact.", "stream": False, "session_id": sid})
        assert _code(r) == 0, r.text[:300]
        if SECRET_T1 not in r.text:
            pytest.skip("le LLM n'a pas restitué le secret (qualité du modèle, pas un défaut de sécurité)")


# ------------------------------------------------------- 3. clés API scopées
class TestScopedApiKeys:
    def test_permissions_expiry_and_revocation(self, primary):
        auth = primary["auth"]
        r = _api(auth, "POST", "/api_keys", json={"name": "recette lecture seule", "permissions": ["dataset.read"]})
        assert _code(r) == 0, r.text
        data = r.json()["data"]
        key = data.get("key") or data.get("api_key") or data.get("token")
        scope_id = data.get("id") or data.get("scope_id")
        k = _bearer(key, primary["ws_id"])
        assert _code(_api(k, "GET", "/datasets")) == 0
        assert _refused(_api(k, "POST", "/datasets", json={"name": "via clé"})), "permission dataset.create absente de la clé"
        assert _refused(_api(k, "GET", "/system/tokens"))
        from api.db.db_models import DB, ApiKeyScope
        with DB.connection_context():
            ApiKeyScope.update(expires_at=datetime.now() - timedelta(days=1)).where(ApiKeyScope.id == scope_id).execute()
        r = _api(k, "GET", "/datasets")
        assert r.status_code == 401 or _code(r) == 401, "clé expirée acceptée"
        with DB.connection_context():
            ApiKeyScope.update(expires_at=None).where(ApiKeyScope.id == scope_id).execute()
        assert _code(_api(k, "GET", "/datasets")) == 0
        _api(auth, "DELETE", f"/api_keys/{scope_id}")
        r = _api(k, "GET", "/datasets")
        assert r.status_code == 401 or _code(r) == 401, "clé révoquée acceptée"


# ------------------------------------------------------------ 4. RBAC / fuites
class TestRolesAndSerialization:
    @pytest.mark.parametrize("who", ["viewer_auth", "editor_auth", "ws_auth"])
    def test_users_me_never_leaks_secrets(self, request, who):
        auth = request.getfixturevalue(who)
        r = _api(auth, "GET", "/users/me")
        d = r.json().get("data") or {}
        assert r.status_code == 200 and "password" not in d and "access_token" not in d, list(d)

    @pytest.mark.parametrize("who", ["viewer_auth", "editor_auth", "ws_auth"])
    def test_superuser_only_routes(self, request, who):
        auth = request.getfixturevalue(who)
        for path in ("/system/status", "/system/config/log"):
            assert _code(_api(auth, "GET", path)) in (401, 403), path

    def test_viewer_is_read_only(self, viewer_auth):
        assert _code(_api(viewer_auth, "GET", "/datasets")) == 0
        assert _code(_api(viewer_auth, "POST", "/datasets", json={"name": "viewer"})) == 403
        for path in ("/langfuse/api-key", "/mcp/servers"):
            assert _code(_api(viewer_auth, "GET", path)) in (401, 403), path

    def test_editor_creates_but_no_secrets(self, editor_auth):
        r = _api(editor_auth, "POST", "/datasets", json={"name": f"recette-live editor {int(time.time()) % 100000}"})
        assert _code(r) == 0, r.text
        _api(editor_auth, "DELETE", "/datasets", json={"ids": [r.json()["data"]["id"]]})
        assert _code(_api(editor_auth, "GET", "/langfuse/api-key")) in (401, 403)


# --------------------------------------------------- 5. IDOR inter-workspace
class TestCrossTenantIsolation:
    def test_workspace_one_cannot_reach_workspace_two(self, primary, second, content1, content2):
        a = primary["auth"]
        kb2, doc2, chat2 = content2["kb"], content2["doc"], content2["chat"]
        probes = [
            ("dataset", _api(a, "GET", f"/datasets/{kb2}")),
            ("documents", _api(a, "GET", f"/datasets/{kb2}/documents")),
            ("download", _api(a, "GET", f"/datasets/{kb2}/documents/{doc2}")),
            ("search", _api(a, "POST", f"/datasets/{kb2}/search", json={"question": "mot de passe"})),
            ("image", _api(a, "GET", f"/documents/images/{kb2}-x")),
            ("add member to tenant", _api(a, "POST", f"/tenants/{second['tenant_id']}/users", json={"email": CI_EMAIL})),
        ]
        if chat2:
            probes += [("chat", _api(a, "GET", f"/chats/{chat2}")), ("sessions", _api(a, "GET", f"/chats/{chat2}/sessions"))]
        leaks = [name for name, r in probes if not _refused(r) or SECRET_T2 in r.text]
        assert not leaks, f"ressources de l'autre workspace atteignables : {leaks}"
        ids = [d["id"] for d in (_api(a, "GET", "/datasets").json().get("data") or [])]
        assert content1["kb"] in ids and kb2 not in ids
        data = _api(a, "GET", "/chats").json().get("data") or {}
        chats = data if isinstance(data, list) else data.get("chats", [])
        assert chat2 not in [c["id"] for c in chats]


# ------------------------------------------------- 6. panel : modèles par défaut
class TestPanelDefaultModels:
    def test_unusable_default_model_is_refused(self, panel, primary):
        """Un id accepté ici mais irrésolvable à l'exécution ne cassait qu'au
        premier message de chat, chez le client (recette 2026-09-07)."""
        r = panel("PUT", f"/workspaces/{primary['ws_id']}/models/defaults", json={"llm_id": "inconnu@OpenAI-API-Compatible"})
        assert r.status_code == 400, r.text
        r = panel("GET", f"/workspaces/{primary['ws_id']}/models/defaults")
        assert r.status_code == 200 and r.json().get("llm_id") != "inconnu@OpenAI-API-Compatible"


# ----------------------------------------------------------- 7. purge physique
class TestPhysicalPurge:
    def test_purge_erases_index_bucket_and_rows(self, panel, primary):
        from common import settings
        from rag.nlp import search
        if getattr(settings, "docStoreConn", None) is None:
            settings.init_settings()  # hors serveur, les connexions ne sont pas initialisées
        from api.db.db_models import DB, Dialog, Document, Knowledgebase, Workspace
        ws = _make_disposable_workspace(panel, primary, f"recette-live purge {int(time.time()) % 100000}")
        c = _setup_content(ws["auth"], "T3", "PERROQUET-TROIS")
        tenant, kb = ws["tenant_id"], c["kb"]
        conn = settings.docStoreConn
        index_exist = conn.index_exist
        assert index_exist(search.index_name(tenant), kb), "l'index du tenant devrait exister après le parse"
        assert settings.STORAGE_IMPL.bucket_exists(kb), "le bucket de la base devrait exister"

        r = panel("DELETE", f"/orgs/{ws['org_id']}/workspaces/{ws['ws_id']}/purge", params={"confirm": "DELETE"})
        assert r.status_code >= 400, "un workspace encore actif ne doit pas être purgeable"
        r = _purge_workspace(panel, ws["org_id"], ws["ws_id"])
        assert r.status_code == 204, r.text  # 502 'cannot unpack non-iterable NoneType' le 2026-09-07

        assert not index_exist(search.index_name(tenant), kb), "index ES/Infinity du tenant toujours présent"
        assert not settings.STORAGE_IMPL.bucket_exists(kb), "bucket MinIO de la base toujours présent"
        with DB.connection_context():
            counts = (
                Knowledgebase.select().where(Knowledgebase.tenant_id == tenant).count(),
                Document.select().where(Document.kb_id == kb).count(),
                Dialog.select().where(Dialog.tenant_id == tenant).count(),
                Workspace.select().where(Workspace.id == ws["ws_id"]).count(),
            )
        assert counts == (0, 0, 0, 0), f"lignes SQL restantes (kb, docs, dialogs, workspace) : {counts}"
        assert _refused(_api(ws["auth"], "GET", "/datasets")), "le workspace purgé reste joignable"
