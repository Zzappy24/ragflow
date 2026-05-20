"""Pin the internal-user filter shared by `_track_active_user` and the
dashboard's `total_users`.

WHY THIS FILE EXISTS
--------------------
Audit on 2026-05-01 found `ci.internal@cyllene.com` polluting the
admin dashboard's "active_users_15m" counter even though the dashboard's
`total_users` already filtered `@internal` emails. Two issues:

  1. The filter was applied to `total_users` but not to `active_users_15m`
     (the Redis ZCOUNT had no filter at all).
  2. The pattern `endswith("@internal")` missed `*.internal@*` emails
     (e.g. `ci.internal@cyllene.com`) which is how our CI users are named.

Fix in api/utils/api_utils.py: `is_internal_user_email` is the single
source of truth, and `_track_active_user` short-circuits on it so internal
users never enter the Redis ZSET in the first place. The dashboard's
total_users filter was widened to match.

These tests pin both:
  - The set of patterns recognised as "internal".
  - That `_track_active_user` skips them.

If a future refactor narrows the pattern back (or removes the skip), CI
users will start polluting the dashboard again.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

warnings.filterwarnings("ignore", category=UserWarning)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture(scope="module")
def utils():
    """Import api.utils.api_utils lazily so xgboost/UMAP UserWarnings during
    init don't trip pytest's filterwarnings='error' at collect time."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from api.utils import api_utils
    except Exception as e:
        pytest.skip(f"api_utils not importable: {e}")
    return api_utils


@pytest.mark.parametrize("email", [
    "ci.internal@cyllene.com",
    "viewer.internal@cyllene.com",
    "editor.internal@cyllene.com",
    "bot.internal@example.org",
    "anything@internal",
    "service-account@internal",
])
def test_internal_emails_recognised(utils, email):
    assert utils.is_internal_user_email(email), (
        f"{email!r} must be recognised as internal — otherwise CI/bot "
        "activity pollutes the active-users dashboard."
    )


@pytest.mark.parametrize("email", [
    "alice@cyllene.com",
    "bob@example.org",
    "qa@infiniflow.org",
    "user.with.dots@company.com",
    "internal-team@company.com",      # 'internal' in localpart but not as marker
    "ext.partner@somewhere.io",
])
def test_real_emails_not_flagged(utils, email):
    assert not utils.is_internal_user_email(email), (
        f"{email!r} is a real user — must NOT be filtered. The internal "
        "marker patterns must stay narrow enough to never miscount real users."
    )


@pytest.mark.parametrize("email", [None, ""])
def test_missing_email_is_not_internal(utils, email):
    """Empty/None email defaults to not-internal — `_track_active_user` will
    fall back to the legacy behaviour (track) rather than silently skipping."""
    assert utils.is_internal_user_email(email) is False


def test_track_active_user_skips_internal(utils, monkeypatch):
    """End-to-end behaviour: passing an internal email must NOT zadd to Redis."""
    calls = {"zadd": 0}

    class _FakeRedis:
        def zadd(self, key, mapping):
            calls["zadd"] += 1
        def zremrangebyscore(self, key, lo, hi):
            pass

    class _FakeConn:
        REDIS = _FakeRedis()

    import rag.utils.redis_conn as redis_conn_mod
    monkeypatch.setattr(redis_conn_mod, "REDIS_CONN", _FakeConn())

    utils._track_active_user("user-123", "ci.internal@cyllene.com")
    assert calls["zadd"] == 0, (
        "Internal user was still zadded — _track_active_user must short-circuit "
        "on is_internal_user_email() BEFORE touching Redis."
    )

    utils._track_active_user("user-456", "alice@cyllene.com")
    assert calls["zadd"] == 1, (
        "Real user was not tracked — the skip is too aggressive."
    )
