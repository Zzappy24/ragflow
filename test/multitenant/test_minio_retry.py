"""
Regression test: rag/utils/minio_conn.py::RAGFlowMinio.get must retry on
transient failures.

WHY THIS TEST EXISTS
--------------------
Upstream's `get()` was a single-attempt fetch with a useless `for _ in range(1)`
loop — on the very first transient blip (network hiccup, post-PUT visibility
lag) it returned `None`, which propagated up to `naive.chunk` and surfaced as
the cryptic "Embedding extraction from file path is not supported." This
triggered intermittent ingestion failures that LOOKED like data corruption
but were really a 1-second timing issue.

Discovered 2026-04-30 while diagnosing why test_e2e_smoke::test_upload_parse_chunks
was sometimes red on the first run after backend restart but green on
subsequent runs (the so-called XPASS/strict story). The fix moved the loop
bound from `range(1)` → `range(3)` with exponential backoff.

If a future upstream merge brings back `range(1)` (or any non-retrying
variant), this test fails loudly and forces an audit before release.

Run:
    uv run python -m pytest test/multitenant/test_minio_retry.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# `rag.utils.minio_conn` and `common.settings` import each other at module
# load time, so importing minio_conn FIRST in our test triggers a partial-
# initialised circular import. Going through `common.settings` first lets
# Python finish both modules cleanly. Once that's done, the real classes
# are bound in `rag.utils.minio_conn`'s module dict and we can grab them.
import common.settings  # noqa: E402, F401
import rag.utils.minio_conn as _minio_conn  # noqa: E402

# `RAGFlowMinio` is decorated with `@singleton` (common/decorator.py), so the
# bare attribute is a factory function that returns the live singleton —
# unsuitable for a unit test (its __init__ tries to connect to a real MinIO).
# The original class is captured in the decorator's closure; pull it out so
# we can instantiate via `__new__` and inject a fake `conn`.
_factory = _minio_conn.RAGFlowMinio
_freevars = _factory.__code__.co_freevars
_RAGFlowMinioClass = _factory.__closure__[_freevars.index("cls")].cell_contents
assert isinstance(_RAGFlowMinioClass, type), (
    "Expected RAGFlowMinio's @singleton closure to yield the underlying class. "
    "If common/decorator.py::singleton was rewritten, update this extraction."
)


class _FailNTimesThenSucceed:
    """Stand-in for a MinIO client whose `get_object` fails N times then succeeds."""

    def __init__(self, fail_count: int, payload: bytes = b"PAYLOAD_OK"):
        self.fail_count = fail_count
        self.calls = 0
        self.payload = payload

    def get_object(self, bucket, filename):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise ConnectionError(f"transient minio error (#{self.calls})")
        m = MagicMock()
        m.read.return_value = self.payload
        return m


def _make_client(fail_count: int):
    """Build a RAGFlowMinio with `conn` swapped out for our fake."""
    obj = _RAGFlowMinioClass.__new__(_RAGFlowMinioClass)
    obj.conn = _FailNTimesThenSucceed(fail_count)
    obj.bucket = None         # @use_default_bucket reads this
    obj.prefix_path = None    # @use_prefix_path reads this
    obj.__open__ = lambda: None  # no-op reconnect
    return obj


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Patch `time.sleep` inside the minio module so retries don't actually
    block the test suite. Capture sleep durations for backoff tests."""
    sleeps: list[float] = []
    monkeypatch.setattr(_minio_conn.time, "sleep", lambda d: sleeps.append(d))
    yield sleeps


def test_get_succeeds_after_one_transient_failure(_no_real_sleep):
    """1 transient failure must NOT cause a None — that's the original bug."""
    client = _make_client(fail_count=1)
    blob = client.get("bkt", "file.txt")
    assert blob == b"PAYLOAD_OK", (
        f"Got {blob!r} after 1 transient failure — the retry loop is broken. "
        "If you just merged upstream, check rag/utils/minio_conn.py: it "
        "probably reverted to `for _ in range(1)`."
    )
    assert client.conn.calls == 2  # 1 failure + 1 retry


def test_get_succeeds_after_two_transient_failures(_no_real_sleep):
    """2 transient failures still recover — the loop must allow at least 3 attempts."""
    client = _make_client(fail_count=2)
    blob = client.get("bkt", "file.txt")
    assert blob == b"PAYLOAD_OK"
    assert client.conn.calls == 3


def test_get_returns_none_after_persistent_failure(_no_real_sleep):
    """After all retries exhausted, return None (don't crash). Also: don't
    retry forever — the loop must be bounded."""
    client = _make_client(fail_count=10)  # always fails
    blob = client.get("bkt", "file.txt")
    assert blob is None
    # Hard upper bound: must NOT retry indefinitely.
    assert client.conn.calls <= 5, (
        f"Made {client.conn.calls} attempts — retry loop is unbounded. "
        "Use `range(N)` with a fixed N (3 is reasonable)."
    )


def test_get_uses_non_decreasing_backoff(_no_real_sleep):
    """Backoff between retries must be non-decreasing. A revert to constant
    `time.sleep(1)` would let a bad MinIO instance get hammered. We check
    only monotonicity, not exact durations."""
    client = _make_client(fail_count=3)
    client.get("bkt", "file.txt")  # exhausts retries → None
    sleeps = _no_real_sleep
    assert len(sleeps) >= 2, (
        f"Only {len(sleeps)} sleep(s) recorded — the retry loop never iterated. "
        "Likely back to upstream's `for _ in range(1)`."
    )
    for prev, curr in zip(sleeps, sleeps[1:]):
        assert curr >= prev, (
            f"Sleep durations not non-decreasing: {sleeps}. "
            "Regression to constant sleep instead of backoff."
        )
