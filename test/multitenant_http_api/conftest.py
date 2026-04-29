"""
Workspace-aware bridge for upstream's `test/testcases/test_http_api/` suite.

WHAT THIS DOES
--------------
Upstream ships ~100 HTTP-API tests under `test/testcases/test_http_api/`.
They assume:
  - A `qa@infiniflow.org` user (hardcoded with an encrypted password).
  - The user's session token used to mint an API key via /system/tokens.
  - That token authorizes against the user's PERSONAL tenant (single-tenant flow).
  - ZHIPU_AI_API_KEY env var is set (else `pytest.exit` at collect time).

In our fork the API token must be tied to a WORKSPACE tenant (workspace is
resolved via the X-Workspace-Id header at token-creation time). This conftest
overrides upstream's `auth`, `token`, and `HttpApiAuth` fixtures so the same
test code runs against the workspace tenant — giving us upstream's full test
maintenance for free at every merge.

NAMESPACE CONFLICT — `common`
-----------------------------
The name `common` is both a package at REPO_ROOT (`common.settings`, etc.) and
a module at `test/testcases/test_http_api/common.py` (upstream HTTP helpers).
We need both. So we import RAGFlow `common.*` symbols eagerly here (at conftest
module load), THEN poison sys.path with upstream's path so `from common import
batch_create_datasets` resolves to the upstream module from inside test files.

SAFETY
------
Many upstream fixtures (`clear_datasets`, etc.) call `delete_all_*` which WIPE
the workspace. Use a dedicated CI workspace (`CI_WORKSPACE_NAME=ci-test`) —
never run against a workspace with real data. Bridge skips by default unless
RAGFLOW_TEST_LOCAL_AUTH=1.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \
      uv run python -m pytest test/multitenant_http_api/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_TESTS = REPO_ROOT / "test" / "testcases"


# ---------------------------------------------------------------------------
# STEP 1 — eager import of RAGFlow `common.*` and DB services BEFORE we
# add upstream's test_http_api/ to sys.path (which would shadow the package).
# ---------------------------------------------------------------------------

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.settings import REDIS_CONN  # noqa: E402
from itsdangerous.url_safe import URLSafeTimedSerializer as _Serializer  # noqa: E402
from api.db.db_models import DB as _DB  # noqa: E402
from api.db.services.user_service import UserService as _UserService  # noqa: E402
from api.db.services.workspace_service import (  # noqa: E402
    WorkspaceService as _WorkspaceService,
    WsMemberService as _WsMemberService,
)


# ---------------------------------------------------------------------------
# STEP 2 — bypass upstream `configs.py`'s ZHIPU_AI_API_KEY hardline check.
# Must happen before `from configs import ...`.
# ---------------------------------------------------------------------------
os.environ.setdefault("ZHIPU_AI_API_KEY", "stub-not-actually-called")


# ---------------------------------------------------------------------------
# STEP 3 — make upstream test infrastructure importable.
# Order matters: test_http_api/ first so plain `common` resolves to upstream's
# helpers, NOT to the RAGFlow `common` package.
#
# Step 1 imported `common.settings` which cached the `common` PACKAGE in
# sys.modules. We bust that cache so the next `from common import …` resolves
# to upstream's `test_http_api/common.py` MODULE. The Python objects we already
# bound (REDIS_CONN, _DB, _UserService, …) are unaffected — they're held by
# this module's namespace, not by the sys.modules entry.
# ---------------------------------------------------------------------------
for p in (
    UPSTREAM_TESTS / "test_http_api",
    UPSTREAM_TESTS,
    UPSTREAM_TESTS / "utils",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

for _cached in [k for k in sys.modules if k == "common" or k.startswith("common.")]:
    del sys.modules[_cached]

# `common` here is upstream's test_http_api/common.py — the RAGFlow `common`
# package was already imported eagerly above and lives in sys.modules.
import pytest                                    # noqa: E402
import requests                                  # noqa: E402
from libs.auth import RAGFlowHttpApiAuth         # noqa: E402
from configs import HOST_ADDRESS, VERSION        # noqa: E402


# ---------------------------------------------------------------------------
# Workspace credential resolution — mirrors test/multitenant/conftest.py
# ---------------------------------------------------------------------------

CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")
CI_WORKSPACE_NAME = os.getenv("CI_WORKSPACE_NAME", "Général")
TEST_AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN")
TEST_WORKSPACE_ID = os.getenv("TEST_WORKSPACE_ID")


def _generate_workspace_credentials() -> tuple[str, str] | None:
    """Derive (auth_token, workspace_id) directly from Redis + DB.

    Mirrors test/multitenant/conftest.py::_generate_credentials so the two
    suites share the same identity in local dev.
    """
    secret_key = REDIS_CONN.get("ragflow:system:secret_key")
    if not secret_key:
        return None
    jwt = _Serializer(secret_key=secret_key)

    with _DB.connection_context():
        users = list(_UserService.query(email=CI_EMAIL))
        if not users:
            return None
        u = users[0]
        auth_token = jwt.dumps(u.access_token)
        memberships = _WsMemberService.list_workspaces_for_user(u.id)
        if not memberships:
            return None

        ws = None
        for m in memberships:
            ok, candidate = _WorkspaceService.get_by_id(m.workspace_id)
            if ok and candidate and candidate.name == CI_WORKSPACE_NAME:
                ws = candidate
                break
        if ws is None:
            _, ws = _WorkspaceService.get_by_id(memberships[0].workspace_id)
        return auth_token, ws.id


@pytest.fixture(scope="session")
def _ws_credentials() -> tuple[str, str]:
    if TEST_AUTH_TOKEN and TEST_WORKSPACE_ID:
        return TEST_AUTH_TOKEN, TEST_WORKSPACE_ID
    if os.getenv("RAGFLOW_TEST_LOCAL_AUTH") == "1":
        creds = _generate_workspace_credentials()
        if creds:
            return creds
    pytest.skip(
        "Workspace credentials not configured. Set RAGFLOW_TEST_LOCAL_AUTH=1 "
        f"(needs user {CI_EMAIL} with workspace {CI_WORKSPACE_NAME}) "
        "or TEST_AUTH_TOKEN + TEST_WORKSPACE_ID."
    )


# ---------------------------------------------------------------------------
# Fixtures that override upstream's defaults.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def auth(_ws_credentials) -> str:
    """Session Authorization header for the workspace user.

    Replaces upstream `auth` (which registers qa@infiniflow.org) with our
    workspace test user's existing session token.
    """
    token, _ = _ws_credentials
    return token


@pytest.fixture(scope="session")
def token(_ws_credentials) -> str:
    """Bearer token = the user's SESSION JWT (not an APIToken).

    `_load_user` (api/apps/__init__.py) tries JWT-decode FIRST, falling back
    to APIToken lookup only if decode fails. The session token is a JWT-signed
    blob of the user's `access_token`, so passing it via `Authorization: Bearer
    <session>` lets `@login_required` resolve `current_user` correctly.

    Why not mint a real APIToken via /system/tokens?
      - In a workspace context (X-Workspace-Id sent), the minted token is
        scoped to the workspace tenant, but `_load_user`'s APIToken fallback
        does `UserService.query(id=tenant_id)` — workspace tenants are not
        users, so the lookup yields no `current_user` and `@login_required`
        rejects with 401.
      - In a personal context (no X-Workspace-Id), the test user lacks the
        `api_key.manage` permission and /system/tokens returns 403.

    Punt: re-use the JWT we already have. The X-Workspace-Id header (added by
    `_WorkspaceBearerAuth` below) drives workspace selection at request time.
    """
    auth_header, _workspace_id = _ws_credentials
    return auth_header


class _WorkspaceBearerAuth(RAGFlowHttpApiAuth):
    """Bearer SDK auth + X-Workspace-Id on every request.

    Wraps upstream's `RAGFlowHttpApiAuth` so the workspace middleware can
    resolve `active_tenant_id` to the workspace tenant on each call. Without
    this header the token would default to the user's PERSONAL tenant — the
    test suite would then exercise single-tenant flows, not workspace flows.
    """

    def __init__(self, token: str, workspace_id: str):
        super().__init__(token)
        self._workspace_id = workspace_id

    def __call__(self, r):
        r = super().__call__(r)
        r.headers["X-Workspace-Id"] = self._workspace_id
        return r


@pytest.fixture(scope="session")
def HttpApiAuth(token, _ws_credentials) -> RAGFlowHttpApiAuth:
    """Bearer-auth helper that ALSO sends X-Workspace-Id."""
    _, workspace_id = _ws_credentials
    return _WorkspaceBearerAuth(token, workspace_id)


# ---------------------------------------------------------------------------
# Re-implement the small set of upstream fixtures the tests depend on.
# (Upstream's conftest at test/testcases/conftest.py is NOT a pytest parent of
# this directory, so its fixtures don't apply automatically.)
# ---------------------------------------------------------------------------

from common import (                                   # noqa: E402
    batch_create_datasets,
    delete_all_datasets,
)


@pytest.fixture(scope="function")
def clear_datasets(request, HttpApiAuth):
    """Wipe all datasets in the workspace AFTER the test runs.

    DESTRUCTIVE — only safe in a dedicated CI workspace.
    """
    def cleanup():
        delete_all_datasets(HttpApiAuth)
    request.addfinalizer(cleanup)


@pytest.fixture(scope="function")
def add_dataset_func(request, HttpApiAuth):
    """Create one dataset, return its id, wipe all datasets at teardown."""
    def cleanup():
        delete_all_datasets(HttpApiAuth)
    request.addfinalizer(cleanup)
    return batch_create_datasets(HttpApiAuth, 1)[0]


@pytest.fixture(scope="class")
def add_dataset(request, HttpApiAuth):
    """Class-scope dataset fixture — same semantics as upstream's add_dataset."""
    def cleanup():
        delete_all_datasets(HttpApiAuth)
    request.addfinalizer(cleanup)
    return batch_create_datasets(HttpApiAuth, 1)[0]


# ---------------------------------------------------------------------------
# Per-subdirectory upstream fixtures — loaded eagerly so they're discoverable
# by tests in this bridge directory regardless of where upstream defined them.
#
# Why we need this: pytest only collects fixtures from `conftest.py` files in
# the test file's directory tree. Upstream's `test_dataset_management/
# conftest.py` defines `add_datasets`, `add_datasets_func`; their `test_chat_
# assistant_management/conftest.py` defines its own. Our bridge directory is
# a sibling, not a parent, so those fixtures are invisible by default.
#
# The loop below imports each upstream subdir conftest as a module and pulls
# every `*_func`, `add_*`, `clear_*` symbol into this conftest's namespace.
# Pytest then sees them as fixtures defined here.
# ---------------------------------------------------------------------------

