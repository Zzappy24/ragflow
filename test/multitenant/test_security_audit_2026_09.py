"""CUSTOM B2B SaaS — audit sécurité méthodique du 2026-09-06 : pins des correctifs.

Chaque classe épingle une faille confirmée dans le code et son correctif. Les
tests sont statiques (AST/grep) sauf ceux de ``api/utils/beta_scope.py``
(logique pure, collaborateurs simulés). Un merge upstream qui retire une
garde fait échouer le test correspondant.

Références : rapport d'audit (mémoire projet), commits du 2026-09-06.
"""
import ast
import pathlib
import re
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text()


def _func(rel: str, name: str) -> str:
    src = _src(rel)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} introuvable dans {rel}")


# ----------------------------------------------------------------- CRITIQUE
class TestSSTIRenderDocx:
    """RCE : docxtpl/Jinja2 rendaient un .docx et un motif de nom de fichier
    fournis par l'utilisateur avec un Environment NU."""

    @pytest.mark.parametrize("rel", [
        "agent/tools/render_docx_template.py",
        "agent/plugin/embedded_plugins/llm_tools/render_docx_template.py",
    ])
    def test_only_sandboxed_environments(self, rel):
        src = _src(rel)
        assert "SandboxedEnvironment(" in src
        assert re.search(r"\bEnvironment\(loader=", src) is None, "Environment nu interdit"
        assert re.search(r"doc\.render\([^)]*jinja_env=SandboxedEnvironment\(\)", src), "doc.render doit passer jinja_env=SandboxedEnvironment()"


class TestEdgeBlocksInternalRoutes:
    """La passerelle envoie /api/ directement à l'API : le 404 nginx seul ne
    protégeait rien. Une règle HTTPRoute plus longue renvoie le préfixe
    interne vers le nginx du front (qui répond 404)."""

    def test_httproute_rule_precedes_api_and_targets_frontend(self):
        src = _src("helm/ragflow/templates/gateway-httproute.yaml")
        internal = src.index("value: /api/v1/internal/")
        api_rule = src.index("value: /api/\n")
        assert internal < api_rule
        after = src[internal:internal + 400]
        assert "-frontend" in after and "port: 80" in after

    def test_sandbox_namespace_enforces_restricted_pod_security(self):
        src = _src("helm/ragflow/templates/sandbox/namespace.yaml")
        assert "pod-security.kubernetes.io/enforce: restricted" in src


# ------------------------------------------------------------ jetons beta
class TestBetaScopeHelpers:
    def test_bound_token_denies_other_resources(self, monkeypatch):
        from api.utils import beta_scope
        tok = SimpleNamespace(dialog_id="dlg1", tenant_id="t")
        assert beta_scope.beta_denies("dlg2", tok) is True
        assert beta_scope.beta_denies("dlg1", tok) is False
        assert beta_scope.beta_denies("x", SimpleNamespace(dialog_id=None)) is False

    def test_allowed_kb_ids_from_dialog_then_search_app(self, monkeypatch):
        from api.utils import beta_scope
        import api.db.services.dialog_service as ds
        import api.db.services.search_service as ss
        dialog = SimpleNamespace(tenant_id="t", kb_ids=["kb1", "kb2"])
        monkeypatch.setattr(ds.DialogService, "get_by_id", staticmethod(lambda i: (True, dialog)))
        assert beta_scope.beta_allowed_kb_ids("t", SimpleNamespace(dialog_id="dlg")) == {"kb1", "kb2"}
        # dialog d'un autre tenant → on retombe sur la search app
        monkeypatch.setattr(ds.DialogService, "get_by_id", staticmethod(lambda i: (True, SimpleNamespace(tenant_id="other", kb_ids=["z"]))))
        monkeypatch.setattr(ss.SearchService, "query", staticmethod(lambda **kw: [SimpleNamespace(search_config={"kb_ids": ["kb9"]})]))
        assert beta_scope.beta_allowed_kb_ids("t", SimpleNamespace(dialog_id="srch")) == {"kb9"}
        # ni dialog ni search app (agent) → pas de restriction supplémentaire
        monkeypatch.setattr(ss.SearchService, "query", staticmethod(lambda **kw: []))
        assert beta_scope.beta_allowed_kb_ids("t", SimpleNamespace(dialog_id="agent")) is None
        assert beta_scope.beta_allowed_kb_ids("t", SimpleNamespace(dialog_id=None)) is None

    def test_kb_ids_owned_by_requires_every_id(self, monkeypatch):
        from api.utils import beta_scope
        import api.db.services.knowledgebase_service as ks
        monkeypatch.setattr(ks.KnowledgebaseService, "query", staticmethod(lambda **kw: [1] if kw.get("id") == "mine" else []))
        assert beta_scope.kb_ids_owned_by("t", ["mine"]) is True
        assert beta_scope.kb_ids_owned_by("t", "mine") is True
        assert beta_scope.kb_ids_owned_by("t", ["mine", "theirs"]) is False
        assert beta_scope.kb_ids_owned_by("t", [""]) is False


