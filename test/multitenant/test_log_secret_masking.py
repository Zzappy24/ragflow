"""
Pin SecretMaskingFilter against credential leaks in logs.

Production logs from a multi-tenant SaaS will inevitably eat bearer tokens
and API keys (debug lines, exception traces, third-party libs that dump
headers). The filter must rewrite them before they ever reach a handler so
an ops engineer reading a log file does not get a working credential, and
so we don't end up with a GDPR/RGPD reportable disclosure event.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_log_secret_masking.py -v
"""
from __future__ import annotations

import logging
import sys
import warnings
from io import StringIO
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", category=UserWarning)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def captured_logger():
    """Build a logger with the SecretMaskingFilter wired the same way the
    production setup wires it (handler-level filter + logger-level filter),
    capturing output to an in-memory StringIO."""
    from common.log_utils import SecretMaskingFilter

    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    flt = SecretMaskingFilter()
    handler.addFilter(flt)

    logger = logging.getLogger("test_log_secret_masking")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.addFilter(flt)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    yield logger, buf

    logger.handlers.clear()
    logger.filters.clear()


# ---------------------------------------------------------------------------

class TestSecretMasking:
    def test_ragflow_api_key_masked(self, captured_logger):
        logger, buf = captured_logger
        logger.info("authenticated request with ragflow-abcdef0123456789ABCDEF99")
        out = buf.getvalue()
        assert "ragflow-abcdef0123456789ABCDEF99" not in out
        assert "ragflow-***" in out

    def test_jwt_masked(self, captured_logger):
        logger, buf = captured_logger
        # 3 segments, base64url, leading "ey".
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        logger.warning("token=%s", jwt)
        out = buf.getvalue()
        assert jwt not in out
        # The substitution leaves the marker pattern visible.
        assert "ey***" in out

    def test_openai_sk_key_masked(self, captured_logger):
        logger, buf = captured_logger
        logger.error("upstream rejected key sk-proj-abcdefghijklmnop1234567890")
        out = buf.getvalue()
        assert "sk-proj-abcdefghijklmnop1234567890" not in out
        assert "sk-***" in out

    def test_authorization_header_masked(self, captured_logger):
        logger, buf = captured_logger
        logger.debug("Authorization: Bearer abcdef0123456789ABCDEF99XYZ123")
        out = buf.getvalue()
        # The raw token must not survive.
        assert "abcdef0123456789ABCDEF99XYZ123" not in out

    def test_password_kv_masked(self, captured_logger):
        logger, buf = captured_logger
        logger.info("connecting with password='SuperSecret_99' to mysql")
        out = buf.getvalue()
        assert "SuperSecret_99" not in out
        assert "password=***" in out

    def test_args_masked(self, captured_logger):
        """Lazy %-formatting: secrets passed as args (not in the format
        string) must also be masked. This is the common case for
        ``logger.info("token=%s", token)``."""
        logger, buf = captured_logger
        logger.info("issued token: %s", "ragflow-XXXXXXXXXXXXXXXXXXXXXXXX")
        out = buf.getvalue()
        assert "ragflow-XXXXXXXXXXXXXXXXXXXXXXXX" not in out
        assert "ragflow-***" in out

    def test_innocent_message_unchanged(self, captured_logger):
        """Hot-path safety: messages without secrets must round-trip exactly
        so log volume + format stays predictable."""
        logger, buf = captured_logger
        logger.info("user 12345 created dataset 'analytics-q4'")
        out = buf.getvalue().strip()
        assert out == "user 12345 created dataset 'analytics-q4'"
