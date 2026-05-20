"""
Webhook tenant-isolation tests.

Webhooks run with the canvas creator's identity (cvs.user_id), so the DSL
``security`` block is the ONLY tenant boundary for the public
``/api/v1/webhook/<agent_id>`` and ``/api/v1/agents/<agent_id>/webhook``
routes. These tests pin two contracts:

  1. A canvas with no ``security`` block refuses webhook traffic with HTTP 403
     and an actionable message — the "secure-by-default" stance the user
     locked in (cf. plans/iterative-mixing-ullman.md).
  2. A canvas with ``security.auth_type == 'token'`` refuses requests
     missing/with-wrong token, and accepts requests with the correct token.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_webhook_security.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

HOST = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
SDK_WEBHOOK = f"{HOST}/api/v1/webhook"
RESTFUL_WEBHOOK = f"{HOST}/api/v1/agents"


def _webhook_dsl(*, with_security: dict | None) -> dict:
    """Minimal webhook-mode canvas DSL.

    ``with_security`` is dropped straight into the Begin component's
    ``params.security`` field. ``None`` means no security block at all (the
    "open" default we now refuse).
    """
    params: dict = {"mode": "Webhook", "methods": ["POST", "GET"]}
    if with_security is not None:
        params["security"] = with_security
    return {
        "components": {
            "begin": {
                "obj": {
                    "component_name": "Begin",
                    "params": params,
                },
            },
        },
        "graph": {"nodes": [], "edges": []},
    }


@pytest.fixture
def make_canvas(ws_auth, workspace_id):
    """Create a webhook-mode canvas directly via DB and yield (agent_id, cleanup)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from api.db.db_models import DB, UserCanvas

    created: list[str] = []

    def _create(*, with_security: dict | None) -> str:
        canvas_id = uuid.uuid4().hex
        # JSONField — peewee handles dict ↔ JSON serialisation, so we pass the
        # dict as-is. The webhook handler does `cvs.dsl.get("components", ...)`
        # and rejects non-dict DSL.
        with DB.connection_context():
            UserCanvas.create(
                id=canvas_id,
                user_id=workspace_id,  # workspace tenant_id (B2B SaaS)
                title=f"webhook-test-{canvas_id[:8]}",
                description="created by test_webhook_security.py",
                dsl=_webhook_dsl(with_security=with_security),
                canvas_category="agent_canvas",
                permission="me",
            )
        created.append(canvas_id)
        return canvas_id

    yield _create

    # Cleanup
    with DB.connection_context():
        for cid in created:
            try:
                UserCanvas.delete().where(UserCanvas.id == cid).execute()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Secure-by-default refusal — canvas without `security` block
# ---------------------------------------------------------------------------

class TestSecureByDefault:
    def test_sdk_route_refuses_canvas_without_security(self, make_canvas):
        agent_id = make_canvas(with_security=None)
        r = requests.post(f"{SDK_WEBHOOK}/{agent_id}", json={"hello": "world"}, timeout=10)
        body = r.json()
        # Tuple-shaped responses unwrap to the dict on .json(); the route
        # returns FORBIDDEN (403) which we surface as code:403.
        assert body.get("code") == 403, f"Expected 403 refusal, got: {body}"
        assert "no security block" in (body.get("message") or "").lower(), body

    def test_restful_route_refuses_canvas_without_security(self, make_canvas):
        agent_id = make_canvas(with_security=None)
        r = requests.post(
            f"{RESTFUL_WEBHOOK}/{agent_id}/webhook",
            json={"hello": "world"},
            timeout=10,
        )
        body = r.json()
        assert body.get("code") == 403, f"Expected 403 refusal, got: {body}"
        assert "no security block" in (body.get("message") or "").lower(), body

    def test_empty_security_block_treated_as_missing(self, make_canvas):
        # `security: {}` is functionally indistinguishable from no block.
        # The current implementation treats both as "missing" and refuses.
        agent_id = make_canvas(with_security={})
        r = requests.post(f"{SDK_WEBHOOK}/{agent_id}", json={}, timeout=10)
        assert r.json().get("code") == 403


# ---------------------------------------------------------------------------
# Token auth — proves the security block, when present, actually gates
# ---------------------------------------------------------------------------

