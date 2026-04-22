"""
RBAC permission enforcement tests — viewer / editor / ws_admin.

Part 1 — Unit tests (rbac.py logic, no server needed):
  Test that the ROLE_PERMISSIONS matrix grants and denies the right permissions.

Part 2 — Integration tests (live server required):
  Test that HTTP endpoints return 403 for viewers, 200 for editors/admins.

  Extra credentials via env vars:
    VIEWER_AUTH_TOKEN + VIEWER_WORKSPACE_ID   — user with viewer role
    EDITOR_AUTH_TOKEN + EDITOR_WORKSPACE_ID   — user with editor role
  If not set, integration tests are skipped.

Run:
    uv run python -m pytest test/multitenant/test_rbac_permissions.py -v
"""
import os
import sys
from pathlib import Path

import pytest
import requests

# ---------------------------------------------------------------------------
# Sys-path + minimal mocks so rbac.py can be imported without the full stack
# (avoids the xgboost / rag / deepdoc heavy import chain).
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import importlib.util  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

# Load rbac.py directly — bypasses api/apps/__init__.py (Flask app init)
# and avoids the xgboost/deepdoc heavy import chain.
for _mod in ["api.utils.api_utils", "api.utils.tenant_context"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

_rbac_path = Path(__file__).resolve().parents[2] / "api" / "apps" / "extensions" / "rbac.py"
_spec = importlib.util.spec_from_file_location("rbac_standalone", _rbac_path)
_rbac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rbac)

ROLE_PERMISSIONS = _rbac.ROLE_PERMISSIONS
WsRole = _rbac.WsRole
Permission = _rbac.Permission

HOST = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
API = f"{HOST}/api/v1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _headers(token: str, workspace_id: str) -> dict:
    return {"Authorization": token, "X-Workspace-Id": workspace_id}


# ============================================================================
# PART 1 — Unit tests: permission matrix logic
# ============================================================================

class TestPermissionMatrix:
    """Tests for api/apps/extensions/rbac.py — no server needed."""

    def _has(self, role, perm):
        return perm in ROLE_PERMISSIONS.get(role, set())

    # -- viewer ---------------------------------------------------------------

    def test_viewer_can_read_dataset(self):
        assert self._has(WsRole.VIEWER, Permission.DATASET_READ)

    def test_viewer_can_read_document(self):
        assert self._has(WsRole.VIEWER, Permission.DOCUMENT_READ)

    def test_viewer_can_read_chat(self):
        assert self._has(WsRole.VIEWER, Permission.CHAT_READ)

    def test_viewer_can_use_chat(self):
        assert self._has(WsRole.VIEWER, Permission.CHAT_USE)

    def test_viewer_can_read_agent(self):
        assert self._has(WsRole.VIEWER, Permission.AGENT_READ)

    def test_viewer_cannot_create_dataset(self):
        assert not self._has(WsRole.VIEWER, Permission.DATASET_CREATE)

    def test_viewer_cannot_delete_dataset(self):
        assert not self._has(WsRole.VIEWER, Permission.DATASET_DELETE)

    def test_viewer_cannot_update_dataset(self):
        assert not self._has(WsRole.VIEWER, Permission.DATASET_UPDATE)

    def test_viewer_cannot_create_document(self):
        assert not self._has(WsRole.VIEWER, Permission.DOCUMENT_CREATE)

    def test_viewer_cannot_delete_document(self):
        assert not self._has(WsRole.VIEWER, Permission.DOCUMENT_DELETE)

    def test_viewer_cannot_create_chat(self):
        assert not self._has(WsRole.VIEWER, Permission.CHAT_CREATE)

    def test_viewer_cannot_update_chat(self):
        assert not self._has(WsRole.VIEWER, Permission.CHAT_UPDATE)

    def test_viewer_cannot_delete_chat(self):
        assert not self._has(WsRole.VIEWER, Permission.CHAT_DELETE)

    def test_viewer_cannot_create_agent(self):
        assert not self._has(WsRole.VIEWER, Permission.AGENT_CREATE)

    def test_viewer_cannot_update_agent(self):
        assert not self._has(WsRole.VIEWER, Permission.AGENT_UPDATE)

    def test_viewer_cannot_delete_agent(self):
        assert not self._has(WsRole.VIEWER, Permission.AGENT_DELETE)

    def test_viewer_cannot_configure_llm(self):
        assert not self._has(WsRole.VIEWER, Permission.LLM_CONFIGURE)

    def test_viewer_cannot_invite_members(self):
        assert not self._has(WsRole.VIEWER, Permission.MEMBER_INVITE)

    def test_viewer_cannot_manage_api_keys(self):
        assert not self._has(WsRole.VIEWER, Permission.API_KEY_MANAGE)

    # -- editor ---------------------------------------------------------------

    def test_editor_can_create_dataset(self):
        assert self._has(WsRole.EDITOR, Permission.DATASET_CREATE)

    def test_editor_can_delete_dataset(self):
        assert self._has(WsRole.EDITOR, Permission.DATASET_DELETE)

    def test_editor_can_update_dataset(self):
        assert self._has(WsRole.EDITOR, Permission.DATASET_UPDATE)

    def test_editor_can_create_document(self):
        assert self._has(WsRole.EDITOR, Permission.DOCUMENT_CREATE)

    def test_editor_can_delete_document(self):
        assert self._has(WsRole.EDITOR, Permission.DOCUMENT_DELETE)

    def test_editor_can_create_chat(self):
        assert self._has(WsRole.EDITOR, Permission.CHAT_CREATE)

    def test_editor_can_use_chat(self):
        assert self._has(WsRole.EDITOR, Permission.CHAT_USE)

    def test_editor_can_create_agent(self):
        assert self._has(WsRole.EDITOR, Permission.AGENT_CREATE)

    def test_editor_cannot_configure_llm(self):
        assert not self._has(WsRole.EDITOR, Permission.LLM_CONFIGURE)

    def test_editor_cannot_invite_members(self):
        assert not self._has(WsRole.EDITOR, Permission.MEMBER_INVITE)

    def test_editor_cannot_manage_api_keys(self):
        assert not self._has(WsRole.EDITOR, Permission.API_KEY_MANAGE)

    # -- ws_admin -------------------------------------------------------------

    def test_ws_admin_has_all_permissions(self):
        for perm in Permission:
            assert perm in ROLE_PERMISSIONS[WsRole.WS_ADMIN], \
                f"ws_admin missing permission: {perm}"

    def test_ws_admin_can_configure_llm(self):
        assert self._has(WsRole.WS_ADMIN, Permission.LLM_CONFIGURE)

    def test_ws_admin_can_invite_members(self):
        assert self._has(WsRole.WS_ADMIN, Permission.MEMBER_INVITE)



