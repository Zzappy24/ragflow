"""Mailer du panel — mock aiosmtplib, zéro réseau."""
import asyncio

import pytest

from test.multitenant.test_code_routes_rbac import _h, panel_client  # noqa: F401 — fixture import

pytestmark = pytest.mark.p1


def test_not_configured_returns_false(monkeypatch):
    from management.server.config import settings
    from management.server.services import mailer

    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert mailer.is_configured() is False
    assert asyncio.run(mailer.send_mail("a@b.c", "s", "b")) is False


def test_send_mail_success(monkeypatch):
    from management.server.config import settings
    from management.server.services import mailer

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    sent = {}

    class FakeSMTP:
        def __init__(self, **kw):
            sent["kw"] = kw

        async def connect(self):
            pass

        async def login(self, u, p):
            sent["login"] = (u, p)

        async def send_message(self, msg):
            sent["msg"] = msg

        async def quit(self):
            pass

    monkeypatch.setattr(mailer.aiosmtplib, "SMTP", FakeSMTP)
    ok = asyncio.run(mailer.send_mail("dev@client.fr", "Sujet", "Corps"))
    assert ok is True
    assert sent["msg"]["To"] == "dev@client.fr"
    assert "Sujet" in str(sent["msg"]["Subject"])


def test_send_mail_port_587_uses_starttls(monkeypatch):
    """Port 587 + TLS=true → STARTTLS (plaintext EHLO + upgrade), not implicit TLS."""
    from management.server.config import settings
    from management.server.services import mailer

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_TLS", True)
    sent = {}

    class FakeSMTP:
        def __init__(self, **kw):
            sent["kw"] = kw

        async def connect(self):
            pass

        async def login(self, u, p):
            pass

        async def send_message(self, msg):
            pass

        async def quit(self):
            pass

    monkeypatch.setattr(mailer.aiosmtplib, "SMTP", FakeSMTP)
    ok = asyncio.run(mailer.send_mail("a@b.c", "s", "b"))
    assert ok is True
    assert sent["kw"]["use_tls"] is False, "port 587 should not use implicit TLS"
    assert sent["kw"]["start_tls"] is True, "port 587 should use STARTTLS"


def test_send_mail_port_465_uses_implicit_tls(monkeypatch):
    """Port 465 + TLS=true → implicit TLS (immediate TLS handshake), not STARTTLS."""
    from management.server.config import settings
    from management.server.services import mailer

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    monkeypatch.setattr(settings, "SMTP_TLS", True)
    sent = {}

    class FakeSMTP:
        def __init__(self, **kw):
            sent["kw"] = kw

        async def connect(self):
            pass

        async def login(self, u, p):
            pass

        async def send_message(self, msg):
            pass

        async def quit(self):
            pass

    monkeypatch.setattr(mailer.aiosmtplib, "SMTP", FakeSMTP)
    ok = asyncio.run(mailer.send_mail("a@b.c", "s", "b"))
    assert ok is True
    assert sent["kw"]["use_tls"] is True, "port 465 should use implicit TLS"
    assert sent["kw"]["start_tls"] is False, "port 465 should not use STARTTLS"


def test_send_mail_failure_returns_false_never_raises(monkeypatch):
    from management.server.config import settings
    from management.server.services import mailer

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")

    class BoomSMTP:
        def __init__(self, **kw):
            pass

        async def connect(self):
            raise OSError("refused")

    monkeypatch.setattr(mailer.aiosmtplib, "SMTP", BoomSMTP)
    assert asyncio.run(mailer.send_mail("a@b.c", "s", "b")) is False


def test_post_users_sends_invite_email(panel_client, org_with_entitlement_and_users, monkeypatch):  # noqa: F811
    """POST /users wires the mailer (Task 1) into the invite flow (Task 2).

    The RAGFlow api-server invite/prepare call (httpx.AsyncClient, imported
    locally inside the route) is faked here so this test doesn't depend on
    a running api-server — mocked at the httpx module level, the cleanest
    seam since the route does `import httpx` inline rather than at module
    scope.
    """
    import uuid

    import httpx

    from management.server.routers import users as users_router

    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    sent = {}

    async def fake_send(to, subject, body_text):
        sent["to"] = to
        sent["body"] = body_text
        return True

    monkeypatch.setattr(users_router, "send_mail", fake_send, raising=False)

    class _FakeInviteResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {"code": "fake-invite-code"}}

    class _FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return _FakeInviteResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    # provision_user requires a default workspace in the org — org_with_entitlement_and_users
    # doesn't create one, so provision it here (provision_workspace provisions a real
    # RAGFlow tenant, cleaned up below via the workspace delete route).
    ws_resp = client.post(
        f"/api/admin/orgs/{org_id}/workspaces",
        json={"name": f"invite-test-ws-{uuid.uuid4().hex[:6]}"},
        headers=_h(tokens["org_admin"]),
    )
    assert ws_resp.status_code == 201, ws_resp.text
    ws_id = ws_resp.json()["id"]

    email = f"invite-{uuid.uuid4().hex[:8]}@client.fr"
    uid = None
    try:
        r = client.post(
            "/api/admin/users",
            json={"email": email, "nickname": "Test Invite", "org_id": org_id, "org_role": "member"},
            headers=_h(tokens["superuser"]),
        )
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["email_sent"] is True
        assert sent["to"] == email
        assert body["invite_url"] in sent["body"]
        uid = body["user_id"]
    finally:
        if uid:
            client.delete(f"/api/admin/users/{uid}", headers=_h(tokens["superuser"]))
        client.delete(f"/api/admin/orgs/{org_id}/workspaces/{ws_id}", headers=_h(tokens["org_admin"]))
