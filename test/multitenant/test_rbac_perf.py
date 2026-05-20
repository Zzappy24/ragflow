"""
RBAC perf regression tests — JOIN correctness + per-request caching.

WHY THIS FILE EXISTS
--------------------
Profiling on 2026-04-30 (see scripts/profile_endpoints.py) showed that on
every authenticated request:

  - `_load_user()` was invoked 3x (LocalProxy doesn't cache by default) →
    3 user-table queries instead of 1.
  - `has_permission()` ran 4 sequential queries (UserService.get_by_id +
    WorkspaceService.get_by_tenant_id + OrgMember.get_membership +
    WsMember.get_membership). Multiple `@require_permission` decorators in
    a request chain compounded that further.

Together those represented ~60% of the wall time of typical CRUD endpoints.
The fixes:

  - `_load_user`: caches its own result on `g._user_resolved` (api/apps/__init__.py).
  - `has_permission`: backed by `_resolve_rbac_context` (single LEFT JOIN over
    User + Workspace + OrgMember + WsMember) and per-request memoised on
    `g._rbac_ctx` (api/apps/extensions/rbac.py).

These tests pin the invariants:
  1. The JOIN returns the SAME RBAC verdict as the legacy 4-query code path
     (functional equivalence — no security regression).
  2. The query count drops within a single request: `_resolve_rbac_context`
     runs once per (user, tenant), not once per `has_permission` call.

If a future upstream merge or refactor reverts either cache, these tests
fail loudly.

Heavy imports (peewee, RAGFlow models) are deferred into fixtures so that
test collection is not blocked by RAGFlow's xgboost/tensorflow/UMAP import-
time UserWarnings under pytest's filterwarnings="error".

Run:
    PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \\
        DOC_ENGINE=infinity ADMIN_JWT_SECRET=... RSA_PASSPHRASE=Welcome \\
        PYTHONPATH=$(pwd) \\
        uv run python -m pytest test/multitenant/test_rbac_perf.py -v
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# Silence RAGFlow's startup UserWarnings (xgboost pkg_resources, tensorflow/UMAP)
# at runtime. pyproject.toml has filterwarnings="error" which would otherwise
# turn them into ImportError during fixture/test execution.
warnings.filterwarnings("ignore", category=UserWarning)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")


# ---------------------------------------------------------------------------
# Fixtures — all heavy imports happen here, not at module load time.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rbac():
    """The rbac module + DB handle, imported lazily so collection is fast.

    Heavy RAGFlow imports trigger several stdout/UserWarnings (xgboost
    pkg_resources, tensorflow/UMAP, etc.). pyproject.toml sets
    filterwarnings="error" which would convert those to ImportError and skip
    every test. catch_warnings + simplefilter("ignore") locally swallow them
    only for the duration of the fixture import.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from common import settings
            settings.init_settings()
            from api.db.db_models import DB
            from api.apps.extensions import rbac as _rbac
    except Exception as e:
        pytest.skip(f"DB stack not importable: {e}")
    return _rbac, DB


@pytest.fixture(scope="module")
def ci_user_and_tenant(rbac):
    """Resolve (user_id, tenant_id) for the CI workspace user — same as the
    bridge conftest does. Skip cleanly if not provisioned."""
    _rbac, DB = rbac
    from api.db.services.user_service import UserService
    from api.db.services.workspace_service import (
        WsMemberService, WorkspaceService,
    )
    with DB.connection_context():
        users = list(UserService.query(email=CI_EMAIL))
        if not users:
            pytest.skip(f"User {CI_EMAIL} not found — provision before running.")
        u = users[0]
        memberships = WsMemberService.list_workspaces_for_user(u.id)
        if not memberships:
            pytest.skip(f"User {CI_EMAIL} has no workspace membership.")
        ok, ws = WorkspaceService.get_by_id(memberships[0].workspace_id)
        if not ok or ws is None:
            pytest.skip(f"Workspace {memberships[0].workspace_id} not found.")
        return u.id, ws.tenant_id


# ---------------------------------------------------------------------------
# Part 1 — Functional equivalence: new JOIN must return the same verdict
#          as the legacy 4-query path on every permission combination.
# ---------------------------------------------------------------------------

def _legacy_has_permission(_rbac, user_id: str, tenant_id: str, perm) -> bool:
    """Faithful replica of the pre-optimisation has_permission() flow,
    using the still-extant single-purpose helpers in rbac.py. Used here as
    a reference oracle to validate the new JOIN-backed path."""
    from api.db.services.user_service import UserService
    e, user = UserService.get_by_id(user_id)
    if e and user and user.is_superuser:
        return True
    workspace = _rbac.resolve_workspace_from_tenant(tenant_id)
    if not workspace:
        return False
    org_role = _rbac.get_user_org_role(user_id, workspace.org_id)
    if org_role == _rbac.OrgRole.ORG_ADMIN:
        return True
    ws_role = _rbac.get_user_ws_role(user_id, workspace.id)
    if not ws_role:
        return False
    return perm in _rbac.ROLE_PERMISSIONS.get(ws_role, set())


