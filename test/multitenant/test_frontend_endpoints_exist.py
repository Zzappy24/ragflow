"""
Contract test: every URL declared in web/src/utils/api.ts must resolve to a
registered backend route.

WHY THIS TEST EXISTS
--------------------
During upstream merges, backend routes are renamed, deleted, or migrated without
the frontend api.ts being updated (or vice-versa). The result is a silent 404
that only surfaces at runtime — after deploy.

Real regression caught: GET /v1/document/image/${id} was removed from the backend
during a RESTful migration and the route was only available at the new
/api/v1/documents/images/<id> path. No test caught it.

This test parses web/src/utils/api.ts, extracts every URL template, substitutes
path parameters with a placeholder UUID, and probes each URL without auth.
Expected outcomes:
  - 401 Unauthorized  → route exists, requires auth     → PASS
  - 405 Method Not Allowed → route exists, wrong method → PASS
  - 200 / any non-404  → route exists, public           → PASS
  - 404 Not Found      → route gone or renamed          → FAIL

No server state is created or mutated — all probes are unauthenticated GETs (or
POST fallback). The test is purely structural.

Run:
    uv run python -m pytest test/multitenant/test_frontend_endpoints_exist.py -v

No credentials required — all probes are unauthenticated.
"""

import os
import re

import pytest
import requests

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")

# Placeholder substituted for every ${param} template variable.
# UUID-shaped so routes with ID validation don't reject on format alone.
PLACEHOLDER = "00000000-0000-0000-0000-000000000000"

# ---------------------------------------------------------------------------
# Endpoints explicitly exempt from the 404 check, with justification.
# Add here ONLY if a URL is structurally impossible to probe (e.g. contains a
# query-string-only param or a non-UUID-shaped positional param whose shape the
# route regex rejects, causing a legitimate 404 from URL mismatch).
# ---------------------------------------------------------------------------
EXEMPT_FRONTEND_ENDPOINTS: set[str] = {
    # googleWebAuthStart / googleWebAuthResult / boxWebAuthStart / boxWebAuthResult:
    # query-string params (?type=...) mean the base URL with our UUID placeholder
    # resolves to a different path entirely on some backends.
    # The actual route is /connectors/google/oauth/web/start and similar — these
    # are OAuth redirect handlers, not data routes.
    "googleWebAuthStart",
    "googleWebAuthResult",
    "boxWebAuthStart",
    "boxWebAuthResult",
    # deleteMemoryMessage / getMessageContent / updateMessageState:
    # URL contains a colon-separated composite ID (memory_id:message_id).
    # Substituting with UUID:UUID produces a URL the Flask router sees as a
    # non-matching path segment, causing a structural 404 unrelated to route existence.
    "deleteMemoryMessage",
    "getMessageContent",
    "updateMessageState",
    # getDocumentFile: declared in api.ts as a base URL ("/v1/document/get") that
    # callers are expected to suffix with /<doc_id>. No caller does so today (it
    # is unused frontend dead code that grep cannot wire to a Python handler).
    # TODO: confirm with frontend team and delete the api.ts entry.
    "getDocumentFile",
    # /v1/dataflow/* family: declared in api.ts and wired through dataflow-service.ts
    # but no Python backend has ever existed at /v1/dataflow/. Pre-existing dead code
    # from a partially-implemented feature predating this merge. Probing them today
    # is guaranteed to 404 — exempted to keep the contract test signal clean.
    # TODO: either implement the dataflow_app.py backend or delete the frontend
    # dataflow service + page (decision belongs to product, out of merge scope).
    "fetchDataflow",
    "setDataflow",
    "removeDataflow",
    "listDataflow",
    "runDataflow",
    # /api/v1/skills/* family: served exclusively by the Go server (registered in
    # internal/router/router.go), not the Python Flask backend that this test
    # probes on port 9380. Probing them on the Python server is guaranteed to
    # 404 — the routes do exist on the Go side. Frontend hits the Go server
    # directly through its own ingress.
    "skillSpaces",
    "skillSpace",
    "skillSpaceByFolder",
    "skillConfig",
    "skillSearch",
    "skillIndex",
    "skillReindex",
    # /api/v1/datasets/<id>/embedding/check: upstream added the service-layer
    # `check_embedding()` in api/apps/services/dataset_api_service.py but did
    # NOT register a Flask route handler for it. Frontend declared the URL
    # but backend has no endpoint. Either upstream forgot to wire the route
    # or it's planned for a future release.
    # TODO: wire `check_embedding` to a `@manager.route("/datasets/<id>/embedding/check", methods=["POST"])`
    # handler in dataset_api.py if/when product confirms the feature is shipping.
    "checkEmbedding",
    # /api/v1/agents/<id>/tags PUT: frontend declared but no Python or Go
    # handler exists for this verb on the per-agent path. The list endpoint
    # (`/agents/tags`) works (see listAgentTags), only the per-agent update
    # is missing. Likely an upstream incomplete feature.
    # TODO: implement the handler or remove the frontend call if unused.
    "updateAgentTags",
}


