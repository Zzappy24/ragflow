"""
Pin the contract of common.doc_store.infinity_conn_base._retry_on_meta_contention.

This helper wraps Infinity metadata writes (CREATE/DROP TABLE/INDEX) so that
RocksDB "Resource busy" errors under concurrent CREATE don't surface to the
caller. The behaviour is load-bearing for parallel KB creation and stress
tests; if a refactor changes the retry budget, backoff curve, or the set of
exceptions that are retried, half the multitenant_http_api suite goes red.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_infinity_retry.py -v
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import warnings
from pathlib import Path
from unittest.mock import MagicMock

import pytest

warnings.filterwarnings("ignore", category=UserWarning)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# infinity_conn_base.py participates in a circular import chain via
# `common.settings -> rag.utils.es_conn -> rag.nlp.rag_tokenizer -> infinity`,
# and the heavy `infinity` SDK isn't installed in dev. We only need one
# module-level helper, so we mock the offending modules JUST FOR THE LOAD
# and RESTORE sys.modules afterwards — otherwise the MagicMock for
# `common.file_utils` / `common.settings` poisons the rest of the test
# session (every later test that touches `get_project_base_directory()`
# blows up because the mock returns another MagicMock as a path).
_HEAVY_DEPS = (
    "infinity",
    "infinity.common",
    "infinity.index",
    "infinity.errors",
    "infinity.rag_tokenizer",
    "pandas",
    "common.file_utils",
    "rag.nlp",
    "rag.nlp.rag_tokenizer",
    "common.settings",
    "common.doc_store.doc_store_base",
)
_saved = {m: sys.modules.get(m) for m in _HEAVY_DEPS}
for _m in _HEAVY_DEPS:
    sys.modules[_m] = MagicMock()
# DocStoreConnection is referenced as a base class — it must be a real type.
sys.modules["common.doc_store.doc_store_base"].DocStoreConnection = type(
    "DocStoreConnection", (), {}
)

try:
    _PATH = REPO / "common" / "doc_store" / "infinity_conn_base.py"
    _spec = importlib.util.spec_from_file_location("infinity_retry_under_test", _PATH)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _retry_on_meta_contention = _mod._retry_on_meta_contention
    _retry_on_txn_conflict = _mod._retry_on_txn_conflict
    _delete_lock = _mod._delete_lock
finally:
    # Restore sys.modules so we don't leak MagicMocks into later tests.
    for _m, _orig in _saved.items():
        if _orig is None:
            sys.modules.pop(_m, None)
        else:
            sys.modules[_m] = _orig


def _busy_exc(code: int = 9003, msg: str = "Resource busy") -> Exception:
    """Build an exception that mimics Infinity's "Resource busy" surface.

    Two shapes have been observed in the SDK: a plain Exception with
    ``args == (9003, "rocksdb::Transaction::Put ... Resource busy ...")`` and
    a richer InfinityException with ``error_code``. Both must be retried.
    """
    return Exception(
        (code, f"rocksdb::Transaction::Put key: db|1|next_table_id, value: 1, {msg}")
    )


class TestRetryOnMetaContention:
    def test_no_error_returns_immediately(self):

        op = MagicMock(return_value="ok")
        result = _retry_on_meta_contention("noop", op)
        assert result == "ok"
        assert op.call_count == 1

    def test_retries_until_success(self):

        op = MagicMock(side_effect=[_busy_exc(), _busy_exc(), "ok"])
        result = _retry_on_meta_contention(
            "create_table(t1)", op, base_delay_ms=1, max_attempts=5
        )
        assert result == "ok"
        assert op.call_count == 3

    def test_exhausts_after_max_attempts(self):

        op = MagicMock(side_effect=[_busy_exc()] * 10)
        with pytest.raises(Exception) as ei:
            _retry_on_meta_contention(
                "create_table(t2)", op, base_delay_ms=1, max_attempts=3
            )
        assert "Resource busy" in str(ei.value)
        # After the budget is exhausted, the LAST exception is re-raised.
        assert op.call_count == 3

    def test_non_contention_error_propagates_immediately(self):
        """Any exception that's not a "Resource busy" surface must bubble up
        on the FIRST call — retrying real bugs is worse than failing fast."""

        op = MagicMock(side_effect=[ValueError("not a contention error")])
        with pytest.raises(ValueError):
            _retry_on_meta_contention("create_table(t3)", op)
        assert op.call_count == 1

    def test_error_code_attribute_form_is_recognised(self):
        """Newer Infinity SDKs raise InfinityException with .error_code = 9003.
        The contention detector must accept that surface too."""

        class FakeInfinityException(Exception):
            def __init__(self):
                super().__init__("anything")
                self.error_code = 9003

        op = MagicMock(side_effect=[FakeInfinityException(), "ok"])
        result = _retry_on_meta_contention(
            "create_table(t4)", op, base_delay_ms=1, max_attempts=3
        )
        assert result == "ok"
        assert op.call_count == 2

    def test_logs_each_retry_at_info(self, caplog):
        """Every retry must leave a breadcrumb so ops can correlate
        contention spikes with KB-creation bursts in production."""

        op = MagicMock(side_effect=[_busy_exc(), _busy_exc(), "ok"])
        with caplog.at_level(logging.INFO):
            _retry_on_meta_contention(
                "create_index(q_vec_idx, t5)", op, base_delay_ms=1, max_attempts=5
            )
        retry_logs = [r for r in caplog.records if "meta contention" in r.message.lower()]
        assert len(retry_logs) == 2  # 2 retries before the 3rd attempt succeeded

    def test_exhaustion_logs_warning(self, caplog):

        op = MagicMock(side_effect=[_busy_exc()] * 10)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception):
                _retry_on_meta_contention(
                    "create_table(t6)", op, base_delay_ms=1, max_attempts=2
                )
        warns = [r for r in caplog.records if "exhausted" in r.message.lower()]
        assert len(warns) == 1


def _conflict_exc() -> Exception:
    """Mimic the surface of an Infinity transaction-conflict abort (livelock
    prod 2026-08-19: concurrent row-DELETEs on the same table)."""
    return Exception(
        "Transaction: 12779305 is conflicted, detailed info: NewTxn conflict "
        "reason: Delete: database: default_db, db_id: 1, table: ragflow_x_y, "
        "table_id: 13, deleted: 5228 vs. Delete: ..."
    )


class TestRetryOnTxnConflict:
    """Contract of the delete-conflict retry (CUSTOM B2B SaaS). Without it,
    two concurrent deletes of the same rows livelock the whole doc engine."""

    def test_conflict_is_retried_until_competitor_wins(self):
        # First attempt conflicts; the retry finds the rows already gone and
        # succeeds (delete is idempotent — 0 rows matched).
        op = MagicMock(side_effect=[_conflict_exc(), "ok"])
        result = _retry_on_txn_conflict("delete(t)", op, base_delay_ms=1)
        assert result == "ok"
        assert op.call_count == 2

    def test_budget_is_bounded_then_propagates(self):
        op = MagicMock(side_effect=[_conflict_exc()] * 10)
        with pytest.raises(Exception) as ei:
            _retry_on_txn_conflict("delete(t)", op, base_delay_ms=1, max_attempts=3)
        assert "conflicted" in str(ei.value)
        assert op.call_count == 3

    def test_non_conflict_error_propagates_immediately(self):
        op = MagicMock(side_effect=[ValueError("table not found")])
        with pytest.raises(ValueError):
            _retry_on_txn_conflict("delete(t)", op)
        assert op.call_count == 1

    def test_meta_contention_and_conflict_predicates_are_distinct(self):
        # A "Resource busy" must NOT be retried by the conflict helper — it
        # belongs to the metadata path with its own budget.
        op = MagicMock(side_effect=[_busy_exc()])
        with pytest.raises(Exception):
            _retry_on_txn_conflict("delete(t)", op)
        assert op.call_count == 1

    def test_delete_lock_is_stable_per_table(self):
        assert _delete_lock("ragflow_a_b") is _delete_lock("ragflow_a_b")
        lock = _delete_lock("ragflow_a_b")
        assert hasattr(lock, "acquire") and hasattr(lock, "release")
