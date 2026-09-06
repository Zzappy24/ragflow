"""
Management panel — ws_admin access tests.

After 2026-05-03 we relaxed ``management/server/routers/models.py`` from
``_require_ws_org_admin`` (org_admin only) to ``require_ws_admin`` (also
accepts ws_admin scoped to their own workspace). This test pins:

  1. /auth/me returns the new ``workspaces`` array with role.
  2. A ws_admin can read and write the model defaults of THEIR workspace.
  3. A viewer of the same workspace gets 403.
  4. Any auth on a workspace where the caller has no role → 403/404.

These tests are integration-style: they hit the live FastAPI server on
``MANAGEMENT_HOST`` (default ``http://127.0.0.1:9381``). The auth path
mints a JWT directly with ``ADMIN_JWT_SECRET`` so we don't rely on the
RSA-wrapped login endpoint (which is UI plumbing).

Skipped automatically if:
  - The management server is unreachable.
  - ``ADMIN_JWT_SECRET`` is not set.

Run:
    ADMIN_JWT_SECRET=... \\
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_management_panel_ws_admin.py -v
"""
import os
import socket
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

MANAGEMENT_HOST = os.getenv("MANAGEMENT_HOST", "http://127.0.0.1:9381")
# All management routes are mounted under /api/admin (cf. management/server/main.py).
MANAGEMENT_API = f"{MANAGEMENT_HOST}/api/admin"
ADMIN_JWT_SECRET = os.getenv("ADMIN_JWT_SECRET", "")
JWT_ALG = "HS256"


# ---------------------------------------------------------------------------
# Skip the whole module if prerequisites are missing.
# ---------------------------------------------------------------------------

def _reachable(url: str, timeout: float = 1.0) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _reachable(MANAGEMENT_HOST),
        reason=f"Management server unreachable at {MANAGEMENT_HOST}",
    ),
]


# ---------------------------------------------------------------------------
# Helpers — mint a JWT for an arbitrary user_id without going through the
# RSA-wrapped /auth/login endpoint. Mirrors what create_access_token does.
# ---------------------------------------------------------------------------

def _mint_admin_jwt(user_id: str) -> str:
    """Le panel n'accepte plus de JWT : jeton OPAQUE lié à une ligne de la table
    admin_session (management/server/auth/sessions.py). On ouvre la session
    directement en base, comme le ferait /auth/login (recette 2026-09-07 :
    les JWT frappés ici étaient tous refusés en 401)."""
    from management.server.auth.sessions import open_session
    return open_session(user_id)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _user_id_for(email: str) -> str | None:
    from api.db.db_models import DB
    from api.db.services.user_service import UserService
    with DB.connection_context():
        users = list(UserService.query(email=email))
        return users[0].id if users else None


def _ws_admin_workspace_for(user_id: str) -> str | None:
    """First ACTIVE workspace where the user holds the ws_admin role.

    Archived workspaces (status "0") keep their ws_member rows but the panel
    answers 404 for them — picking one made the whole module fail
    (recette 2026-09-07)."""
    from api.db.db_models import DB, Workspace
    from api.db.services.workspace_service import WsMemberService
    with DB.connection_context():
        memberships = WsMemberService.list_workspaces_for_user(user_id)
        for m in memberships:
            if m.role != "ws_admin":
                continue
            ws = Workspace.get_or_none(Workspace.id == m.workspace_id)
            if ws is not None and ws.status == "1":
                return m.workspace_id
    return None


# ---------------------------------------------------------------------------
# Session-scoped fixtures: tokens for ws_admin / viewer.
# ---------------------------------------------------------------------------

CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")
VIEWER_EMAIL = os.getenv("VIEWER_EMAIL", "viewer.internal@cyllene.com")


@pytest.fixture(scope="session")
def ws_admin_token():
    uid = _user_id_for(CI_EMAIL)
    if not uid:
        pytest.skip(f"User {CI_EMAIL} not found in DB")
    return _mint_admin_jwt(uid), uid