class TestBetaRoutesAreScoped:
    BOT = "api/apps/restful_apis/bot_api.py"

    def test_load_user_stashes_the_beta_token(self):
        body = _func("api/apps/__init__.py", "_load_user")
        assert "g.beta_token = objs[0]" in body

    @pytest.mark.parametrize("name,needle", [
        ("chatbot_completions", "beta_denies(dialog_id)"),
        ("agent_bot_completions", "UserCanvasService.accessible, agent_id, tenant_id"),
        ("begin_inputs", "UserCanvasService.accessible, agent_id, tenant_id"),
        ("ask_about_embedded", "kb_ids_owned_by, uid, kb_ids"),
        ("mindmap", "kb_ids_owned_by, tenant_id, kb_ids"),
        ("retrieval_test_embedded", "kb_ids_owned_by, tenant_id, kb_ids"),
        ("retrieval_test_embedded", 'beta_denies(req["search_id"])'),
    ])
    def test_route_checks_scope(self, name, needle):
        assert needle in _func(self.BOT, name), f"{name} doit borner la ressource au tenant/objet du jeton"

    def test_retrieval_caps_page_size_and_top_k(self):
        body = _func(self.BOT, "retrieval_test_embedded")
        assert "size = min(size, 100)" in body and "top = min(top, 1024)" in body

    def test_beta_download_limited_to_bound_datasets(self):
        body = _func("api/apps/sdk/doc.py", "download_doc")
        assert "beta_allowed_kb_ids(tenant_id, objs[0])" in body

    @pytest.mark.parametrize("name", ["ask", "mindmap"])
    def test_chat_api_validates_kb_ids_against_active_tenant(self, name):
        body = _func("api/apps/restful_apis/chat_api.py", name)
        assert "_validate_dataset_ids(" in body
        assert "SearchService.query(id=search_id, tenant_id=" in body


# --------------------------------------------------------------- clés API
class TestApiKeyScopeEnforced:
    def test_token_lists_require_api_key_manage(self):
        for rel in ("api/apps/restful_apis/system_api.py", "api/apps/restful_apis/stats_api.py"):
            src = _src(rel)
            i = src.index("def token_list(")
            decorators = src[max(0, i - 400):i]
            assert "require_permission(Permission.API_KEY_MANAGE)" in decorators, rel

    def test_load_user_refuses_expired_disabled_or_orphan_scoped_keys(self):
        body = _func("api/apps/__init__.py", "_load_user")
        block = body[body.index("if scope is not None:"):body.index("# Fallback: legacy unscoped token")]
        assert "expires_at" in block and "API key expired" in block
        assert "creator invalid" in block
        assert block.count("return None") >= 3, "clé scopée invalide = refus, jamais de repli"
        assert "g.api_key_permissions = list(scope.permissions or [])" in block

    def test_token_required_propagates_permissions_and_caps_jwt_age(self):
        body = _func("api/utils/api_utils.py", "token_required")
        assert "g.api_key_permissions = list(scope.permissions or [])" in body
        assert "max_age=_max_age" in body

    def test_require_permission_intersects_key_permissions(self):
        body = _func("api/apps/extensions/rbac.py", "require_permission")
        assert 'getattr(_g, "api_key_permissions", None)' in body
        assert "permission.value not in scoped" in body
        assert body.index("api_key_permissions") < body.index("has_permission(user_id, tenant_id, permission)")


# ------------------------------------------------------------- IDOR divers
class TestMiscAuthorization:
    def test_webhook_traces_require_login_and_workspace_agent(self):
        legacy = _src("api/apps/restful_apis/agents.py")
        i = legacy.index("def webhook_trace(")
        assert "@login_required" in legacy[i - 120:i]
        assert "UserCanvasService.accessible(agent_id, active_tenant_id())" in _func("api/apps/restful_apis/agents.py", "webhook_trace")
        assert "UserCanvasService.accessible(agent_id, active_tenant_id())" in _func("api/apps/restful_apis/agent_api.py", "webhook_trace")

    def test_set_tenant_info_is_workspace_scoped_and_whitelisted(self):
        body = _func("api/apps/restful_apis/user_api.py", "set_tenant_info")
        assert 'req.pop("tenant_id", None)' in body
        assert "TenantService.update_by_id(active_tenant_id(), update)" in body
        assert '"llm_id", "embd_id"' in body


