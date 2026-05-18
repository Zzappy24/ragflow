"""
Static analysis: every POST/PUT/PATCH/DELETE route in the Python API must have
@require_permission in its decorator chain.

No server required — pure source analysis.

When a new route is added without @require_permission this test fails, forcing
the author to either add the decorator or explicitly add an entry to EXEMPT with
a justification comment.

Run:
    uv run python -m pytest test/multitenant/test_rbac_coverage.py -v
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Auto-discover all Python files in restful_apis/ — new upstream files are
# automatically included without requiring a manual list update.
ROUTE_FILES = sorted([
    *(ROOT / "api/apps/restful_apis").glob("*.py"),
    *(ROOT / "api/apps").glob("*.py"),
    *(ROOT / "api/apps/sdk").glob("*.py"),
])

# Files scoped for the pyflakes undefined-name check — only files we own and
# actively maintain.  The legacy *_app.py files are upstream code: they have
# pre-existing missing imports in rarely-called paths, and an upstream
# refactor could rename symbols causing spurious test failures.
IMPORT_CHECK_FILES = sorted([
    *(ROOT / "api/apps/restful_apis").glob("*.py"),
    *(ROOT / "api/apps/sdk").glob("*.py"),
])

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# GET routes intentionally exempt from the @login_required / @token_required requirement.
# Key: (path relative to repo root, function_name)
# Each entry MUST include a justification comment.
EXEMPT_GET: set[tuple[str, str]] = {
    # OAuth callbacks — public callback URLs by design (stateful redirect flow).
    ("api/apps/restful_apis/connector_api.py", "google_gmail_web_oauth_callback"),
    ("api/apps/restful_apis/connector_api.py", "google_drive_web_oauth_callback"),
    ("api/apps/restful_apis/connector_api.py", "box_web_oauth_callback"),
    ("api/apps/restful_apis/user_api.py", "oauth_login"),
    ("api/apps/restful_apis/user_api.py", "oauth_callback"),
    # Liveness / config probes — must be reachable without auth (health checks, k8s).
    ("api/apps/restful_apis/system_api.py", "ping"),
    ("api/apps/restful_apis/system_api.py", "healthz"),
    ("api/apps/restful_apis/system_api.py", "get_config"),
    # Version is in the Go server's apiNoAuth group (public by design) — match here.
    ("api/apps/restful_apis/system_api.py", "version"),
    ("api/apps/restful_apis/user_api.py", "get_login_channels"),
    # Webhook endpoints — security via signed payload / DSL token, not session auth.
    ("api/apps/restful_apis/agent_api.py", "webhook"),
    ("api/apps/sdk/agents.py", "webhook"),
    ("api/apps/sdk/agents.py", "webhook_trace"),
    # Embedded chatbot/searchbot widgets — inline API-token validation, intentionally public.
    ("api/apps/sdk/session.py", "chatbots_inputs"),
    ("api/apps/sdk/session.py", "begin_inputs"),
    ("api/apps/sdk/session.py", "detail_share_embedded"),
    # Old-style connector OAuth poll routes — redirect-based auth, no session.
    ("api/apps/connector_app.py", "poll_google_web_result"),
    ("api/apps/connector_app.py", "poll_box_web_result"),
    ("api/apps/restful_apis/connector_api.py", "poll_google_web_result"),
    ("api/apps/restful_apis/connector_api.py", "poll_box_web_result"),
    # SDK download_doc — performs inline APIToken.query(beta=token) check at the
    # top of the handler instead of using @token_required. The auth gate is real
    # (line 437-444 of api/apps/sdk/doc.py), just wired by hand.
    ("api/apps/sdk/doc.py", "download_doc"),
    # Dify-compatible retrieval API — uses @apikey_required (Dify-style Bearer
    # token from their external KB integration); not our @token_required pattern.
    ("api/apps/sdk/dify_retrieval.py", "retrieval"),
    # Dify health check — public probe used by Dify to verify connectivity.
    ("api/apps/sdk/dify_retrieval.py", "retrieval_health_check"),
}

# Routes intentionally exempt from @require_permission.
# Key: (path relative to repo root, function_name)
# Each entry MUST include a justification comment — reviewers should question any new entry.
EXEMPT: set[tuple[str, str]] = {
    # --- restful_apis/ ---
    # Log-level configuration is an infra / superuser concern, not workspace content.
    ("api/apps/restful_apis/system_api.py", "set_logger_level"),

    # --- api/apps/ ---
    # Authentication routes — no workspace context.
    ("api/apps/user_app.py", "login"),
    ("api/apps/user_app.py", "internal_bridge_prepare"),
    ("api/apps/user_app.py", "internal_invite_prepare"),
    ("api/apps/user_app.py", "bridge_login"),
    ("api/apps/user_app.py", "set_initial_password"),
    ("api/apps/user_app.py", "setting_user"),
    ("api/apps/user_app.py", "user_add"),
    ("api/apps/user_app.py", "forget_send_otp"),
    ("api/apps/user_app.py", "forget_verify_otp"),
    ("api/apps/user_app.py", "forget_reset_password"),
    # Same auth routes migrated to restful_apis/user_api.py — inline workspace/tenant checks.
    ("api/apps/restful_apis/user_api.py", "login"),
    ("api/apps/restful_apis/user_api.py", "log_out"),
    ("api/apps/restful_apis/user_api.py", "internal_bridge_prepare"),
    ("api/apps/restful_apis/user_api.py", "internal_invite_prepare"),
    ("api/apps/restful_apis/user_api.py", "bridge_login"),
    ("api/apps/restful_apis/user_api.py", "set_initial_password"),
    ("api/apps/restful_apis/user_api.py", "setting_user"),
    ("api/apps/restful_apis/user_api.py", "user_add"),
    ("api/apps/restful_apis/user_api.py", "forget_get_captcha"),
    ("api/apps/restful_apis/user_api.py", "forget_send_otp"),
    ("api/apps/restful_apis/user_api.py", "forget_verify_otp"),
    ("api/apps/restful_apis/user_api.py", "forget_reset_password"),
    # api_app new_token: already restricted to superusers only (inline check).
    ("api/apps/api_app.py", "new_token"),
    # Same route migrated to restful_apis/stats_api.py — same inline superuser check.
    ("api/apps/restful_apis/stats_api.py", "new_token"),
    # restful_apis system_api tokens: handled by require_permission(API_KEY_MANAGE).
    # Team/tenant management — old-style invite flow, superseded by workspace system.
    ("api/apps/tenant_app.py", "create"),
    ("api/apps/tenant_app.py", "rm"),
    ("api/apps/tenant_app.py", "agree"),
    # Same routes migrated to restful_apis/ — inline auth check (current_user.id != tenant_id).
    ("api/apps/restful_apis/tenant_api.py", "create"),
    ("api/apps/restful_apis/tenant_api.py", "rm"),
    ("api/apps/restful_apis/tenant_api.py", "agree"),
    # OAuth callbacks — stateful redirect flow, no session workspace context.
    ("api/apps/connector_app.py", "poll_google_web_result"),
    ("api/apps/connector_app.py", "poll_box_web_result"),
    # Same OAuth callbacks migrated to restful_apis/connector_api.py.
    ("api/apps/restful_apis/connector_api.py", "poll_google_web_result"),
    ("api/apps/restful_apis/connector_api.py", "poll_box_web_result"),

    # Per-user API key management — intentionally accessible to ANY logged-in
    # user (each user manages their own keys). The route clamps the requested
    # permissions to the caller's effective permissions in
    # `_user_effective_permissions`, so a viewer cannot self-elevate by
    # minting a write-scoped key. Workspace-wide / service-key management
    # lives in the management panel and is admin-only.
    ("api/apps/restful_apis/api_key_api.py", "list_my_api_keys"),
    ("api/apps/restful_apis/api_key_api.py", "create_my_api_key"),
    ("api/apps/restful_apis/api_key_api.py", "revoke_my_api_key"),

    # --- api/apps/sdk/ ---
    # Embedded chatbot/searchbot widgets — use inline API-token validation, intentionally public.
    ("api/apps/sdk/session.py", "chatbot_completions"),
    ("api/apps/sdk/session.py", "agent_bot_completions"),
    ("api/apps/sdk/session.py", "ask_about_embedded"),
    ("api/apps/sdk/session.py", "mindmap"),
    ("api/apps/sdk/session.py", "retrieval_test_embedded"),
    ("api/apps/sdk/session.py", "related_questions_embedded"),
    # Dify integration — uses Dify's own auth mechanism.
    ("api/apps/sdk/dify_retrieval.py", "retrieval"),
    # Webhook endpoint — public by design, security handled in DSL (IP whitelist, token, JWT).
    ("api/apps/sdk/agents.py", "webhook"),
    # Same webhook migrated to restful_apis/agent_api.py — same public-by-design semantics.
    ("api/apps/restful_apis/agent_api.py", "webhook"),

    # --- api/apps/backward_compat.py ---
    # backward_compat.py routes are deprecated forwarders that call the new RESTful
    # routes — permission enforcement happens in the underlying handler being called.
    ("api/apps/backward_compat.py", "deprecated_chat_completions"),
    ("api/apps/backward_compat.py", "deprecated_openai_chat_completions"),
    ("api/apps/backward_compat.py", "deprecated_update_session"),
    ("api/apps/backward_compat.py", "deprecated_file_create"),
    ("api/apps/backward_compat.py", "deprecated_file_upload"),
    ("api/apps/backward_compat.py", "deprecated_file_mv"),
    ("api/apps/backward_compat.py", "deprecated_file_rename"),
    ("api/apps/backward_compat.py", "deprecated_file_rm"),
    ("api/apps/backward_compat.py", "deprecated_file_upload_info"),
    ("api/apps/backward_compat.py", "deprecated_related_questions"),
    ("api/apps/backward_compat.py", "deprecated_update_chunk"),
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _route_write_methods(decorator: ast.expr) -> set[str]:
    """
    If decorator is @X.route(..., methods=[...]), return the subset of write
    methods declared. Returns empty set if the decorator is not a route or
    declares no write methods.
    """
    if not isinstance(decorator, ast.Call):
        return set()
    func = decorator.func
    if not (isinstance(func, ast.Attribute) and func.attr == "route"):
        return set()
    for kw in decorator.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List):
            declared = {
                elt.value
                for elt in kw.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
            return declared & WRITE_METHODS
    return set()


def _has_require_permission(decorators: list[ast.expr]) -> bool:
    """Return True if any decorator in the list is @require_permission(...)."""
    for dec in decorators:
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name) and func.id == "require_permission":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "require_permission":
                return True
    return False


def _collect_unprotected(path: Path) -> list[tuple[str, str, set[str]]]:
    """
    Parse *path* and return a list of (rel_path, func_name, write_methods)
    for every write route that lacks @require_permission and is NOT in EXEMPT.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    rel = str(path.relative_to(ROOT))
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        write_methods: set[str] = set()
        for dec in node.decorator_list:
            write_methods |= _route_write_methods(dec)
        if not write_methods:
            continue
        if _has_require_permission(node.decorator_list):
            continue
        if (rel, node.name) in EXEMPT:
            continue
        violations.append((rel, node.name, write_methods))

    return violations


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_all_write_routes_have_require_permission():
    """
    Every POST/PUT/PATCH/DELETE route in the scanned files must carry
    @require_permission.  Add an EXEMPT entry (with justification) for routes
    that legitimately bypass workspace RBAC.
    """
    all_violations: list[tuple[str, str, set[str]]] = []
    for path in ROUTE_FILES:
        assert path.exists(), f"Route file not found (update ROUTE_FILES): {path}"
        all_violations.extend(_collect_unprotected(path))

    if all_violations:
        lines = [
            "\nWrite routes missing @require_permission:\n",
            "(add @require_permission(...) OR add to EXEMPT with a justification)\n",
        ]
        for rel, func, methods in sorted(all_violations):
            lines.append(f"  {rel}::{func}  methods={sorted(methods)}")
        pytest.fail("\n".join(lines))


