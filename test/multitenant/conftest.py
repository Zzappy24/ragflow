"""
Fixtures for multi-tenant / workspace isolation tests.

These tests require a running RAGFlow server at HOST_ADDRESS with our custom
B2B SaaS multi-tenant layer enabled (X-Workspace-Id middleware).

Configuration via env vars (with defaults for local dev):
  HOST_ADDRESS   default http://127.0.0.1:9380
  TEST_EMAIL     test user email  (default: qa@infiniflow.org)
  TEST_PASSWORD  test user password in plaintext  (default: 123)
"""
import logging
import os
import sys
from pathlib import Path

import pytest
import requests
from requests.auth import AuthBase

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
VERSION = "v1"
# Credentials: either provide a pre-computed auth token + workspace_id directly,
# or provide email/password and the conftest will log in automatically.
TEST_AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN")       # e.g. "Authorization header value"
TEST_WORKSPACE_ID = os.getenv("TEST_WORKSPACE_ID")   # workspace UUID
TEST_EMAIL = os.getenv("TEST_EMAIL", "qa@infiniflow.org")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "123")
INVALID_TOKEN = "invalid_token_000"


# ---------------------------------------------------------------------------
# Workspace-aware auth helpers
# ---------------------------------------------------------------------------

class WorkspaceAuth(AuthBase):
    """requests auth that injects both Authorization and X-Workspace-Id."""

    def __init__(self, token: str, workspace_id: str):
        self._token = token
        self.workspace_id = workspace_id

    def __call__(self, r):
        r.headers["Authorization"] = self._token
        if self.workspace_id:
            r.headers["X-Workspace-Id"] = self.workspace_id
        return r


class BareAuth(AuthBase):
    """Valid JWT, NO X-Workspace-Id header — used to test missing-workspace rejection."""

    def __init__(self, token: str):
        self._token = token

    def __call__(self, r):
        r.headers["Authorization"] = self._token
        return r


# ---------------------------------------------------------------------------
# Session-level login + workspace resolution
# ---------------------------------------------------------------------------

CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")
CI_WORKSPACE_NAME = os.getenv("CI_WORKSPACE_NAME", "Général")

VIEWER_EMAIL = os.getenv("VIEWER_EMAIL", "")
EDITOR_EMAIL = os.getenv("EDITOR_EMAIL", "")


