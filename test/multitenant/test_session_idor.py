"""
Pin GET /agents/<agent_id>/sessions/<session_id> against IDOR.

Background: a leaked session_id from canvas A could be loaded through any
other canvas's session route as long as the caller had CHAT_READ on the
target canvas. The route checked ownership of the canvas but not that the
session belonged to it. We now scope the lookup by dialog_id so the
session must belong to the requested canvas, returning 404 otherwise.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_session_idor.py -v
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

HOST = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
API = f"{HOST}/api/v1"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def two_canvases_with_session(workspace_id):
    """Create two canvases C1, C2 in the active workspace, plus a session
    S1 that belongs to C1. Canvas user_id is the workspace's TENANT_ID
    (B2B SaaS — accessible() compares against active_tenant_id which is
    the workspace's tenant, not the workspace id itself)."""
    from api.db.db_models import API4Conversation, DB, UserCanvas
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(workspace_id)
    assert ok and ws, f"Workspace {workspace_id} missing"
    tenant_id = ws.tenant_id

    c1_id = uuid.uuid4().hex
    c2_id = uuid.uuid4().hex
    s1_id = uuid.uuid4().hex

    with DB.connection_context():
        UserCanvas.create(
            id=c1_id,
            user_id=tenant_id,
            title=f"idor-c1-{c1_id[:8]}",
            description="created by test_session_idor.py",
            dsl={"components": {}, "graph": {"nodes": [], "edges": []}},
            canvas_category="agent_canvas",
            permission="me",
        )
        UserCanvas.create(
            id=c2_id,
            user_id=tenant_id,
            title=f"idor-c2-{c2_id[:8]}",
            description="created by test_session_idor.py",
            dsl={"components": {}, "graph": {"nodes": [], "edges": []}},
            canvas_category="agent_canvas",
            permission="me",
        )
        API4Conversation.create(
            id=s1_id,
            dialog_id=c1_id,
            user_id=tenant_id,
            source="agent",
            message=[],
        )

    yield c1_id, c2_id, s1_id

    with DB.connection_context():
        try:
            API4Conversation.delete().where(API4Conversation.id == s1_id).execute()
        except Exception:
            pass
        UserCanvas.delete().where(UserCanvas.id.in_([c1_id, c2_id])).execute()


# ---------------------------------------------------------------------------

class TestAgentSessionIdor:
    def test_session_loadable_via_owning_canvas(
        self, ws_auth, two_canvases_with_session
    ):
        c1, _, s1 = two_canvases_with_session
        r = requests.get(f"{API}/agents/{c1}/sessions/{s1}", auth=ws_auth, timeout=10)
        body = r.json()
        assert body.get("code") == 0, f"Owner canvas should resolve session: {body}"
        assert body["data"]["id"] == s1

    def test_session_not_loadable_via_other_canvas(
        self, ws_auth, two_canvases_with_session
    ):
        c1, c2, s1 = two_canvases_with_session
        # S1 belongs to C1. Try to fetch it through C2 — must be refused
        # with a generic "not found" so we don't leak existence.
        r = requests.get(f"{API}/agents/{c2}/sessions/{s1}", auth=ws_auth, timeout=10)
        body = r.json()
        assert body.get("code") != 0, (
            f"IDOR: session belonging to {c1} should NOT load via {c2}: {body}"
        )
        assert "not found" in (body.get("message") or "").lower()

    def test_unknown_session_returns_not_found(
        self, ws_auth, two_canvases_with_session
    ):
        c1, _, _ = two_canvases_with_session
        fake = uuid.uuid4().hex
        r = requests.get(f"{API}/agents/{c1}/sessions/{fake}", auth=ws_auth, timeout=10)
        body = r.json()
        assert body.get("code") != 0, body
        assert "not found" in (body.get("message") or "").lower()
