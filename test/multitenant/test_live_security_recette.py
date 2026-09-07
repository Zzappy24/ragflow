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

Outils : _live.py ; fixtures partagées : conftest.py (live_*). Chaque
workspace jetable est archivé puis purgé en fin de module.
"""
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _live  # noqa: E402
from _live import CI_EMAIL, api, bearer, beta_auth, code, refused  # noqa: E402

pytestmark = _live.live_markers()
SECRET_T1, SECRET_T2 = "PERROQUET-UN", "PERROQUET-DEUX"


# ------------------------------------------------- 1. jetons : éditeurs / viewers
class TestEmbedTokens:
    def test_editor_gets_only_a_beta(self, editor_auth, live_content1, live_beta1):
        r = api(editor_auth, "GET", "/system/tokens")
        assert refused(r), "la liste des clés API (clés en clair) doit rester admin"
        r = api(editor_auth, "GET", "/system/tokens/beta", params={"dialog_id": live_content1["chat"] or "x"})
        assert code(r) == 0 and set(r.json()["data"]) == {"beta"} and r.json()["data"]["beta"], r.text

    def test_viewer_gets_nothing(self, viewer_auth, live_content1):
        assert refused(api(viewer_auth, "GET", "/system/tokens"))
        assert refused(api(viewer_auth, "GET", "/system/tokens/beta", params={"dialog_id": live_content1["chat"] or "x"}))


# ------------------------------------------------------ 2. portée du jeton beta
class TestBetaScope:
    def test_other_tenant_chatbot_refused(self, live_beta1, live_content2):
        if not live_content2["chat"]:
            pytest.skip("pas de modèle de chat sur le workspace de recette")
        b = beta_auth(live_beta1)
        assert refused(api(b, "GET", f"/chatbots/{live_content2['chat']}/info"))
        r = api(b, "POST", f"/chatbots/{live_content2['chat']}/completions", json={"question": "mot de passe ?", "stream": False})
        assert refused(r) and SECRET_T2 not in r.text, r.text[:200]

    def test_retrieval_only_on_own_bases(self, live_beta1, live_content1, live_content2):
        b = beta_auth(live_beta1)
        r = api(b, "POST", "/searchbots/retrieval_test", json={"question": "mot de passe du coffre", "kb_id": [live_content2["kb"]], "page": 1, "size": 5})
        assert refused(r) and SECRET_T2 not in r.text, r.text[:200]
        r = api(b, "POST", "/searchbots/retrieval_test", json={"question": "mot de passe du coffre", "kb_id": [live_content1["kb"]], "page": 1, "size": 5})
        assert code(r) == 0, r.text[:300]  # UnboundLocalError('size') le 2026-09-07

    def test_download_only_own_documents(self, live_beta1, live_content1, live_content2):
        b = beta_auth(live_beta1)
        r = api(b, "GET", f"/documents/{live_content2['doc']}")
        assert r.status_code != 200 or SECRET_T2 not in r.text
        r = api(b, "GET", f"/documents/{live_content1['doc']}")
        assert r.status_code == 200 and SECRET_T1 in r.text, r.text[:120]

    def test_beta_cannot_use_login_routes(self, live_beta1):
        r = api(beta_auth(live_beta1), "GET", "/datasets")
        assert r.status_code in (401, 403) or code(r) in (401, 403), r.text[:120]

    def test_own_chatbot_answers_from_its_base(self, live_beta1, live_content1):
        if not live_content1["chat"]:
            pytest.skip("pas de modèle de chat sur le workspace de recette")
        b = beta_auth(live_beta1)
        r = api(b, "POST", f"/chatbots/{live_content1['chat']}/completions", json={"question": "bonjour", "stream": False})
        assert code(r) == 0, r.text[:200]
        sid = None
        try:
            sid = json.loads(r.json()["data"].split("data:", 1)[1])["data"]["session_id"]
        except Exception:
            pass
        r = api(b, "POST", f"/chatbots/{live_content1['chat']}/completions", json={"question": "Quel est le mot de passe du coffre ? Réponds avec le mot exact.", "stream": False, "session_id": sid})
        assert code(r) == 0, r.text[:300]
        if SECRET_T1 not in r.text:
            pytest.skip("le LLM n'a pas restitué le secret (qualité du modèle, pas un défaut de sécurité)")


# ------------------------------------------------------- 3. clés API scopées
class TestScopedApiKeys:
    def test_permissions_expiry_and_revocation(self, live_primary):
        auth = live_primary["auth"]
        r = api(auth, "POST", "/api_keys", json={"name": "recette lecture seule", "permissions": ["dataset.read"]})
        assert code(r) == 0, r.text
        data = r.json()["data"]
        key = data.get("key") or data.get("api_key") or data.get("token")
        scope_id = data.get("id") or data.get("scope_id")
        k = bearer(key, live_primary["ws_id"])
        assert code(api(k, "GET", "/datasets")) == 0
        assert refused(api(k, "POST", "/datasets", json={"name": "via clé"})), "permission dataset.create absente de la clé"
        assert refused(api(k, "GET", "/system/tokens"))
        from api.db.db_models import DB, ApiKeyScope
        with DB.connection_context():
            ApiKeyScope.update(expires_at=datetime.now() - timedelta(days=1)).where(ApiKeyScope.id == scope_id).execute()
        r = api(k, "GET", "/datasets")
        assert r.status_code == 401 or code(r) == 401, "clé expirée acceptée"
        with DB.connection_context():
            ApiKeyScope.update(expires_at=None).where(ApiKeyScope.id == scope_id).execute()
        assert code(api(k, "GET", "/datasets")) == 0
        api(auth, "DELETE", f"/api_keys/{scope_id}")
        r = api(k, "GET", "/datasets")
        assert r.status_code == 401 or code(r) == 401, "clé révoquée acceptée"


# ------------------------------------------------------------ 4. RBAC / fuites
class TestRolesAndSerialization:
    @pytest.mark.parametrize("who", ["viewer_auth", "editor_auth", "ws_auth"])
    def test_users_me_never_leaks_secrets(self, request, who):
        auth = request.getfixturevalue(who)
        r = api(auth, "GET", "/users/me")
        d = r.json().get("data") or {}
        assert r.status_code == 200 and "password" not in d and "access_token" not in d, list(d)

    @pytest.mark.parametrize("who", ["viewer_auth", "editor_auth", "ws_auth"])
    def test_superuser_only_routes(self, request, who):
        auth = request.getfixturevalue(who)
        for path in ("/system/status", "/system/config/log"):
            assert code(api(auth, "GET", path)) in (401, 403), path

    def test_viewer_is_read_only(self, viewer_auth):
        assert code(api(viewer_auth, "GET", "/datasets")) == 0
        assert code(api(viewer_auth, "POST", "/datasets", json={"name": "viewer"})) == 403
        for path in ("/langfuse/api-key", "/mcp/servers"):
            assert code(api(viewer_auth, "GET", path)) in (401, 403), path

    def test_editor_creates_but_no_secrets(self, editor_auth):
        r = api(editor_auth, "POST", "/datasets", json={"name": f"recette-live editor {int(time.time()) % 100000}"})
        assert code(r) == 0, r.text
        api(editor_auth, "DELETE", "/datasets", json={"ids": [r.json()["data"]["id"]]})
        assert code(api(editor_auth, "GET", "/langfuse/api-key")) in (401, 403)


# --------------------------------------------------- 5. IDOR inter-workspace
class TestCrossTenantIsolation:
    def test_workspace_one_cannot_reach_workspace_two(self, live_primary, live_second, live_content1, live_content2):
        a = live_primary["auth"]
        kb2, doc2, chat2 = live_content2["kb"], live_content2["doc"], live_content2["chat"]
        probes = [
            ("dataset", api(a, "GET", f"/datasets/{kb2}")),
            ("documents", api(a, "GET", f"/datasets/{kb2}/documents")),
            ("download", api(a, "GET", f"/datasets/{kb2}/documents/{doc2}")),
            ("search", api(a, "POST", f"/datasets/{kb2}/search", json={"question": "mot de passe"})),
            ("image", api(a, "GET", f"/documents/images/{kb2}-x")),
            ("add member to tenant", api(a, "POST", f"/tenants/{live_second['tenant_id']}/users", json={"email": CI_EMAIL})),
        ]
        if chat2:
            probes += [("chat", api(a, "GET", f"/chats/{chat2}")), ("sessions", api(a, "GET", f"/chats/{chat2}/sessions"))]
        leaks = [name for name, r in probes if not refused(r) or SECRET_T2 in r.text]
        assert not leaks, f"ressources de l'autre workspace atteignables : {leaks}"
        ids = [d["id"] for d in (api(a, "GET", "/datasets").json().get("data") or [])]
        assert live_content1["kb"] in ids and kb2 not in ids
        data = api(a, "GET", "/chats").json().get("data") or {}
        chats = data if isinstance(data, list) else data.get("chats", [])
        assert chat2 not in [c["id"] for c in chats]


# ------------------------------------------------- 6. panel : modèles par défaut
class TestPanelDefaultModels:
    def test_unusable_default_model_is_refused(self, live_panel, live_primary):
        """Un id accepté ici mais irrésolvable à l'exécution ne cassait qu'au
        premier message de chat, chez le client (recette 2026-09-07)."""
        r = live_panel("PUT", f"/workspaces/{live_primary['ws_id']}/models/defaults", json={"llm_id": "inconnu@OpenAI-API-Compatible"})
        assert r.status_code == 400, r.text
        r = live_panel("GET", f"/workspaces/{live_primary['ws_id']}/models/defaults")
        assert r.status_code == 200 and r.json().get("llm_id") != "inconnu@OpenAI-API-Compatible"


# ----------------------------------------------------------- 7. purge physique
class TestPhysicalPurge:
    def test_purge_erases_index_bucket_and_rows(self, live_panel, live_primary):
        from api.db.db_models import DB, Dialog, Document, Knowledgebase, Workspace
        from common import settings
        from rag.nlp import search
        if getattr(settings, "docStoreConn", None) is None:
            settings.init_settings()  # hors serveur, les connexions ne sont pas initialisées
        ws = _live.make_disposable_workspace(live_panel, live_primary, f"recette-live purge {int(time.time()) % 100000}")
        c = _live.setup_content(ws["auth"], "T3", "PERROQUET-TROIS")
        tenant, kb = ws["tenant_id"], c["kb"]
        assert settings.docStoreConn.index_exist(search.index_name(tenant), kb), "l'index du tenant devrait exister après le parse"
        assert settings.STORAGE_IMPL.bucket_exists(kb), "le bucket de la base devrait exister"

        r = live_panel("DELETE", f"/orgs/{ws['org_id']}/workspaces/{ws['ws_id']}/purge", params={"confirm": "DELETE"})
        assert r.status_code >= 400, "un workspace encore actif ne doit pas être purgeable"
        r = _live.purge_workspace(live_panel, ws["org_id"], ws["ws_id"])
        assert r.status_code == 204, r.text  # 502 'cannot unpack non-iterable NoneType' le 2026-09-07

        assert not settings.docStoreConn.index_exist(search.index_name(tenant), kb), "index ES/Infinity du tenant toujours présent"
        assert not settings.STORAGE_IMPL.bucket_exists(kb), "bucket MinIO de la base toujours présent"
        with DB.connection_context():
            counts = (
                Knowledgebase.select().where(Knowledgebase.tenant_id == tenant).count(),
                Document.select().where(Document.kb_id == kb).count(),
                Dialog.select().where(Dialog.tenant_id == tenant).count(),
                Workspace.select().where(Workspace.id == ws["ws_id"]).count(),
            )
        assert counts == (0, 0, 0, 0), f"lignes SQL restantes (kb, docs, dialogs, workspace) : {counts}"
        assert refused(api(ws["auth"], "GET", "/datasets")), "le workspace purgé reste joignable"