@pytest.mark.parametrize("perm_attr", [
    "DATASET_READ", "DATASET_CREATE", "DATASET_DELETE",
    "LLM_CONFIGURE", "MEMBER_INVITE", "AUDIT_READ",
])
def test_join_path_matches_legacy_for_all_permissions(rbac, ci_user_and_tenant, perm_attr):
    _rbac, DB = rbac
    user_id, tenant_id = ci_user_and_tenant
    perm = getattr(_rbac.Permission, perm_attr)
    with DB.connection_context():
        legacy = _legacy_has_permission(_rbac, user_id, tenant_id, perm)
        modern = _rbac.has_permission(user_id, tenant_id, perm)
    assert modern == legacy, (
        f"JOIN-based has_permission diverges from legacy on {perm.value}: "
        f"new={modern} legacy={legacy}. The JOIN must be a faithful refactor."
    )


def test_join_returns_none_for_unknown_user(rbac):
    """Unknown user → None context → has_permission must deny."""
    _rbac, DB = rbac
    with DB.connection_context():
        ctx = _rbac._resolve_rbac_context("0" * 32, "0" * 32)
        assert ctx is None, "Unknown user must yield None context (not a partial dict)."
        assert _rbac.has_permission("0" * 32, "0" * 32, _rbac.Permission.DATASET_READ) is False


def test_join_handles_missing_workspace(rbac, ci_user_and_tenant):
    """Existing user + non-existent tenant_id → workspace_id=None → deny."""
    _rbac, DB = rbac
    user_id, _ = ci_user_and_tenant
    bogus_tenant = "f" * 32
    with DB.connection_context():
        ctx = _rbac._resolve_rbac_context(user_id, bogus_tenant)
        assert ctx is not None, "User exists, ctx must not be None."
        assert ctx["workspace_id"] is None, (
            "Non-existent tenant_id must yield workspace_id=None, "
            f"got {ctx['workspace_id']}."
        )
        assert _rbac.has_permission(user_id, bogus_tenant, _rbac.Permission.DATASET_READ) is False


# ---------------------------------------------------------------------------
# Part 2 — Per-request caching: within a Quart request, repeated calls must
#          NOT trigger additional resolves.
# ---------------------------------------------------------------------------

async def _within_request_ctx(_rbac, DB, fn):
    """Run `fn(_rbac, DB)` inside a real Quart request context — the only
    place where quart.g is writable. quart.test_request_context is async-only
    in modern Quart, hence this helper."""
    from quart import Quart
    app = Quart(__name__)
    async with app.test_request_context("/"):
        return fn(_rbac, DB)


def _run_async(coro):
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_resolve_rbac_context_cached_within_request(rbac, ci_user_and_tenant, monkeypatch):
    """5 has_permission calls in the same request → 1 DB resolve."""
    _rbac, DB = rbac
    user_id, tenant_id = ci_user_and_tenant

    call_count = {"n": 0}
    real = _rbac._resolve_rbac_context

    def counting(*a, **kw):
        call_count["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(_rbac, "_resolve_rbac_context", counting)

    def body(_rbac, DB):
        with DB.connection_context():
            for perm_attr in ["DATASET_READ", "DATASET_CREATE", "DATASET_UPDATE",
                              "DATASET_DELETE", "AUDIT_READ"]:
                _rbac.has_permission(user_id, tenant_id, getattr(_rbac.Permission, perm_attr))

    _run_async(_within_request_ctx(_rbac, DB, body))

    assert call_count["n"] == 1, (
        f"_resolve_rbac_context was invoked {call_count['n']} time(s) for 5 "
        "has_permission calls in the same request — request-scope cache is broken. "
        "Check api/apps/extensions/rbac.py:_resolve_rbac_context_cached."
    )


def test_resolve_rbac_context_keyed_per_user_tenant_pair(rbac, ci_user_and_tenant, monkeypatch):
    """Different (user, tenant) tuples must each get their own cache entry."""
    _rbac, DB = rbac
    user_id, tenant_id = ci_user_and_tenant

    call_count = {"n": 0}
    real = _rbac._resolve_rbac_context

    def counting(*a, **kw):
        call_count["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(_rbac, "_resolve_rbac_context", counting)

    def body(_rbac, DB):
        with DB.connection_context():
            _rbac.has_permission(user_id, tenant_id, _rbac.Permission.DATASET_READ)
            _rbac.has_permission("0" * 32, tenant_id, _rbac.Permission.DATASET_READ)
            _rbac.has_permission(user_id, "f" * 32, _rbac.Permission.DATASET_READ)

    _run_async(_within_request_ctx(_rbac, DB, body))

    assert call_count["n"] == 3, (
        f"Expected 3 resolve calls (3 distinct cache keys), got {call_count['n']}. "
        "The cache must key on (user_id, tenant_id), never collapse different inputs."
    )


def test_resolve_rbac_context_no_cache_outside_request(rbac, ci_user_and_tenant, monkeypatch):
    """Outside a request context (cron, scripts), every call must hit the DB —
    no g.* available. This guarantees background jobs see fresh data."""
    _rbac, DB = rbac
    user_id, tenant_id = ci_user_and_tenant

    call_count = {"n": 0}
    real = _rbac._resolve_rbac_context

    def counting(*a, **kw):
        call_count["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(_rbac, "_resolve_rbac_context", counting)

    with DB.connection_context():
        _rbac.has_permission(user_id, tenant_id, _rbac.Permission.DATASET_READ)
        _rbac.has_permission(user_id, tenant_id, _rbac.Permission.DATASET_READ)

    assert call_count["n"] == 2, (
        "Without a request context, the cache must be bypassed — every call "
        f"must query the DB. Got {call_count['n']} call(s). A leaky cache "
        "here would make crons return stale RBAC."
    )