def _generate_credentials(email: str = CI_EMAIL, workspace_name: str = CI_WORKSPACE_NAME):
    """
    Derive test credentials directly from Redis + DB — no browser needed.
    Reads SECRET_KEY from Redis (where the server stores it), signs the
    user's access_token, and returns the workspace named workspace_name.
    Falls back to the first workspace if none matches by name.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer

    from api.db.db_models import DB
    from api.db.services.user_service import UserService
    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    from common.settings import REDIS_CONN

    secret_key = REDIS_CONN.get("ragflow:system:secret_key")
    if not secret_key:
        return None
    jwt = Serializer(secret_key=secret_key)

    with DB.connection_context():
        users = list(UserService.query(email=email))
        if not users:
            return None
        u = users[0]
        auth_token = jwt.dumps(u.access_token)
        memberships = WsMemberService.list_workspaces_for_user(u.id)
        if not memberships:
            return None

        # Prefer the workspace named workspace_name ("Général" by default).
        ws = None
        for m in memberships:
            ok, candidate = WorkspaceService.get_by_id(m.workspace_id)
            if ok and candidate and candidate.name == workspace_name:
                ws = candidate
                break
        if ws is None:
            # Fallback: first workspace (logs a warning so CI catches misconfiguration)
            import warnings
            warnings.warn(
                f"CI workspace '{workspace_name}' not found for {email}. "
                f"Falling back to first available workspace. "
                f"Set CI_WORKSPACE_NAME to the correct workspace name."
            )
            _, ws = WorkspaceService.get_by_id(memberships[0].workspace_id)

        return auth_token, ws.id


@pytest.fixture(scope="session")
def _credentials():
    # Always preferred: explicit env vars (CI pipelines, remote envs).
    if TEST_AUTH_TOKEN and TEST_WORKSPACE_ID:
        return TEST_AUTH_TOKEN, TEST_WORKSPACE_ID

    # Auto-derive from Redis+DB only when explicitly opted in for local dev.
    # Never enabled in shared/remote environments — the secret key must stay server-side.
    if os.getenv("RAGFLOW_TEST_LOCAL_AUTH") == "1":
        creds = _generate_credentials()
        if creds:
            return creds

    pytest.exit(
        "Test credentials not configured.\n\n"
        "Option A — explicit tokens (CI pipelines, remote envs):\n"
        "  export TEST_AUTH_TOKEN='...'\n"
        "  export TEST_WORKSPACE_ID='...'\n\n"
        "Option B — local dev auto-derive (requires Redis + DB access):\n"
        "  export RAGFLOW_TEST_LOCAL_AUTH=1\n"
        f"  (user {CI_EMAIL} must exist and belong to a workspace)\n",
        returncode=1,
    )


@pytest.fixture(scope="session")
def ws_auth(_credentials):
    """WorkspaceAuth — valid JWT + correct workspace header."""
    token, workspace_id = _credentials
    return WorkspaceAuth(token, workspace_id)


@pytest.fixture(scope="session")
def bare_auth(_credentials):
    """BareAuth — valid JWT but no X-Workspace-Id (tests missing-workspace rejection)."""
    token, _ = _credentials
    return BareAuth(token)


@pytest.fixture(scope="session")
def workspace_id(_credentials):
    _, ws_id = _credentials
    return ws_id


def _role_credentials(role_email_env: str, role_name: str):
    """
    Derive credentials for a role-specific user (viewer / editor).
    Requires RAGFLOW_TEST_LOCAL_AUTH=1 or explicit TOKEN+WS env vars.
    Returns None if not configured (tests using this will be skipped).
    """
    token_env = f"{role_name.upper()}_AUTH_TOKEN"
    ws_env    = f"{role_name.upper()}_WORKSPACE_ID"
    token = os.getenv(token_env)
    ws_id = os.getenv(ws_env)
    if token and ws_id:
        return token, ws_id

    if os.getenv("RAGFLOW_TEST_LOCAL_AUTH") == "1":
        email = os.getenv(role_email_env, "")
        if email:
            creds = _generate_credentials(email)
            if creds:
                return creds
    return None


@pytest.fixture(scope="session")
def viewer_auth():
    """WorkspaceAuth for a viewer-role user. Skip if not configured."""
    creds = _role_credentials("VIEWER_EMAIL", "viewer")
    if not creds:
        pytest.skip("Viewer credentials not configured (set VIEWER_EMAIL or VIEWER_AUTH_TOKEN+VIEWER_WORKSPACE_ID)")
    token, ws_id = creds
    return WorkspaceAuth(token, ws_id)


@pytest.fixture(scope="session")
def editor_auth():
    """WorkspaceAuth for an editor-role user. Skip if not configured."""
    creds = _role_credentials("EDITOR_EMAIL", "editor")
    if not creds:
        pytest.skip("Editor credentials not configured (set EDITOR_EMAIL or EDITOR_AUTH_TOKEN+EDITOR_WORKSPACE_ID)")
    token, ws_id = creds
    return WorkspaceAuth(token, ws_id)


# ---------------------------------------------------------------------------
# Dataset fixture scoped to the workspace
# ---------------------------------------------------------------------------

def _create_dataset(auth, name="contract-test"):
    res = requests.post(f"{HOST_ADDRESS}/api/{VERSION}/datasets", auth=auth, json={"name": name})
    data = res.json()
    assert data.get("code") == 0, f"Failed to create dataset: {data}"
    return data["data"]["id"]


def _delete_dataset(auth, kb_id):
    requests.delete(f"{HOST_ADDRESS}/api/{VERSION}/datasets", auth=auth, json={"ids": [kb_id]})


@pytest.fixture()
def ws_dataset(ws_auth):
    """Ephemeral dataset in the test workspace — deleted after each test."""
    kb_id = _create_dataset(ws_auth)
    yield kb_id
    _delete_dataset(ws_auth, kb_id)


# ---------------------------------------------------------------------------
# Code product — org + entitlement + RBAC-ready users fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def org_with_entitlement_and_users():
    """Org + active entitlement (100 EUR / 1mo) + 3 users wired for Code RBAC tests.

    Mirrors test_code_provisioning.org_with_entitlement's seeding, plus:
      - a superuser (is_superuser=1)
      - an org_admin (OrgMember role=org_admin)
      - a plain_member (OrgMember role=member)
    Each user gets an opaque panel session (management.server.auth.sessions.open_session).
    Yields (org_id, {"superuser": ..., "org_admin": ..., "plain_member": ...,
                     "plain_member_email": ...}).
    """
    from api.db.db_models import (
        DB,
        CodeEntitlement,
        CodeKey,
        CodeSpendSnapshot,
        CodeTeam,
        CodeTeamMember,
        Organisation,
        OrgMember,
        User,
    )
    from common.misc_utils import get_uuid
    from management.server.auth.sessions import open_session as create_access_token  # sessions opaques (plus de JWT) — recette 2026-09-07

    org_id = get_uuid()
    user_ids = {}
    emails = {}
    with DB.connection_context():
        Organisation.create(id=org_id, name=f"code-rbac-{org_id[:6]}",
                            slug=f"code-rbac-{org_id[:6]}", created_by="tester")
        CodeEntitlement.create(id=get_uuid(), org_id=org_id, status="active",
                               org_code_budget=100.0, budget_period="1mo", created_by="tester")

        for role in ("superuser", "org_admin", "plain_member"):
            uid = get_uuid()
            email = f"code-rbac-{role}-{uid[:6]}@example.com"
            User.create(id=uid, nickname=f"code-rbac-{role}", email=email,
                       password="x", is_superuser=(role == "superuser"))
            user_ids[role] = uid
            emails[role] = email

        OrgMember.create(id=get_uuid(), org_id=org_id, user_id=user_ids["org_admin"],
                         role="org_admin")
        OrgMember.create(id=get_uuid(), org_id=org_id, user_id=user_ids["plain_member"],
                         role="member")

    tokens = {role: create_access_token(uid) for role, uid in user_ids.items()}
    tokens["plain_member_email"] = emails["plain_member"]

    yield org_id, tokens

    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_id)]
        if team_ids:
            CodeKey.delete().where(CodeKey.code_team_id.in_(team_ids)).execute()
            CodeTeamMember.delete().where(CodeTeamMember.code_team_id.in_(team_ids)).execute()
            CodeSpendSnapshot.delete().where(CodeSpendSnapshot.code_team_id.in_(team_ids)).execute()
        CodeTeam.delete().where(CodeTeam.org_id == org_id).execute()
        CodeEntitlement.delete().where(CodeEntitlement.org_id == org_id).execute()
        OrgMember.delete().where(OrgMember.org_id == org_id).execute()
        User.delete().where(User.id.in_(list(user_ids.values()))).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


@pytest.fixture()
def second_org_admin():
    """A second, unrelated org with its own org_admin — for cross-org IDOR tests.

    Yields (org_id, access_token, admin_email). Not a member of the org created
    by org_with_entitlement_and_users, so it can be used to prove that org A's
    resources (teams/keys) are inaccessible to org B's admin.
    """
    from api.db.db_models import DB, Organisation, OrgMember, User
    from common.misc_utils import get_uuid
    from management.server.auth.sessions import open_session as create_access_token  # sessions opaques (plus de JWT) — recette 2026-09-07

    org_id = get_uuid()
    uid = get_uuid()
    email = f"code-rbac-org-b-admin-{uid[:6]}@example.com"
    with DB.connection_context():
        Organisation.create(id=org_id, name=f"code-rbac-b-{org_id[:6]}",
                            slug=f"code-rbac-b-{org_id[:6]}", created_by="tester")
        User.create(id=uid, nickname="code-rbac-org-b-admin", email=email,
                   password="x", is_superuser=False)
        OrgMember.create(id=get_uuid(), org_id=org_id, user_id=uid, role="org_admin")

    token = create_access_token(uid)

    yield org_id, token, email

    with DB.connection_context():
        OrgMember.delete().where(OrgMember.org_id == org_id).execute()
        User.delete().where(User.id == uid).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


# ---------------------------------------------------------------------------
# Dedicated test DB guard for test/multitenant/*
# ---------------------------------------------------------------------------

# CODE_TESTS_DB: name of a dedicated MySQL/Postgres schema (e.g. "rag_flow_test")
# to isolate this directory's tests — most notably the code_* housekeeping
# tests, which call housekeeping()/reconcile_all()/snapshot_spend() and would
# otherwise read/write real data in whatever DB the server is pointed at.
# When set, Peewee is re-pointed at that schema for the whole test session and
# tables are created there. When NOT set (the common local-dev case today),
# tests keep running against the shared dev DB — a session-level warning is
# logged so it's obvious in test output. A hard skip isn't used here because
# it would break local runs for everyone until a second schema is provisioned;
# the row-level scoping and >= 1 / teardown-only-what-you-created assertions
# added alongside this fixture (see test_code_housekeeping.py) are what make
# running against the shared DB safe in the meantime.
CODE_TESTS_DB = os.getenv("CODE_TESTS_DB")


@pytest.fixture(scope="session", autouse=True)
def code_tests_db_guard():
    if CODE_TESTS_DB:
        from api.db.db_models import DB, init_database_tables
        from common import settings

        db_config = settings.DATABASE.copy()
        db_config.pop("name", None)
        DB.close_all()
        DB.init(CODE_TESTS_DB, **db_config)
        init_database_tables()
        logging.getLogger(__name__).warning(
            "test/multitenant running against isolated DB %r (CODE_TESTS_DB)", CODE_TESTS_DB)
    else:
        logging.getLogger(__name__).warning(
            "code tests running against the SHARED dev DB — set CODE_TESTS_DB to isolate")
    yield


# ---------------------------------------------------------------------------
# Fixtures LIVE partagées (test_live_*.py) — serveur + panel + moteurs vivants.
# Outils dans _live.py. Workspaces jetables archivés puis purgés en fin de module.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="module")
def live_panel():
    import _live
    return _live.Panel()


@pytest.fixture(scope="module")
def live_primary(ws_auth, workspace_id):
    import _live
    ws = _live.tenant_of_workspace(workspace_id)
    return {"auth": ws_auth, "ws_id": workspace_id, "tenant_id": ws.tenant_id, "org_id": ws.org_id}


@pytest.fixture(scope="module")
def live_second(live_panel, live_primary):
    import time as _time

    import _live
    ws = _live.make_disposable_workspace(live_panel, live_primary, f"recette-live IDOR {int(_time.time()) % 100000}")
    yield ws
    _live.purge_workspace(live_panel, ws["org_id"], ws["ws_id"])


@pytest.fixture(scope="module")
def live_content1(live_primary):
    import _live
    c = _live.setup_content(live_primary["auth"], "T1", "PERROQUET-UN")
    yield c
    if c["chat"]:
        _live.api(live_primary["auth"], "DELETE", "/chats", json={"ids": [c["chat"]]})
    _live.api(live_primary["auth"], "DELETE", "/datasets", json={"ids": [c["kb"]]})


@pytest.fixture(scope="module")
def live_content2(live_second):
    import _live
    return _live.setup_content(live_second["auth"], "T2", "PERROQUET-DEUX")


@pytest.fixture(scope="module")
def live_beta1(live_primary):
    import _live
    r = _live.api(live_primary["auth"], "POST", "/system/tokens", json={})
    assert _live.code(r) == 0, r.text
    return r.json()["data"]["beta"]