@pytest.mark.parametrize("path", ROUTE_FILES, ids=lambda p: p.name)
def test_route_file_is_parseable(path: Path):
    """Sanity: each file must parse without syntax errors."""
    src = path.read_text(encoding="utf-8")
    ast.parse(src, filename=str(path))


def _pyflakes_undefined_names(path: Path) -> list[tuple[str, str]]:
    """
    Use pyflakes to find undefined names (UndefinedName / F821).
    Returns list of (message_text, col_offset_str) pairs — one per violation.
    """
    from pyflakes import api as pf_api, checker as pf_checker
    import pyflakes.messages as pf_messages

    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    w = pf_checker.Checker(tree, filename=str(path))
    return [
        (str(path.relative_to(ROOT)), msg.message % msg.message_args)
        for msg in w.messages
        if isinstance(msg, pf_messages.UndefinedName)
    ]


# Undefined names legitimately injected at runtime by register_page().
# Key: exact pyflakes message string that should be suppressed.
# Add with justification — reviewers should question any new entry.
PYFLAKES_EXEMPT: set[str] = {
    # register_page() injects `app` and `manager` into every route module namespace.
    "undefined name 'app'",
    "undefined name 'manager'",
}


def test_no_undefined_names_in_route_files():
    """
    Catch missing imports in route files before they silently break at runtime.

    Example: FileService was dropped from sdk/doc.py during an upstream merge.
    The NameError was swallowed by token_required's broad except-clause and
    surfaced as a spurious 401 "API key is invalid!" — extremely hard to diagnose.
    This pyflakes check (F821 / UndefinedName) catches it in <1s, no server needed.

    If pyflakes flags a name that is legitimately injected at runtime (e.g. by
    register_page()), add the exact message string to PYFLAKES_EXEMPT with a comment.
    """
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        pytest.skip("pyflakes not installed (run: uv pip install pyflakes)")

    all_violations: list[tuple[str, str]] = []
    for path in IMPORT_CHECK_FILES:
        for rel, msg in _pyflakes_undefined_names(path):
            if msg not in PYFLAKES_EXEMPT:
                all_violations.append((rel, msg))

    if all_violations:
        lines = ["\nUndefined names in route files (missing import?):\n"]
        for rel, msg in sorted(set(all_violations)):
            lines.append(f"  {rel}: {msg}")
        lines.append(
            "\n(Add to PYFLAKES_EXEMPT with justification if the name is injected"
            " at runtime, e.g. by register_page().)"
        )
        pytest.fail("\n".join(lines))