# ------------------------------------------------------------- lot 2 : IDOR
class TestLot2Idor:
    CONN = "api/apps/restful_apis/connector_api.py"

    @pytest.mark.parametrize("name", ["get_connector", "update_connector", "list_logs", "resume", "test_connector"])
    def test_connector_routes_are_workspace_scoped(self, name):
        body = _func(self.CONN, name)
        assert "_scoped_connector(connector_id)" in body, f"{name} doit charger le connecteur par (id, tenant actif)"
        assert "ConnectorService.get_by_id(" not in body

    def test_no_credentials_print(self):
        assert "print(credentials)" not in _src(self.CONN)

    def test_oauth_popup_escapes_script_context_and_targets_own_origin(self):
        body = _func(self.CONN, "_render_web_oauth_popup")
        assert r'\\u003c' in body and "fullmatch" in body
        assert 'window.location.origin' in _src("common/data_source/google_util/constant.py")
        assert 'postMessage({payload_json}, "*")' not in _src("common/data_source/google_util/constant.py")

    def test_file_commits_bound_to_active_workspace(self):
        src = _src("api/apps/restful_apis/file_commit_api.py")
        assert "kb.tenant_id != _active_tenant()" in _func("api/apps/restful_apis/file_commit_api.py", "_resolve_dataset_folder")
        assert src.count("_folder_in_workspace(") >= 3
        assert "f.tenant_id != _active_tenant()" in _func("api/apps/restful_apis/file_commit_api.py", "get_file_version_history")

    def test_listings_reject_foreign_owner_ids(self):
        assert "_allowed_owner_ids = {active_tenant_id(), current_user.id}" in _func("api/apps/restful_apis/chat_api.py", "list_chats")
        assert 'if t == tenant_id] or [tenant_id]' in _src("api/apps/services/dataset_api_service.py")

    def test_secrets_routes_require_configure_permissions(self):
        mcp = _src("api/apps/restful_apis/mcp_api.py")
        i = mcp.index("def detail(")
        assert "require_permission(Permission.MCP_CONFIGURE)" in mcp[i - 300:i]
        lf = _src("api/apps/restful_apis/langfuse_api.py")
        j = lf.index("def get_api_key(")
        assert "require_permission(Permission.LLM_CONFIGURE)" in lf[j - 300:j]

    def test_document_images_and_thumbnails_scoped_and_safe(self):
        img = _func("api/apps/restful_apis/document_api.py", "get_document_image")
        assert "KnowledgebaseService.query, tenant_id=active_tenant_id(), id=bkt" in img
        assert "apply_safe_file_response_headers(response" in img
        thumbs = _func("api/apps/restful_apis/document_api.py", "list_thumbnails")
        assert 'd.get("kb_id") in _own_kbs' in thumbs

    def test_agent_routes_scoped(self):
        assert "created_by not in {active_tenant_id(), current_user.id}" in _func("api/apps/restful_apis/agent_api.py", "download_agent_file")
        assert "UserCanvasService.accessible, agent_id, active_tenant_id()" in _func("api/apps/restful_apis/agent_api.py", "upload_agent_file")
        assert 'KnowledgebaseService.query, tenant_id=tenant_id, id=doc["kb_id"]' in _func("api/apps/restful_apis/agent_api.py", "rerun_agent")

    def test_system_status_superuser_only(self):
        body = _func("api/apps/restful_apis/system_api.py", "status")
        assert "current_user.is_superuser" in body and body.index("is_superuser") < body.index("res = {}")

    def test_legacy_team_routes_disabled_and_file_rename_rbac(self):
        for name in ("create", "rm"):
            assert "managed by the admin panel" in _func("api/apps/restful_apis/tenant_api.py", name)
        bc = _src("api/apps/backward_compat.py")
        k = bc.index("def deprecated_file_rename(")
        assert "require_permission(Permission.DOCUMENT_CREATE)" in bc[k - 300:k]

    def test_pandoc_disables_raw_tex(self):
        assert 'format="markdown-raw_tex"' in _src("agent/component/docs_generator.py")

    def test_invoke_never_logs_raw_values(self):
        src = _src("agent/component/invoke.py")
        assert "raw=%r" not in src