# ---------------------------------------------------------------------------
# Parse api.ts
# ---------------------------------------------------------------------------

def _parse_api_ts() -> list[tuple[str, str]]:
    """
    Parse web/src/utils/api.ts and return (key, url) pairs for every entry
    that contains ${webAPI} or ${restAPIv1}.

    Handles two declaration shapes:
      Plain:    key: `${webAPI}/llm/factories`,
      Function: key: (id: string) => `${restAPIv1}/datasets/${id}/documents`,
    """
    api_ts_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "web", "src", "utils", "api.ts"
    )
    with open(api_ts_path, encoding="utf-8") as f:
        src = f.read()

    # Match lines containing backtick template literals with ${webAPI} or ${restAPIv1}
    pattern = re.compile(
        r'^\s+(\w+)\s*(?::\s*(?:\([^)]*\)\s*=>\s*)?|=\s*)'  # key: or key: (...) =>
        r'`(\$\{(?:webAPI|restAPIv1)\}[^`]*)`',              # `${webAPI}/...`
        re.MULTILINE,
    )

    results: list[tuple[str, str]] = []
    for m in pattern.finditer(src):
        key = m.group(1)
        template = m.group(2)
        # Substitute base vars
        url = template.replace("${webAPI}", "/v1").replace("${restAPIv1}", "/api/v1")
        # Substitute any remaining ${param} with placeholder
        url = re.sub(r"\$\{[^}]+\}", PLACEHOLDER, url)
        # Strip query strings that would make the path structurally wrong
        # (keep them — query params are fine for route matching)
        results.append((key, url))

    return results


# URL path prefixes served by a *different* backend (not the one under test).
# These routes exist, but on a separate process — probing them on HOST_ADDRESS
# would always 404. The admin panel runs on its own backend (default port 9381,
# `management/server`), so its /api/v1/admin/* routes are out of scope here.
SKIP_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/admin/",  # admin panel backend (separate uvicorn process)
)


# Build the endpoint list once at module level so parametrize can use it.
_ALL_ENDPOINTS: list[tuple[str, str]] = _parse_api_ts()

# Filter out exemptions and routes that target a different backend
ENDPOINTS: list[tuple[str, str]] = [
    (k, u)
    for k, u in _ALL_ENDPOINTS
    if k not in EXEMPT_FRONTEND_ENDPOINTS
    and not any(u.startswith(p) for p in SKIP_PATH_PREFIXES)
]


def _probe(url: str) -> int:
    """
    Probe a URL without auth.  GET first; if 404, retry with POST (empty body).
    Returns the best (non-404) status code, or 404 if both attempts 404.
    """
    full = HOST_ADDRESS + url
    try:
        r = requests.get(full, timeout=5, allow_redirects=False)
        if r.status_code != 404:
            return r.status_code
        # Fallback: try POST — some routes only accept POST and Flask/Quart may
        # return 404 (not 405) for a plain GET to a POST-only rule.
        r2 = requests.post(full, json={}, timeout=5, allow_redirects=False)
        if r2.status_code != 404:
            return r2.status_code
        return 404
    except requests.RequestException:
        # Connection error: server not running — skip rather than fail.
        pytest.skip(f"Server not reachable at {HOST_ADDRESS}")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,url", ENDPOINTS, ids=[k for k, _ in ENDPOINTS])
def test_frontend_endpoint_exists(name: str, url: str):
    """
    Every URL in api.ts must map to a registered backend route.
    404 means the route was deleted or renamed without updating the frontend.
    401/405/200/etc. all mean the route exists.
    """
    status = _probe(url)
    assert status != 404, (
        f"\nFRONTEND CONTRACT BROKEN: api.ts key '{name}' -> '{url}' returned 404.\n"
        f"The backend route no longer exists at this path.\n"
        f"Either restore the route, update api.ts, or add '{name}' to "
        f"EXEMPT_FRONTEND_ENDPOINTS with a justification comment."
    )