# ---------------------------------------------------------------------------
# GET-route auth check helpers
# ---------------------------------------------------------------------------

def _is_get_route(decorator: ast.expr) -> bool:
    """
    Return True if decorator is @X.route(...) with no methods kwarg (default GET)
    or with methods that include "GET".
    """
    if not isinstance(decorator, ast.Call):
        return False
    func = decorator.func
    if not (isinstance(func, ast.Attribute) and func.attr == "route"):
        return False
    for kw in decorator.keywords:
        if kw.arg == "methods" and isinstance(kw.value, ast.List):
            declared = {
                elt.value
                for elt in kw.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            }
            return "GET" in declared
    # No methods kwarg → Flask/Quart default is GET
    return True


def _has_auth_decorator(decorators: list[ast.expr]) -> bool:
    """
    Return True if any decorator in the list is @login_required or @token_required
    (by bare name or attribute access, e.g. app.login_required).
    """
    AUTH_NAMES = {"login_required", "token_required"}
    for dec in decorators:
        # @login_required  or  @token_required  (bare Name, not called)
        if isinstance(dec, ast.Name) and dec.id in AUTH_NAMES:
            return True
        # @app.login_required  etc.
        if isinstance(dec, ast.Attribute) and dec.attr in AUTH_NAMES:
            return True
        # @login_required()  — called with no args (uncommon but possible)
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name) and func.id in AUTH_NAMES:
                return True
            if isinstance(func, ast.Attribute) and func.attr in AUTH_NAMES:
                return True
    return False