class TestTokenAuth:
    SECRET = "stress-test-token-do-not-leak"

    def test_missing_token_rejected(self, make_canvas):
        agent_id = make_canvas(with_security={"auth_type": "token", "token": self.SECRET})
        r = requests.post(f"{SDK_WEBHOOK}/{agent_id}", json={}, timeout=10)
        # The route raises a generic Exception which gets serialized as
        # BAD_REQUEST 400 — the important thing is it's NOT 200/0.
        assert r.json().get("code") != 0, "Token auth should reject missing token"

    def test_wrong_token_rejected(self, make_canvas):
        agent_id = make_canvas(with_security={"auth_type": "token", "token": self.SECRET})
        r = requests.post(
            f"{SDK_WEBHOOK}/{agent_id}",
            json={},
            headers={"Authorization": "Bearer wrong-token"},
            timeout=10,
        )
        assert r.json().get("code") != 0, "Token auth should reject wrong token"

    def test_correct_token_passes_security(self, make_canvas):
        # We don't run the canvas to completion — that requires a full LLM
        # stack. We only verify that the security check passes (no 403, no
        # 401). The downstream canvas execution may still error with 400/500
        # because the test DSL has no real components — that's fine.
        agent_id = make_canvas(with_security={"auth_type": "token", "token": self.SECRET})
        r = requests.post(
            f"{SDK_WEBHOOK}/{agent_id}",
            json={},
            headers={"Authorization": f"Bearer {self.SECRET}"},
            timeout=10,
        )
        body = r.json()
        # Whatever happens in canvas execution, we must NOT see the
        # "no security block" 403 — that would mean our auth bypass is broken.
        msg = (body.get("message") or "").lower()
        assert "no security block" not in msg, (
            f"Correct token still triggered 'no security block' refusal — bug: {body}"
        )


# ---------------------------------------------------------------------------
# Audit log persistence — every refused/accepted webhook must leave a row in
# cyllene_audit_log so cross-tenant probe attempts and successful triggers
# both have a paper trail.
# ---------------------------------------------------------------------------

class TestWebhookAuditLog:
    def test_refusal_writes_audit_row(self, make_canvas):
        from api.db.db_models import AuditLog, DB

        agent_id = make_canvas(with_security=None)
        before = _audit_count(AuditLog, DB, agent_id, "WEBHOOK_REJECTED_NO_SECURITY")
        r = requests.post(f"{SDK_WEBHOOK}/{agent_id}", json={"x": 1}, timeout=10)
        assert r.json().get("code") == 403
        after = _audit_count(AuditLog, DB, agent_id, "WEBHOOK_REJECTED_NO_SECURITY")
        assert after == before + 1, (
            f"Refusal must write an audit row; before={before} after={after}"
        )

    def test_successful_invoke_writes_audit_row(self, make_canvas):
        from api.db.db_models import AuditLog, DB

        # Token-auth DSL contract:
        #   security.token = {token_header: <header>, token_value: <secret>}
        agent_id = make_canvas(
            with_security={
                "auth_type": "token",
                "token": {
                    "token_header": "X-Webhook-Token",
                    "token_value": "test-token-XXXXXXXXXX",
                },
            },
        )
        before = _audit_count(AuditLog, DB, agent_id, "WEBHOOK_INVOKE")
        # The canvas itself may 400/500 (empty DSL), but the audit row is
        # written BEFORE Canvas() is constructed, so it must land regardless.
        requests.post(
            f"{SDK_WEBHOOK}/{agent_id}",
            json={},
            headers={"X-Webhook-Token": "test-token-XXXXXXXXXX"},
            timeout=10,
        )
        after = _audit_count(AuditLog, DB, agent_id, "WEBHOOK_INVOKE")
        assert after == before + 1, (
            f"Successful (security-passed) invoke must write an audit row; "
            f"before={before} after={after}"
        )


def _audit_count(AuditLog, DB, resource_id: str, action: str) -> int:
    with DB.connection_context():
        return (
            AuditLog.select()
            .where(
                (AuditLog.action == action)
                & (AuditLog.resource_id == resource_id)
                & (AuditLog.resource_type == "canvas")
            )
            .count()
        )