@pytest.fixture(scope="session")
def ws_admin_workspace_id(ws_admin_token):
    _, uid = ws_admin_token
    ws_id = _ws_admin_workspace_for(uid)
    if not ws_id:
        pytest.skip(f"User {CI_EMAIL} is not ws_admin in any workspace")
    return ws_id


@pytest.fixture(scope="session")
def viewer_token():
    uid = _user_id_for(VIEWER_EMAIL)
    if not uid:
        pytest.skip(f"User {VIEWER_EMAIL} not found in DB")
    return _mint_admin_jwt(uid), uid


# ===========================================================================
# /auth/me contract — the new `workspaces` array
# ===========================================================================

class TestAuthMe:
    def test_me_returns_workspaces_array(self, ws_admin_token):
        token, _ = ws_admin_token
        r = requests.get(
            f"{MANAGEMENT_API}/auth/me",
            headers=_bearer(token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "workspaces" in body, f"/me missing workspaces field: {body}"
        assert isinstance(body["workspaces"], list)
        # Each entry must carry at least these fields for the frontend.
        if body["workspaces"]:
            entry = body["workspaces"][0]
            for key in ("ws_id", "ws_name", "org_id", "role"):
                assert key in entry, f"/me workspace entry missing {key}: {entry}"

    def test_me_workspaces_includes_ws_admin_role(
        self, ws_admin_token, ws_admin_workspace_id,
    ):
        token, _ = ws_admin_token
        body = requests.get(
            f"{MANAGEMENT_API}/auth/me",
            headers=_bearer(token),
            timeout=10,
        ).json()
        match = next(
            (w for w in body["workspaces"] if w["ws_id"] == ws_admin_workspace_id),
            None,
        )
        assert match is not None, (
            f"Expected workspace {ws_admin_workspace_id} in /me response: {body}"
        )
        assert match["role"] == "ws_admin", match


# ===========================================================================
# Models routes — ws_admin access on their OWN workspace
# ===========================================================================

class TestModelsRoutesWsAdmin:
    def test_ws_admin_can_get_defaults(
        self, ws_admin_token, ws_admin_workspace_id,
    ):
        token, _ = ws_admin_token
        r = requests.get(
            f"{MANAGEMENT_API}/workspaces/{ws_admin_workspace_id}/models/defaults",
            headers=_bearer(token),
            timeout=10,
        )
        assert r.status_code == 200, (
            f"ws_admin should be able to read model defaults of own workspace, "
            f"got {r.status_code}: {r.text}"
        )

    def test_ws_admin_can_list_providers(
        self, ws_admin_token, ws_admin_workspace_id,
    ):
        token, _ = ws_admin_token
        r = requests.get(
            f"{MANAGEMENT_API}/workspaces/{ws_admin_workspace_id}/models/providers",
            headers=_bearer(token),
            timeout=10,
        )
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)


# ===========================================================================
# Negative path — viewer or wrong workspace must be denied
# ===========================================================================

class TestModelsRoutesDenials:
    def test_viewer_cannot_get_defaults(
        self, viewer_token, ws_admin_workspace_id,
    ):
        # Viewer is a member of the workspace but only with viewer role —
        # require_ws_admin rejects.
        token, _ = viewer_token
        r = requests.get(
            f"{MANAGEMENT_API}/workspaces/{ws_admin_workspace_id}/models/defaults",
            headers=_bearer(token),
            timeout=10,
        )
        assert r.status_code == 403, (
            f"Viewer must NOT read model defaults, got {r.status_code}: {r.text}"
        )

    def test_unknown_workspace_returns_404(self, ws_admin_token):
        # ws_admin tries to read a workspace that does not exist — 404 from
        # require_ws_admin's existence check.
        token, _ = ws_admin_token
        fake_ws = uuid.uuid4().hex
        r = requests.get(
            f"{MANAGEMENT_API}/workspaces/{fake_ws}/models/defaults",
            headers=_bearer(token),
            timeout=10,
        )
        assert r.status_code == 404, r.text

    def test_no_token_returns_401(self, ws_admin_workspace_id):
        r = requests.get(
            f"{MANAGEMENT_API}/workspaces/{ws_admin_workspace_id}/models/defaults",
            timeout=10,
        )
        assert r.status_code in (401, 403), r.text