def _collect_unauthed_get_routes(path: Path) -> list[tuple[str, str]]:
    """
    Parse *path* and return (rel_path, func_name) for every GET route that
    lacks @login_required / @token_required and is NOT in EXEMPT_GET.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    rel = str(path.relative_to(ROOT))
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_get = any(_is_get_route(dec) for dec in node.decorator_list)
        if not is_get:
            continue
        if _has_auth_decorator(node.decorator_list):
            continue
        if (rel, node.name) in EXEMPT_GET:
            continue
        violations.append((rel, node.name))

    return violations


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# GET-route RBAC: read endpoints must enforce permission, not just auth.
# ---------------------------------------------------------------------------
#
# An authenticated user is NOT automatically authorized — a viewer in
# workspace A still must be denied when reading a dataset from workspace B,
# and a non-member of any workspace must not be able to list other people's
# data just because they have a valid JWT.  GET routes that surface
# workspace-scoped data therefore need `@require_permission(…READ)`.
#
# Each entry in EXEMPT_GET_RBAC must come with a one-line reason — reviewers
# should question any new addition.

EXEMPT_GET_RBAC: set[tuple[str, str]] = {
    # Already exempt from auth altogether — re-listed here so this test
    # doesn't double-flag them.  Keep in sync with EXEMPT_GET above.
    *EXEMPT_GET,

    # `current_user.id`-scoped endpoints — they intentionally only return the
    # caller's own data; there is no permission gate to apply because there
    # is no other user's data on the table.
    # Per-user API key listing — filters by `created_by = current_user.id`,
    # so the caller can only ever see their own keys (cf. justification in
    # EXEMPT for the matching write routes).
    ("api/apps/restful_apis/api_key_api.py", "list_my_api_keys"),
    ("api/apps/restful_apis/user_api.py", "user_info"),
    ("api/apps/restful_apis/user_api.py", "user_setting"),
    ("api/apps/restful_apis/user_api.py", "list_tenants"),
    ("api/apps/restful_apis/user_api.py", "list_tenant_models"),
    ("api/apps/restful_apis/user_api.py", "list_user_workspaces"),
    ("api/apps/restful_apis/user_api.py", "list_user_organizations"),
    ("api/apps/user_app.py", "user_info"),
    ("api/apps/user_app.py", "list_tenants"),
    ("api/apps/user_app.py", "tenant_info"),
    ("api/apps/restful_apis/system_api.py", "list_tokens"),
    ("api/apps/api_app.py", "token_list"),
    ("api/apps/restful_apis/stats_api.py", "token_list"),

    # Tenant/workspace listing — caller can only see their own memberships.
    ("api/apps/tenant_app.py", "tenant_list"),

    # llm_app.py — these inspect the user's PERSONAL tenant model defaults,
    # not workspace-scoped business data.  Permission framework doesn't apply.
    ("api/apps/llm_app.py", "factories"),
    ("api/apps/llm_app.py", "list_app"),
    ("api/apps/llm_app.py", "my_llms"),
    ("api/apps/llm_app.py", "list_models"),

    # Stats/observability — already gated to superusers via inline checks.
    ("api/apps/api_app.py", "stats"),
    ("api/apps/restful_apis/stats_api.py", "stats"),
    ("api/apps/api_app.py", "list_apps"),
    ("api/apps/restful_apis/stats_api.py", "list_apps"),

    # --- restful_apis/system_api.py ---
    # System-wide health / metadata — no workspace-scoped data on the wire.
    ("api/apps/restful_apis/system_api.py", "version"),
    ("api/apps/restful_apis/system_api.py", "status"),
    ("api/apps/restful_apis/system_api.py", "oceanbase_status"),
    ("api/apps/restful_apis/system_api.py", "get_logger_levels"),
    # Current-user-scoped API tokens (scoped to active_tenant_id(), not other workspace data).
    ("api/apps/restful_apis/system_api.py", "token_list"),

    # --- restful_apis/tenant_api.py ---
    # Caller's own tenant memberships only — no other user's data.
    ("api/apps/restful_apis/tenant_api.py", "tenant_list"),
    # Inline `current_user.id != tenant_id` gate — self-or-owner check, no workspace content.
    ("api/apps/restful_apis/tenant_api.py", "user_list"),

    # --- restful_apis/user_api.py ---
    # Current-user-scoped self-info enriched with RBAC context (no other user's data).
    ("api/apps/restful_apis/user_api.py", "user_profile"),
    # Current-user-scoped default model config (calls TenantService.get_info_by(current_user.id)).
    ("api/apps/restful_apis/user_api.py", "tenant_info"),

    # --- restful_apis/langfuse_api.py ---
    # Current-tenant-scoped LLM observability keys — no workspace business data.
    ("api/apps/restful_apis/langfuse_api.py", "get_api_key"),

    # --- restful_apis/mcp_api.py ---
    # Inline `mcp_server.tenant_id == active_tenant_id()` ownership check; no wider scope.
    ("api/apps/restful_apis/mcp_api.py", "detail"),

    # --- restful_apis/plugin_api.py ---
    # Global plugin metadata catalogue — workspace-agnostic, no user data on the wire.
    ("api/apps/restful_apis/plugin_api.py", "llm_tools"),

    # --- restful_apis/agent_api.py ---
    # Global agent template catalogue — workspace-agnostic public read-only list.
    ("api/apps/restful_apis/agent_api.py", "list_agent_template"),
    # Static system-level prompt text — no workspace data, no per-user scope.
    ("api/apps/restful_apis/agent_api.py", "prompts"),
    # Webhook trace log — inline `cvs.user_id == current_user.id` owner gate; current-user-scoped only.
    ("api/apps/restful_apis/agent_api.py", "webhook_trace"),

    # --- api/apps/backward_compat.py GET forwarders ---
    # Deprecated forwarders that delegate to the file_api.py target which enforces permission.
    ("api/apps/backward_compat.py", "deprecated_file_get"),
    ("api/apps/backward_compat.py", "deprecated_file_list"),
    ("api/apps/backward_compat.py", "deprecated_file_all_parent_folder"),
    ("api/apps/backward_compat.py", "deprecated_file_parent_folder"),
    ("api/apps/backward_compat.py", "deprecated_file_root_folder"),
}


def _has_require_permission(decorators: list[ast.expr]) -> bool:
    """Return True if any decorator is @require_permission(...)."""
    for dec in decorators:
        if isinstance(dec, ast.Call):
            func = dec.func
            if isinstance(func, ast.Name) and func.id == "require_permission":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "require_permission":
                return True
    return False


def _collect_unauthorized_get_routes(path: Path) -> list[tuple[str, str]]:
    """
    Return (rel_path, func_name) for every GET route that has auth (login_required
    or token_required) but lacks @require_permission and is NOT in EXEMPT_GET_RBAC.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    rel = str(path.relative_to(ROOT))
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_get = any(_is_get_route(dec) for dec in node.decorator_list)
        if not is_get:
            continue
        if not _has_auth_decorator(node.decorator_list):
            # Auth-less GET routes are handled by test_get_routes_require_auth.
            continue
        if _has_require_permission(node.decorator_list):
            continue
        if (rel, node.name) in EXEMPT_GET_RBAC:
            continue
        violations.append((rel, node.name))

    return violations