class TestLot3SsrfAndSessions:
    def test_provider_url_guard_allows_platform_hosts_only(self, monkeypatch):
        from common import provider_url_guard as g
        monkeypatch.setenv("VLLM_CHAT_BASE_URL", "http://vllm-router-service.vllm.svc.cluster.local/v1")
        g.assert_provider_base_url_allowed("http://vllm-router-service.vllm.svc.cluster.local/v1")  # plateforme
        g.assert_provider_base_url_allowed("")  # vide = défaut du fournisseur
        with pytest.raises(ValueError):
            g.assert_provider_base_url_allowed("http://127.0.0.1:9200")
        with pytest.raises(ValueError):
            g.assert_provider_base_url_allowed("http://169.254.169.254/latest/meta-data")

    @pytest.mark.parametrize("rel,needle", [
        ("agent/tools/exesql.py", "assert_host_is_safe(str(self._param.host))"),
        ("agent/tools/email.py", "assert_host_is_safe(str(self._param.smtp_server))"),
        ("agent/component/browser.py", "assert_url_is_safe(url)"),
        ("agent/component/invoke.py", "host, ip = assert_url_is_safe(current)"),
        ("agent/component/invoke.py", '"allow_redirects": False'),
        ("agent/component/invoke.py", "assert_url_is_safe(proxy)"),
        ("agent/component/browser.py", "build_opener(_NoRedirect())"),
        ("api/apps/restful_apis/agent_api.py", 'assert_host_is_safe(str(req["host"]))'),
        ("api/apps/services/provider_api_service.py", "assert_provider_base_url_allowed(base_url)"),
        ("management/server/routers/models.py", "_assert_api_base_allowed(body.api_base, user)"),
        ("common/mcp_tool_call_conn.py", "pin_dns_global(_host, _ip)"),
        ("agent/component/agent_with_tools.py", 'get_or_none(id=mcp["mcp_id"], tenant_id=self._canvas.get_tenant_id())'),
        ("common/data_source/rest_api_connector.py", "raise ConnectorValidationError(msg) from exc"),
    ])
    def test_ssrf_sinks_are_guarded(self, rel, needle):
        assert needle in _src(rel), f"{rel} : garde SSRF absente ({needle})"

    def test_every_sync_connector_validates_settings(self):
        src = _src("rag/svr/sync_data_source.py")
        for ctor in ("ConfluenceConnector(", "WebDAVConnector(", "MoodleConnector(", "ImapConnector("):
            i = src.index("self.connector = " + ctor)
            assert "self.connector.validate_connector_settings()" in src[i:i + 1200], f"{ctor} sans validate_connector_settings()"

    def test_session_cookie_bound_to_access_token(self):
        assert 'session["_access_token"] = str(getattr(user, "access_token", "") or "").strip()' in _func("api/apps/__init__.py", "login_user")
        assert 'session.get("_access_token") != access_token' in _func("api/apps/__init__.py", "_load_user_from_session")
        assert 'session.pop("_access_token", None)' in _func("api/apps/__init__.py", "logout_user")

    def test_password_change_and_reset_rotate_access_token(self):
        assert 'update_dict["access_token"] = get_uuid()' in _func("api/apps/restful_apis/user_api.py", "setting_user")
        assert 'UserService.update_by_id(user.id, {"access_token": get_uuid()})' in _func("api/apps/restful_apis/user_api.py", "forget_reset_password")

    def test_login_does_not_reveal_unknown_emails(self):
        assert "is not registered" not in _func("api/apps/restful_apis/user_api.py", "login")

    def test_deprovision_kills_user_api_keys(self):
        body = _func("management/server/services/provisioning.py", "deprovision_user")
        assert "ApiKeyScope.update(status=\"0\")" in body and "APIToken.delete()" in body