# ============================================================================
# PART 2 — Integration tests: endpoint-level enforcement
# Skipped if VIEWER_AUTH_TOKEN / VIEWER_WORKSPACE_ID are not set.
# ============================================================================

# ============================================================================
# PART 2 — Integration tests: endpoint-level enforcement (live server)
# Uses viewer_auth / editor_auth / ws_auth fixtures from conftest.py.
# Skipped automatically if credentials are not configured.
# ============================================================================

def _assert_denied(auth, method, path, body=None):
    """Assert the endpoint returns a permission-denied response (code 403)."""
    r = requests.request(method, f"{API}{path}", auth=auth, json=body, timeout=10)
    data = r.json()
    denied = (r.status_code == 403) or (data.get("code") == 403)
    assert denied, (
        f"Expected 403 for {method} {path}, got HTTP {r.status_code} body={data}"
    )


def _assert_allowed(auth, method, path, body=None):
    """Assert the endpoint does NOT return 403 (may still return other errors)."""
    r = requests.request(method, f"{API}{path}", auth=auth, json=body, timeout=10)
    data = r.json()
    assert data.get("code") != 403, (
        f"Expected non-403 for {method} {path}, got {r.status_code} body={data}"
    )


class TestViewerForbiddenWrites:
    """Viewer must receive 403 on all write endpoints."""

    def test_viewer_cannot_create_dataset(self, viewer_auth):
        _assert_denied(viewer_auth, "POST", "/datasets", {"name": "viewer-should-fail"})

    def test_viewer_cannot_delete_dataset(self, viewer_auth):
        _assert_denied(viewer_auth, "DELETE", "/datasets", {"ids": ["fake-id"]})

    def test_viewer_cannot_upload_document(self, viewer_auth):
        # Permission check fires before dataset existence check
        _assert_denied(viewer_auth, "POST", "/datasets/nonexistent-rbac/documents", {})

    def test_viewer_cannot_update_document(self, viewer_auth):
        _assert_denied(viewer_auth, "PATCH", "/datasets/nonexistent-rbac/documents/doc1", {"name": "x"})

    def test_viewer_cannot_update_auto_metadata(self, viewer_auth):
        _assert_denied(viewer_auth, "PUT", "/datasets/nonexistent-rbac/auto_metadata", {})

    def test_viewer_cannot_update_session(self, viewer_auth):
        _assert_denied(viewer_auth, "PATCH", "/chats/nonexistent-rbac/sessions/sess1", {"name": "x"})

    def test_viewer_cannot_create_memory(self, viewer_auth):
        _assert_denied(viewer_auth, "POST", "/memories", {
            "name": "test", "memory_type": "short_term", "embd_id": "x", "llm_id": "x",
        })

    def test_viewer_cannot_delete_memory(self, viewer_auth):
        _assert_denied(viewer_auth, "DELETE", "/memories/nonexistent-rbac")

    def test_viewer_cannot_add_message(self, viewer_auth):
        _assert_denied(viewer_auth, "POST", "/messages", {
            "memory_id": "x", "agent_id": "x", "session_id": "x",
            "user_input": "hi", "agent_response": "hi",
        })