def test_get_routes_have_rbac():
    """
    Every authenticated GET route returning workspace-scoped data must carry
    @require_permission(...) — auth alone (login_required / token_required)
    only proves who the caller is, not that they can read this resource.

    WHY THIS TEST EXISTS
    --------------------
    Discovered 2026-04-30: GET /api/v1/agents/<id>/sessions in restful_apis/
    agent_api.py shipped with @login_required but no @require_permission, so
    any authenticated user could list any agent's sessions. The shadow SDK
    route had been masking it.  After we deleted the shadow this test would
    have caught it on its own.

    Adding a new GET route?  Either decorate it with the appropriate
    @require_permission(...READ) OR add to EXEMPT_GET_RBAC with a one-line
    reason explaining why permission gating doesn't apply (current-user-only
    endpoint, public health probe, etc.).
    """
    all_violations: list[tuple[str, str]] = []
    for path in ROUTE_FILES:
        all_violations.extend(_collect_unauthorized_get_routes(path))

    if all_violations:
        lines = [
            "\nAuthenticated GET routes missing @require_permission:\n",
            "(add @require_permission(Permission.X_READ) OR add to "
            "EXEMPT_GET_RBAC with a justification comment)\n",
        ]
        for rel, func in sorted(all_violations):
            lines.append(f"  {rel}::{func}")
        pytest.fail("\n".join(lines))


def test_get_routes_require_auth():
    """
    Every GET route in the scanned files must have @login_required or
    @token_required in its decorator chain.

    WHY THIS TEST EXISTS
    --------------------
    Upstream's RESTful migration introduced GET /api/v1/documents/images/<image_id>
    WITHOUT any auth decorator — making chunk images publicly readable to anyone
    who knows or guesses an image_id (security regression).  This test would have
    caught it immediately.

    Routes that legitimately skip auth (OAuth callbacks, health checks, webhooks,
    public widgets) must be explicitly listed in EXEMPT_GET with a justification
    comment.  Reviewers should question any new entry.

    @token_required counts as auth — SDK routes use this instead of login_required.
    """
    all_violations: list[tuple[str, str]] = []
    for path in ROUTE_FILES:
        assert path.exists(), f"Route file not found (update ROUTE_FILES): {path}"
        all_violations.extend(_collect_unauthed_get_routes(path))

    if all_violations:
        lines = [
            "\nGET routes missing @login_required or @token_required:\n",
            "(add the decorator OR add to EXEMPT_GET with a justification comment)\n",
        ]
        for rel, func in sorted(all_violations):
            lines.append(f"  {rel}::{func}")
        pytest.fail("\n".join(lines))
