"""Per-endpoint MySQL query audit — counts queries, flags N+1 and SELECT *.

Hooks `RetryingPooledMySQLDatabase.execute_sql` to capture every SQL
statement issued during a request, then runs each target endpoint and
reports:

  - Total queries per endpoint
  - Duplicate queries (high count of identical SQL = N+1 smell)
  - Queries against the `user` table that fetch the `avatar` column
    (large base64 TextField that bloats the wire on every auth path)
  - Other potentially wide SELECTs

Network round-trip is bypassed (Quart test_client), so the count is pure
SQL and unaffected by latency.

Usage:
    PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \\
        DOC_ENGINE=infinity \\
        ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026 \\
        RSA_PASSPHRASE=Welcome \\
        PYTHONPATH=$(pwd) \\
        uv run python scripts/audit_queries.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from common import settings  # noqa: E402
settings.init_settings()

from api.apps import app  # noqa: E402
from api.db.db_models import init_database_tables as init_web_db, RetryingPooledMySQLDatabase  # noqa: E402
from api.db.init_data import init_web_data  # noqa: E402

init_web_db()
init_web_data()

from api.apps.extensions.rbac_retriever import install_rbac_proxy  # noqa: E402
install_rbac_proxy()


@app.before_request
async def _rbac_resolve_tenant():
    from quart import g, request
    g._ws_header = request.headers.get("X-Workspace-Id") or None
    g._tenant_resolved = False
    g.active_tenant_id = None
    g.rbac_user_id = None


# --- Auth credentials -------------------------------------------------------

from api.db.db_models import DB  # noqa: E402
from api.db.services.user_service import UserService  # noqa: E402
from api.db.services.workspace_service import (  # noqa: E402
    WorkspaceService, WsMemberService,
)
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer  # noqa: E402
from rag.utils.redis_conn import REDIS_CONN  # noqa: E402

CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")
CI_WORKSPACE_NAME = os.getenv("CI_WORKSPACE_NAME", "Général")


def _credentials() -> tuple[str, str]:
    secret = REDIS_CONN.get("ragflow:system:secret_key")
    if not secret:
        raise SystemExit("Redis secret_key missing — start the server once first.")
    jwt = Serializer(secret_key=secret)
    with DB.connection_context():
        users = list(UserService.query(email=CI_EMAIL))
        if not users:
            raise SystemExit(f"User {CI_EMAIL} not found.")
        u = users[0]
        token = jwt.dumps(u.access_token)
        ms = WsMemberService.list_workspaces_for_user(u.id)
        ws = None
        for m in ms:
            ok, c = WorkspaceService.get_by_id(m.workspace_id)
            if ok and c and c.name == CI_WORKSPACE_NAME:
                ws = c
                break
        if ws is None and ms:
            _, ws = WorkspaceService.get_by_id(ms[0].workspace_id)
        if ws is None:
            raise SystemExit("No workspace membership found.")
        return token, ws.id


TOKEN, WS_ID = _credentials()
HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Workspace-Id": WS_ID}


# --- Hook peewee execute_sql ------------------------------------------------

_orig_execute = RetryingPooledMySQLDatabase.execute_sql
_current_endpoint = ["?"]
_queries: dict[str, list[str]] = defaultdict(list)


def _hooked_execute_sql(self, sql, params=None, commit=True):
    _queries[_current_endpoint[0]].append(sql)
    return _orig_execute(self, sql, params, commit)


RetryingPooledMySQLDatabase.execute_sql = _hooked_execute_sql


# --- Endpoints to audit -----------------------------------------------------

ENDPOINTS = [
    ("list_datasets",   "GET", "/api/v1/datasets",            None),
    ("users_me",        "GET", "/api/v1/users/me",            None),
    ("list_files",      "GET", "/api/v1/files",               None),
    ("list_mcp",        "GET", "/api/v1/mcp/servers",         None),
    ("list_memories",   "GET", "/api/v1/memories",            None),
    ("list_chats",      "GET", "/api/v1/chats",               None),
    ("list_agents",     "GET", "/api/v1/agents",              None),
    ("users_me_models", "GET", "/api/v1/users/me/models",     None),
    ("list_tenants",    "GET", "/api/v1/tenants",             None),
]


async def _hit(method: str, path: str, query: str | None) -> int:
    client = app.test_client()
    full = f"{path}?{query}" if query else path
    resp = await client.get(full, headers=HEADERS)
    return resp.status_code


async def main():
    # Warm-up (avoid one-time imports/caches counting against any endpoint)
    _current_endpoint[0] = "_warmup"
    await _hit("GET", "/api/v1/datasets", None)
    _queries.pop("_warmup", None)

    for name, method, path, query in ENDPOINTS:
        _current_endpoint[0] = name
        # 1 call only — we want absolute counts, not amortised over reps
        code = await _hit(method, path, query)
        n = len(_queries[name])
        print(f"[{code}] {name:<22}  {n:>3} queries")


asyncio.run(main())


# --- Report -----------------------------------------------------------------

print("\n" + "=" * 70)
print("PER-ENDPOINT BREAKDOWN")
print("=" * 70)

for name, *_ in ENDPOINTS:
    sqls = _queries.get(name, [])
    if not sqls:
        continue
    print(f"\n--- {name} ({len(sqls)} queries) ---")
    counter = Counter(sqls)
    duplicates = [(s, c) for s, c in counter.most_common() if c > 1]
    if duplicates:
        print(f"  DUPLICATES (potential N+1):")
        for sql, count in duplicates[:5]:
            short = re.sub(r"\s+", " ", sql).strip()[:120]
            print(f"    {count}x  {short}")
    avatar_hits = [s for s in sqls if "`avatar`" in s or " avatar " in s]
    if avatar_hits:
        print(f"  WIDE SELECT (User.avatar TextField fetched): {len(avatar_hits)}x")
        short = re.sub(r"\s+", " ", avatar_hits[0]).strip()[:200]
        print(f"    e.g.  {short}")
    # Show first 3 unique SQLs as flavor
    unique = []
    seen = set()
    for s in sqls:
        if s not in seen:
            seen.add(s)
            unique.append(s)
        if len(unique) >= 3:
            break
    if not duplicates and not avatar_hits:
        for sql in unique[:3]:
            short = re.sub(r"\s+", " ", sql).strip()[:120]
            print(f"    1x  {short}")


# --- Aggregates -------------------------------------------------------------

print("\n" + "=" * 70)
print("AGGREGATE SUMMARY")
print("=" * 70)

total_queries = sum(len(v) for v in _queries.values())
worst = sorted(_queries.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]
print(f"  Total queries across {len(_queries)} endpoints: {total_queries}")
print(f"  Worst offenders:")
for name, sqls in worst:
    print(f"    {name:<22} {len(sqls)}")
all_sqls = [s for v in _queries.values() for s in v]
avatar_count = sum(1 for s in all_sqls if "`avatar`" in s or " avatar " in s)
print(f"  User.avatar column fetched in: {avatar_count} / {total_queries} queries  "
      f"({avatar_count * 100 // max(total_queries, 1)}%)")
