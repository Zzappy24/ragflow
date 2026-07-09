"""Mailer du panel — mock aiosmtplib, zéro réseau."""
import asyncio

import pytest

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
