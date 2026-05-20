"""
Per-user API keys — RBAC + isolation tests.

Backstory: any workspace member can mint a personal API key, but the key's
permission set is *clamped* to what the caller currently holds. A viewer
cannot self-elevate by minting a ``dataset.write`` key; a member can only
revoke keys they themselves created. These tests pin the contract so the
clamp survives upstream merges (which periodically rework ``rbac.py``,
``token_required``, and ``_load_user``).

Routes exercised:
  POST   /api/v1/api_keys
  GET    /api/v1/api_keys
  DELETE /api/v1/api_keys/<scope_id>

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_api_keys_per_user.py -v
"""
import os

import pytest
import requests

HOST = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
API = f"{HOST}/api/v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create(auth, *, name, permissions, expires_at=None):
    body = {"name": name, "permissions": permissions}
    if expires_at is not None:
        body["expires_at"] = expires_at
    return requests.post(f"{API}/api_keys", auth=auth, json=body, timeout=10).json()


def _list(auth):
    return requests.get(f"{API}/api_keys", auth=auth, timeout=10).json()


def _delete(auth, scope_id):
    return requests.delete(f"{API}/api_keys/{scope_id}", auth=auth, timeout=10).json()


@pytest.fixture
def cleanup():
    """Best-effort cleanup of created scope ids at end of test."""
    created: list[tuple] = []  # (auth, scope_id)

    def track(auth, scope_id):
        created.append((auth, scope_id))

    yield track
    for auth, sid in created:
        try:
            _delete(auth, sid)
        except Exception:
            pass


# ===========================================================================
# Permission clamping — the security-critical path
# ===========================================================================

class TestPermissionClamping:
    """A user cannot mint a key with permissions they don't already hold."""

    def test_viewer_can_mint_read_only_key(self, viewer_auth, cleanup):
        r = _create(
            viewer_auth,
            name="viewer-read-only",
            permissions=["dataset.read", "document.read"],
        )
        assert r.get("code") == 0, f"Viewer should mint read-only key: {r}"
        cleanup(viewer_auth, r["data"]["id"])
        # Token must be returned exactly once on creation.
        assert r["data"].get("token", "").startswith("ragflow-")

    def test_viewer_cannot_mint_write_key(self, viewer_auth):
        r = _create(
            viewer_auth,
            name="viewer-write-fail",
            permissions=["dataset.create"],
        )
        assert r.get("code") != 0, f"Viewer must NOT mint write key: {r}"
        msg = (r.get("message") or "").lower()
        assert "permissions" in msg, f"Error should mention permissions: {r}"

    def test_editor_cannot_mint_api_key_manage(self, editor_auth):
        # api_key.manage is ws_admin only — an editor must not self-elevate.
        r = _create(
            editor_auth,
            name="editor-elevation-fail",
            permissions=["api_key.manage"],
        )
        assert r.get("code") != 0, f"Editor must NOT mint admin-scope key: {r}"

    def test_admin_can_mint_admin_scope_key(self, ws_auth, cleanup):
        r = _create(
            ws_auth,
            name="admin-full-scope",
            permissions=["dataset.read", "dataset.create", "agent.read"],
        )
        assert r.get("code") == 0, f"Admin must be able to mint write key: {r}"
        cleanup(ws_auth, r["data"]["id"])


# ===========================================================================
# Per-user isolation — list + delete are scoped to created_by
# ===========================================================================

