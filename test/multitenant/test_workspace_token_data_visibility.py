"""
Workspace-scoped API token contract — data visibility through the REST API.

Regression net for a class of bugs we hit during the 2026-05-14 upstream merge:
upstream rewrote `dataset_api_service.list_datasets()` to call
`TenantService.get_joined_tenants_by_user_id(tenant_id)` — semantically wrong
in our fork because `tenant_id` is the **workspace tenant_id** (resolved by
our X-Workspace-Id middleware / @add_tenant_id_to_kwargs / token_required
decorators), not a user_id. The lookup returned an empty membership set, so
the service silently returned 0 datasets to perfectly valid callers
(browser users in a workspace, MCP API keys, SDK calls with X-Workspace-Id).

We detected the regression via the heavy E2E MCP tests, but those need the
full MCP server up + a session + a created KB + 60+ seconds to surface what
is a 50ms logical bug. This test exercises the contract *directly* against
the REST API with a workspace-scoped API token: a workspace KB MUST be
visible through GET /api/v1/datasets when called with a token whose
APIToken.tenant_id matches the workspace tenant.

Run with the standard multitenant env:
    RAGFLOW_TEST_LOCAL_AUTH=1 RSA_PASSPHRASE=Welcome \\
    VIEWER_EMAIL=viewer.internal@cyllene.com EDITOR_EMAIL=editor.internal@cyllene.com \\
    uv run python -m pytest test/multitenant/test_workspace_token_data_visibility.py -v
"""
from __future__ import annotations

import os
import secrets
import sys
import uuid
from pathlib import Path

import pytest
import requests

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")


def _server_reachable() -> bool:
    try:
        requests.get(f"{HOST_ADDRESS}/api/v1/system/version", timeout=2)
        return True
    except (requests.RequestException, OSError):
        return False


pytestmark = pytest.mark.skipif(
    not _server_reachable(),
    reason=f"RAGFlow backend unreachable at {HOST_ADDRESS}",
)


@pytest.fixture(scope="module")
def workspace_token_with_kb(workspace_id):
    """
    Provision a fresh workspace-scoped API token + a fresh KB inside that
    workspace, both directly via the DB so we don't depend on whatever
    upstream tinkered with the dataset-creation route.

    Yields (token, kb_id). Tears both down after the module finishes.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from api.db.db_models import DB, APIToken, Workspace, Knowledgebase
    from api.db.services.workspace_service import ApiKeyScopeService, WsMemberService
    from api.db.services.user_service import UserService

    token = "ragflow-test-visibility-" + secrets.token_urlsafe(16)
    kb_id = uuid.uuid4().hex

    with DB.connection_context():
        ws = Workspace.get_or_none(Workspace.id == workspace_id)
        assert ws is not None, f"Test workspace {workspace_id} not found"

        # Pick the first active workspace member as the human owner.
        members = list(
            WsMemberService.model.select().where(
                WsMemberService.model.workspace_id == workspace_id
            )
        )
        member_user_id = None
        for m in members:
            if UserService.query(id=m.user_id):
                member_user_id = m.user_id
                break
        assert member_user_id, "No active user in the test workspace"

        APIToken.create(tenant_id=ws.tenant_id, token=token, source="none")
        ApiKeyScopeService.model.create(
            id=uuid.uuid4().hex,
            token=token,
            workspace_id=workspace_id,
            permissions=["dataset.create", "dataset.read", "dataset.update", "dataset.delete"],
            name="pytest-visibility-key",
            created_by=member_user_id,
            status="1",
        )

        Knowledgebase.create(
            id=kb_id,
            tenant_id=ws.tenant_id,
            name=f"visibility-pytest-{kb_id[:8]}",
            description="dataset_api_service.list_datasets contract test",
            embd_id="nomic-embed-text@Ollama",
            language="English",
            permission="me",
            created_by=member_user_id,
            status="1",
        )

    yield token, kb_id

    with DB.connection_context():
        try:
            Knowledgebase.delete().where(Knowledgebase.id == kb_id).execute()
            ApiKeyScopeService.model.delete().where(
                ApiKeyScopeService.model.token == token
            ).execute()
            APIToken.delete().where(APIToken.token == token).execute()
        except Exception:
            pass


class TestWorkspaceTokenDataVisibility:
    """The contract: a workspace-scoped API token must see KBs in its workspace.

    This guards against the entire class of upstream regressions where a
    service-layer refactor uses `tenant_id` as if it were a `user_id`
    (e.g. via `get_joined_tenants_by_user_id`, `get_by_user_id`, etc.).
    Those bugs are silent — the API returns 200 + an empty list, so the
    web UI just shows "no datasets" and the bug masquerades as a UX issue.
    """

    def test_workspace_kb_visible_via_get_datasets(self, workspace_token_with_kb):
        token, kb_id = workspace_token_with_kb
        res = requests.get(
            f"{HOST_ADDRESS}/api/v1/datasets",
            headers={"Authorization": f"Bearer {token}"},
            params={"page": 1, "page_size": 100},
            timeout=10,
        )
        assert res.status_code == 200, f"GET /datasets failed: {res.status_code} {res.text}"
        body = res.json()
        assert body.get("code") == 0, f"API returned error: {body}"
        ids = [row["id"] for row in body.get("data") or []]
        assert kb_id in ids, (
            f"REGRESSION: workspace KB {kb_id} not visible to its own workspace token.\n"
            f"GET /api/v1/datasets returned {len(ids)} KBs: {ids[:5]}{'...' if len(ids) > 5 else ''}\n"
            f"Total claimed: {body.get('total_datasets')}\n"
            "Likely cause: a service-layer function (probably "
            "dataset_api_service.list_datasets) is calling "
            "get_joined_tenants_by_user_id(tenant_id) — wrong, tenant_id is a "
            "workspace tenant, not a user_id. Use workspace-strict scoping "
            "(tenant_ids = [tenant_id]) instead."
        )

    def test_workspace_kb_count_matches_total(self, workspace_token_with_kb):
        """Sanity: `total_datasets` in the response is consistent with `data` length."""
        token, _ = workspace_token_with_kb
        res = requests.get(
            f"{HOST_ADDRESS}/api/v1/datasets",
            headers={"Authorization": f"Bearer {token}"},
            params={"page": 1, "page_size": 1000},
            timeout=10,
        )
        body = res.json()
        # On a single page that holds everything, len(data) must equal total.
        assert len(body.get("data") or []) == body.get("total_datasets"), body