class TestFrontXss:
    def test_preprocess_latex_decodes_entities_only_inside_math(self):
        src = _src("web/src/utils/chat.ts")
        body = src[src.index("export const preprocessLaTeX"):src.index("export function replaceThinkToSection")]
        assert ".replace(/&lt;/g" not in body, "décodage global des entités = contournement de DOMPurify"
        assert "decodeMathEntities(equation)" in body

    def test_every_rehype_raw_renderer_sanitizes_the_tree(self):
        """rehype-raw exécute le HTML brut : tout rendu qui l'utilise doit
        enchaîner rehype-sanitize (arbre assaini APRÈS le parsing, seule
        barrière qui tient face à un bloc HTML enveloppant du contenu décodé)."""
        offenders = []
        for path in (ROOT / "web/src").rglob("*.tsx"):
            src = path.read_text()
            if "rehypeRaw" not in src:
                continue
            if "[rehypeSanitize, markdownSanitizeSchema]" not in src:
                offenders.append(str(path.relative_to(ROOT)))
        assert not offenders, "rehype-raw sans rehype-sanitize : " + ", ".join(offenders)
        schema = _src("web/src/utils/markdown-sanitize.ts")
        assert "defaultSchema" in schema and "'iframe'" not in schema and "'script'" not in schema
        assert '"rehype-sanitize"' in _src("web/package.json")
        widget = _src("web/src/components/floating-chat-widget-markdown.tsx")
        assert "DOMPurify.sanitize(content, {" in widget

    def test_front_nginx_sends_security_headers_and_keeps_share_pages_embeddable(self):
        conf = _src("helm/ragflow/charts/ragflow-frontend/templates/configmap-nginx.yaml")
        assert conf.count('add_header X-Frame-Options "DENY" always;') >= 2
        assert 'add_header Referrer-Policy "no-referrer" always;' in conf
        share = conf.index("location ~ ^/(chats/(share|widget)|agent/share|search/share)")
        block = conf[share:conf.index("location / {")]
        assert "X-Frame-Options" not in block, "les pages de partage doivent rester embarquables"


# ------------------------------------------- Intégrer / Partager (éditeurs)
class TestEmbedBetaForEditors:
    """Le bouton Intégrer / Partager lisait GET /system/tokens (clés API en
    clair), réservé à API_KEY_MANAGE depuis l'audit : les éditeurs ne
    pouvaient plus publier leurs bots. Route dédiée qui ne renvoie que le
    beta, et qui choisit une clé utilisable pour LE bot embarqué."""

    def test_pick_embed_token_prefers_bound_then_unbound_never_other_bot(self):
        from api.utils.beta_scope import pick_embed_token
        other = SimpleNamespace(token="t1", dialog_id="botA", beta="a")
        free = SimpleNamespace(token="t2", dialog_id=None, beta="b")
        mine = SimpleNamespace(token="t3", dialog_id="botB", beta="c")
        assert pick_embed_token([other, free, mine], "botB") is mine
        assert pick_embed_token([other, free], "botB") is free
        assert pick_embed_token([other], "botB") is None, "la clé d'un autre bot serait refusée par beta_denies"
        assert pick_embed_token([other, free], None) is free
        assert pick_embed_token([], "botB") is None
        blank = SimpleNamespace(token="t4", dialog_id="", beta="d")
        assert pick_embed_token([blank], "botB") is blank

    def test_route_is_editor_reachable_and_never_returns_a_key(self):
        rel = "api/apps/restful_apis/system_api.py"
        src = _src(rel)
        route = _func(rel, "embed_beta")
        decos = src[src.index('manager.route("/system/tokens/beta"'):src.index("def embed_beta")]
        assert "login_required" in decos
        assert "require_permission(Permission.CHAT_UPDATE)" in decos
        assert "pick_embed_token(" in route
        assert 'get_json_result(data={"beta": beta})' in route
        assert "data=objs" not in route and '"token"' not in route
        listing = src[src.index('manager.route("/system/tokens", methods=["GET"]'):src.index("def token_list")]
        assert "require_permission(Permission.API_KEY_MANAGE)" in listing, "la liste des clés reste admin"

    def test_front_embed_button_no_longer_lists_api_keys(self):
        for rel in ("web/src/components/embed-dialog/use-show-embed-dialog.ts", "web/src/components/api-service/hooks.ts"):
            src = _src(rel)
            assert "useFetchManualSystemTokenList" not in src and "listToken" not in src, rel
        assert "userService.getEmbedBeta(" in _src("web/src/hooks/use-user-setting-request.tsx")
        assert "/system/tokens/beta" in _src("web/src/utils/api.ts")
        embed = _src("web/src/components/embed-dialog/use-show-embed-dialog.ts")
        assert "useFetchEmbedBeta()" in embed and "fetchEmbedBeta(sharedId)" in embed
        # les appelants passent l'identifiant du bot embarqué
        assert "useShowEmbedModal(id)" in _src("web/src/pages/agent/index.tsx")
        assert "useShowEmbedModal(id)" in _src("web/src/pages/next-chats/chat/sessions.tsx")
        assert re.search(r"useFetchTokenListBeforeOtherStep\(\s*SearchData\?\.id,?\s*\)", _src("web/src/pages/next-search/ragflow-logo.tsx"))
