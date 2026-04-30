"""One-shot profiling script — measures CPU cost of hot endpoints.

Imports the Quart app in-process, generates real auth credentials via the
same path as the bridge conftest (Redis JWT + DB user lookup), then hits
each endpoint via Quart's test_client. pyinstrument profiles everything
that runs in this process: routing, before_request hooks, handler,
peewee queries, JSON serialization.

This is a measurement-only script. It does NOT modify the running server.
Network round-trip is bypassed (test_client speaks ASGI directly), so the
profile reflects pure CPU work — which is what we want to optimize.

Usage:
    PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \\
        DOC_ENGINE=infinity \\
        ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026 \\
        RSA_PASSPHRASE=Welcome \\
        PYTHONPATH=$(pwd) \\
        uv run python scripts/profile_endpoints.py

Outputs:
    /tmp/profile_<endpoint>.html (open in browser for flamegraph)
    Plus per-endpoint timing summary on stdout.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from common import settings
settings.init_settings()

from api.apps import app  # noqa: E402
from api.db.db_models import init_database_tables as init_web_db  # noqa: E402
from api.db.init_data import init_web_data  # noqa: E402

init_web_db()
init_web_data()

# Replicate the RBAC before_request hook from ragflow_server.py — without it,
# active_tenant_id() can't resolve the workspace from X-Workspace-Id.
from api.apps.extensions.rbac_retriever import install_rbac_proxy  # noqa: E402
install_rbac_proxy()


@app.before_request
async def _rbac_resolve_tenant():
    from quart import g, request
    g._ws_header = request.headers.get("X-Workspace-Id") or None
    g._tenant_resolved = False
    g.active_tenant_id = None
    g.rbac_user_id = None


from pyinstrument import Profiler  # noqa: E402

# --- Generate auth credentials (mirrors bridge conftest) -------------------

from api.db.db_models import DB as _DB  # noqa: E402
from api.db.services.user_service import UserService as _UserService  # noqa: E402
from api.db.services.workspace_service import (  # noqa: E402
    WorkspaceService as _WorkspaceService,
    WsMemberService as _WsMemberService,
)
from itsdangerous.url_safe import URLSafeTimedSerializer as _Serializer  # noqa: E402
from rag.utils.redis_conn import REDIS_CONN  # noqa: E402

CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")
CI_WORKSPACE_NAME = os.getenv("CI_WORKSPACE_NAME", "Général")


def _credentials() -> tuple[str, str]:
    secret_key = REDIS_CONN.get("ragflow:system:secret_key")
    if not secret_key:
        raise SystemExit("Redis secret_key missing — start the server once first.")
    jwt = _Serializer(secret_key=secret_key)
    with _DB.connection_context():
        users = list(_UserService.query(email=CI_EMAIL))
        if not users:
            raise SystemExit(f"User {CI_EMAIL} not found.")
        u = users[0]
        token = jwt.dumps(u.access_token)
        memberships = _WsMemberService.list_workspaces_for_user(u.id)
        ws = None
        for m in memberships:
            ok, candidate = _WorkspaceService.get_by_id(m.workspace_id)
            if ok and candidate and candidate.name == CI_WORKSPACE_NAME:
                ws = candidate
                break
        if ws is None and memberships:
            _, ws = _WorkspaceService.get_by_id(memberships[0].workspace_id)
        if ws is None:
            raise SystemExit("No workspace membership found.")
        return token, ws.id


TOKEN, WS_ID = _credentials()
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "X-Workspace-Id": WS_ID,
}
print(f"[creds] user={CI_EMAIL} workspace={CI_WORKSPACE_NAME} ws_id={WS_ID}")
print()


# --- Endpoints to profile (hot CRUD / read paths) ---------------------------

ENDPOINTS = [
    # name, method, path, query — paths must exist in api/apps/restful_apis/
    ("list_datasets",   "GET", "/api/v1/datasets",        None),
    ("users_me",        "GET", "/api/v1/users/me",        None),
    ("list_files",      "GET", "/api/v1/files",           None),
    ("list_mcp",        "GET", "/api/v1/mcp/servers",     None),
    ("list_memories",   "GET", "/api/v1/memories",        None),
]

REPEATS = 5  # runs per endpoint to amortize one-time cost (warmup, JIT, caches)


async def _hit(method: str, path: str, query: str | None):
    client = app.test_client()
    full = f"{path}?{query}" if query else path
    if method == "GET":
        resp = await client.get(full, headers=HEADERS)
    else:
        raise ValueError(f"unhandled method {method}")
    return resp.status_code, await resp.get_data()


def profile_one(name: str, method: str, path: str, query: str | None) -> None:
    print(f"=== {name} ({method} {path}{'?' + query if query else ''}) ===")
    profiler = Profiler(interval=0.001)

    async def runner():
        # warm-up (don't profile)
        await _hit(method, path, query)
        # profile REPEATS calls in a row
        profiler.start()
        codes = []
        for _ in range(REPEATS):
            code, _body = await _hit(method, path, query)
            codes.append(code)
        profiler.stop()
        return codes

    codes = asyncio.run(runner())
    out = REPO / f".profile_{name}.html"
    out.write_text(profiler.output_html())
    print(f"  status codes: {codes}")
    print(f"  → flamegraph: {out}")
    print(profiler.output_text(unicode=False, color=False, show_all=False, timeline=False))
    print()


for ep in ENDPOINTS:
    profile_one(*ep)