class TestPerUserIsolation:
    """User A cannot see or revoke User B's keys via the per-user routes."""

    def test_admin_does_not_see_viewer_key_in_list(
        self, ws_auth, viewer_auth, cleanup,
    ):
        r = _create(
            viewer_auth,
            name="viewer-isolation",
            permissions=["dataset.read"],
        )
        assert r.get("code") == 0, f"Pre-condition failed: {r}"
        viewer_key_id = r["data"]["id"]
        cleanup(viewer_auth, viewer_key_id)

        listing = _list(ws_auth)
        assert listing.get("code") == 0, listing
        ids = {k["id"] for k in listing["data"]["keys"]}
        assert viewer_key_id not in ids, (
            "Admin sees viewer's key in their per-user list — isolation leak"
        )

    def test_admin_cannot_revoke_viewer_key(
        self, ws_auth, viewer_auth, cleanup,
    ):
        r = _create(
            viewer_auth,
            name="viewer-revoke-target",
            permissions=["dataset.read"],
        )
        assert r.get("code") == 0, r
        viewer_key_id = r["data"]["id"]
        cleanup(viewer_auth, viewer_key_id)

        deleted = _delete(ws_auth, viewer_key_id)
        # We return a generic "not found" rather than 403 to avoid leaking
        # the existence of cross-user keys. Either non-zero code or HTTP 404
        # qualifies — what matters is the key is NOT actually deleted.
        assert deleted.get("code") != 0 or "deleted" not in deleted.get("data", {}), (
            f"Admin should not revoke viewer's key via per-user route: {deleted}"
        )

        # Confirm the key still exists from the viewer's side.
        viewer_list = _list(viewer_auth)
        viewer_ids = {k["id"] for k in viewer_list["data"]["keys"]}
        assert viewer_key_id in viewer_ids, (
            "Viewer's key disappeared after admin attempted to revoke it"
        )


# ===========================================================================
# Input validation
# ===========================================================================

class TestInputValidation:
    def test_name_required(self, viewer_auth):
        r = requests.post(
            f"{API}/api_keys",
            auth=viewer_auth,
            json={"permissions": ["dataset.read"]},
            timeout=10,
        ).json()
        assert r.get("code") != 0, f"Missing name should be rejected: {r}"

    def test_permissions_required(self, viewer_auth):
        r = requests.post(
            f"{API}/api_keys",
            auth=viewer_auth,
            json={"name": "no-perms"},
            timeout=10,
        ).json()
        assert r.get("code") != 0, f"Missing permissions must be rejected: {r}"

    def test_unknown_permission_rejected(self, viewer_auth):
        r = _create(
            viewer_auth,
            name="bad-perm",
            permissions=["dataset.invent_a_new_perm"],
        )
        assert r.get("code") != 0, r

    def test_past_expiration_rejected(self, viewer_auth):
        r = _create(
            viewer_auth,
            name="past-expiry",
            permissions=["dataset.read"],
            expires_at="2020-01-01",
        )
        assert r.get("code") != 0, r
        assert "future" in (r.get("message") or "").lower(), r


# ===========================================================================
# Lifecycle round-trip — create → list → delete
# ===========================================================================

class TestLifecycle:
    def test_round_trip(self, ws_auth):
        r = _create(
            ws_auth,
            name="lifecycle-round-trip",
            permissions=["dataset.read"],
        )
        assert r.get("code") == 0, r
        scope_id = r["data"]["id"]

        listing = _list(ws_auth)
        ids = {k["id"] for k in listing["data"]["keys"]}
        assert scope_id in ids, "Created key not visible in own list"

        deleted = _delete(ws_auth, scope_id)
        assert deleted.get("code") == 0, deleted

        # After delete, key must no longer appear.
        post = _list(ws_auth)
        post_ids = {k["id"] for k in post["data"]["keys"]}
        assert scope_id not in post_ids, "Deleted key still listed"

    def test_token_returned_only_on_create(self, ws_auth, cleanup):
        r = _create(
            ws_auth,
            name="token-once",
            permissions=["dataset.read"],
        )
        assert r.get("code") == 0, r
        scope_id = r["data"]["id"]
        cleanup(ws_auth, scope_id)
        assert "token" in r["data"], "Token must be returned on create"

        # Subsequent list must NOT echo the full token, only a preview.
        listing = _list(ws_auth)
        match = next((k for k in listing["data"]["keys"] if k["id"] == scope_id), None)
        assert match is not None
        assert match.get("token") is None, "Full token leaked in list"
        assert match.get("token_preview"), "token_preview missing"
