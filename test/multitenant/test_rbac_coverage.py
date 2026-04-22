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
    # api_app new_token: already restricted to superusers only (inline check).
    ("api/apps/api_app.py", "new_token"),
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