import importlib.util as _ilu                  # noqa: E402

_UPSTREAM_HTTP_API = UPSTREAM_TESTS / "test_http_api"


def _import_upstream_subconftests() -> None:
    """Eagerly load every test_http_api/<subdir>/conftest.py and re-export its
    @pytest.fixture-decorated callables into this module's globals.
    """
    for subdir in sorted(_UPSTREAM_HTTP_API.iterdir()):
        if not subdir.is_dir():
            continue
        sub_conf = subdir / "conftest.py"
        if not sub_conf.exists():
            continue
        spec = _ilu.spec_from_file_location(
            f"_upstream_subconftest_{subdir.name}", str(sub_conf)
        )
        mod = _ilu.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            # If a subdir conftest can't load (e.g. requires fixtures not in
            # this bridge yet), skip it rather than crash the whole suite.
            import warnings
            warnings.warn(f"Skipping upstream subconftest {sub_conf}: {e}")
            continue
        for name in dir(mod):
            if name.startswith("_"):
                continue
            obj = getattr(mod, name)
            # Modern pytest wraps fixture-decorated callables in
            # FixtureFunctionDefinition (has `_fixture_function_marker`).
            # Older pytest used `_pytestfixturefunction` directly. Accept either.
            is_fixture = (
                hasattr(obj, "_fixture_function_marker")
                or hasattr(obj, "_pytestfixturefunction")
            )
            if is_fixture and name not in globals():
                globals()[name] = obj


_import_upstream_subconftests()