class TestViewerAllowedReads:
    """Viewer must be allowed to read (not 403)."""

    def test_viewer_can_list_datasets(self, viewer_auth):
        _assert_allowed(viewer_auth, "GET", "/datasets")

    def test_viewer_can_list_memories(self, viewer_auth):
        _assert_allowed(viewer_auth, "GET", "/memories")

    def test_viewer_can_list_documents(self, viewer_auth, ws_dataset):
        _assert_allowed(viewer_auth, "GET", f"/datasets/{ws_dataset}/documents")

    def test_viewer_can_get_auto_metadata(self, viewer_auth, ws_dataset):
        _assert_allowed(viewer_auth, "GET", f"/datasets/{ws_dataset}/auto_metadata")


LLM_API = f"{HOST}/v1/llm"


class TestEditorForbiddenAdminOps:
    """Editor must be blocked on ws_admin-only operations (LLM_CONFIGURE)."""

    def _denied_llm(self, auth, method, path, body=None):
        r = requests.request(method, f"{LLM_API}{path}", auth=auth, json=body, timeout=10)
        data = r.json()
        denied = (r.status_code == 403) or (data.get("code") == 403)
        assert denied, f"Expected 403 for editor on {method} {LLM_API}{path}, got {r.status_code} body={data}"

    def test_editor_cannot_set_api_key(self, editor_auth):
        self._denied_llm(editor_auth, "POST", "/set_api_key", {"llm_factory": "OpenAI", "api_key": "sk-test"})

    def test_editor_cannot_add_llm(self, editor_auth):
        self._denied_llm(editor_auth, "POST", "/add_llm", {"llm_factory": "OpenAI", "llm_name": "gpt-4"})

    def test_editor_cannot_delete_llm(self, editor_auth):
        self._denied_llm(editor_auth, "POST", "/delete_llm", {"llm_factory": "OpenAI", "llm_name": "gpt-4"})

    def test_editor_cannot_delete_factory(self, editor_auth):
        self._denied_llm(editor_auth, "POST", "/delete_factory", {"llm_factory": "OpenAI"})


class TestEditorAllowedWrites:
    """Editor must be able to create and delete datasets."""

    def test_editor_can_create_and_delete_dataset(self, editor_auth):
        r = requests.post(f"{API}/datasets", auth=editor_auth, json={"name": "rbac-editor-test"}, timeout=10)
        data = r.json()
        assert data.get("code") == 0, f"Editor create dataset failed: {data}"
        ds_id = data["data"]["id"]

        r = requests.delete(f"{API}/datasets", auth=editor_auth, json={"ids": [ds_id]}, timeout=10)
        data = r.json()
        assert data.get("code") == 0, f"Editor delete dataset failed: {data}"

    def test_editor_can_access_document_upload_endpoint(self, editor_auth):
        # Create a dataset first
        r = requests.post(f"{API}/datasets", auth=editor_auth, json={"name": "rbac-editor-doc-test"}, timeout=10)
        data = r.json()
        assert data.get("code") == 0, f"Editor create dataset failed: {data}"
        ds_id = data["data"]["id"]

        try:
            # POST without a file — RBAC passes (not 403), business logic rejects for missing file
            r = requests.post(f"{API}/datasets/{ds_id}/documents", auth=editor_auth, timeout=10)
            data = r.json()
            assert data.get("code") != 403, f"Editor was blocked by RBAC on document upload: {data}"
        finally:
            requests.delete(f"{API}/datasets", auth=editor_auth, json={"ids": [ds_id]}, timeout=10)


class TestApiKeyManagement:
    """Only ws_admin can create/delete API keys (Permission.API_KEY_MANAGE)."""

    def test_viewer_cannot_create_api_key(self, viewer_auth):
        _assert_denied(viewer_auth, "POST", "/system/tokens")

    def test_viewer_cannot_delete_api_key(self, viewer_auth):
        _assert_denied(viewer_auth, "DELETE", "/system/tokens/fake-token")

    def test_editor_cannot_create_api_key(self, editor_auth):
        _assert_denied(editor_auth, "POST", "/system/tokens")

    def test_editor_cannot_delete_api_key(self, editor_auth):
        _assert_denied(editor_auth, "DELETE", "/system/tokens/fake-token")

    def test_admin_can_create_and_delete_api_key(self, ws_auth):
        r = requests.post(f"{API}/system/tokens", auth=ws_auth, timeout=10)
        data = r.json()
        assert data.get("code") == 0, f"Admin create API key failed: {data}"
        token = data["data"]["token"]
        r = requests.delete(f"{API}/system/tokens/{token}", auth=ws_auth, timeout=10)
        data = r.json()
        assert data.get("code") == 0, f"Admin delete API key failed: {data}"


class TestAdminAllowedWrites:
    """ws_admin must be able to perform all write operations."""

    def test_admin_can_create_and_delete_dataset(self, ws_auth):
        r = requests.post(f"{API}/datasets", auth=ws_auth, json={"name": "rbac-admin-test"}, timeout=10)
        data = r.json()
        assert data.get("code") == 0, f"Admin create dataset failed: {data}"
        ds_id = data["data"]["id"]

        r = requests.delete(f"{API}/datasets", auth=ws_auth, json={"ids": [ds_id]}, timeout=10)
        data = r.json()
        assert data.get("code") == 0, f"Admin delete dataset failed: {data}"
