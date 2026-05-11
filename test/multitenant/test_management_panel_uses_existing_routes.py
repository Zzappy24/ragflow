"""
Contract test: every URL the management panel constructs from
RAGFLOW_API_URL must resolve to a registered RAGFlow backend route.

WHY THIS TEST EXISTS
--------------------
Upstream's RESTful migration renamed many routes from /v1/<page>/<path>
to /api/v1/<path>. The Python code in management/server/ still hard-codes
those URLs as plain strings, so the call only fails at runtime — usually
the first time a user clicks a feature that wasn't part of our pre-merge
smoke flow.

Real regressions caught here:
  /v1/user/internal/bridge/prepare → /api/v1/internal/bridge/prepare
  /v1/user/internal/invite/prepare → /api/v1/internal/invite/prepare
  /v1/health                        → /api/v1/system/healthz
  /v1/datasets                      → /api/v1/datasets

This is the sibling of test_frontend_endpoints_exist.py (which does the
same for web/src/utils/api.ts) — same pattern, different caller.

Run:
    uv run python -m pytest test/multitenant/test_management_panel_uses_existing_routes.py -v

No credentials required — all probes are unauthenticated; we accept any
HTTP code except 404 ("not registered"). 401 / 403 / 405 / 422 all mean
the route exists but rejected us for an orthogonal reason, which is what
we want.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import requests


HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
REPO_ROOT = Path(__file__).resolve().parents[2]
MGMT_ROOT = REPO_ROOT / "management" / "server"

# UUID-shaped placeholder so routes with id-shape validators don't reject early.
PLACEHOLDER = "00000000-0000-0000-0000-000000000000"

# Patterns that build URLs against the RAGFlow backend. We deliberately accept
# multiple shapes (config var, ragflow_client._url helper, raw f-strings).
URL_PATTERNS: list[re.Pattern] = [
    # `f"{settings.RAGFLOW_API_URL}/some/path"`
    re.compile(r'f"\{settings\.RAGFLOW_API_URL\}([^"]+)"'),
    # `self._url("/some/path")` inside ragflow_client.py
    re.compile(r'self\._url\(\s*"([^"]+)"'),
]

# Files explicitly out of scope (no live HTTP probe possible / route is
# inherited from a third-party). Add with a justification when you must.
EXEMPT_PATHS: set[str] = set()


def _extract_urls() -> list[tuple[str, str, int]]:
    """Walk management/server/*.py, return (file, path_template, line_no)."""
    found: list[tuple[str, str, int]] = []
    for py in MGMT_ROOT.rglob("*.py"):
        rel = py.relative_to(REPO_ROOT).as_posix()
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for pat in URL_PATTERNS:
                for m in pat.finditer(line):
                    path = m.group(1)
                    # Skip pure base URLs the client builds *on top of* (no
                    # endpoint to probe): "" or "/" alone.
                    if path.strip("/") == "":
                        continue
                    found.append((rel, path, lineno))
    return found


URL_CASES = _extract_urls()


def _server_up() -> bool:
    try:
        r = requests.get(f"{HOST_ADDRESS}/api/v1/system/version", timeout=2)
        return r.status_code != 0
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_server():
    if not _server_up():
        pytest.skip(f"RAGFlow backend unreachable at {HOST_ADDRESS}")


def _substitute_placeholders(path: str) -> str:
    """Replace `{var}` and `${var}` and `:var` shaped placeholders with a UUID."""
    # f-string substitutions: {user_id}, {ws_id}, etc.
    path = re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", PLACEHOLDER, path)
    return path


@pytest.mark.parametrize(
    "src_file,path,line",
    URL_CASES,
    ids=[f"{f}:{ln} {p}" for (f, p, ln) in URL_CASES],
)
def test_management_url_resolves(src_file: str, path: str, line: int):
    """The URL is registered on the RAGFlow backend (any status != 404)."""
    if f"{src_file}::{path}" in EXEMPT_PATHS:
        pytest.skip("Exempted via EXEMPT_PATHS")

    probe = _substitute_placeholders(path)
    url = f"{HOST_ADDRESS}{probe}"

    # Try GET first; if the route is POST-only, GET returns 405 (route exists).
    try:
        r = requests.get(url, timeout=5)
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Network error probing {url}: {e}")

    if r.status_code == 404:
        # Some POST-only routes return 404 from a Flask catch-all when the
        # method routing fails — confirm by trying POST.
        try:
            r2 = requests.post(url, timeout=5, json={})
        except requests.exceptions.RequestException as e:
            pytest.fail(f"Network error probing POST {url}: {e}")
        if r2.status_code != 404:
            return  # route exists, GET just wasn't supported
        pytest.fail(
            f"Route 404 (gone or renamed): {src_file}:{line}\n"
            f"  template: {path}\n  probed:   {url}\n"
            f"  → fix the URL in {src_file} or add it to EXEMPT_PATHS with a justification."
        )
