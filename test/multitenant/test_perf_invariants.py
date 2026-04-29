"""
Static invariant tests — verify that performance-critical code changes from our
fork haven't been silently reverted by an upstream merge.

These tests do NOT require a running server. They analyze source files directly.

Invariants tracked:
  1. np.vstack(batches) in embedding_model.py — O(n) vs upstream's O(n²) loop
  2. DOC_BULK_SIZE >= 64 and EMBEDDING_BATCH_SIZE >= 256 in settings.py
     (upstream defaults are 4 and 16 — restoring those is a 10x throughput regression)
  3. active_tenant_id() / maybe_active_tenant_id() used in key handlers
     (current_user.id leaks data across workspaces)
  4. Per-tenant DB lock key in task_service.py
  5. _embed_insert_pipelined + chunk_limiter semaphore in task_executor.py
  6. Infinity pool auto-sized from WORKER_MAX_TASKS (upstream hardcodes "4")
  7. Redis ConnectionPool sized from REDIS_POOL_SIZE (upstream creates per-call)

Run:
    uv run pytest test/multitenant/test_perf_invariants.py -v
"""
import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text()


# ---------------------------------------------------------------------------
# Embedding model — O(n) vstack vs O(n²) concatenate loop
# ---------------------------------------------------------------------------

class TestEmbeddingModelPerf:
    """np.vstack(batches) must be present — upstream uses np.concatenate in a loop."""

    def test_vstack_present(self):
        src = read("rag/llm/embedding_model.py")
        assert "np.vstack(batches)" in src or "numpy.vstack(batches)" in src, (
            "np.vstack(batches) not found in embedding_model.py. "
            "Upstream may have reverted to the O(n²) np.concatenate loop. "
            "Check the merge diff for rag/llm/embedding_model.py."
        )

    def test_no_concatenate_loop(self):
        src = read("rag/llm/embedding_model.py")
        # Detect the upstream anti-pattern: np.concatenate inside a loop over batches
        concatenate_in_loop = re.search(
            r"for\b.+\bbatch.+\n(?:.*\n){0,5}.*np\.concatenate",
            src,
        )
        assert not concatenate_in_loop, (
            "Found np.concatenate inside what looks like a batch loop. "
            "This is the O(n²) upstream pattern — ensure np.vstack(batches) is used instead."
        )


# ---------------------------------------------------------------------------
# Settings — bulk/batch size constants
# ---------------------------------------------------------------------------

class TestSettingsConstants:
    """Performance-tuned constants must remain in settings.

    Pin VALUES, not just names. Upstream's defaults (DOC_BULK_SIZE=4,
    EMBEDDING_BATCH_SIZE=16) are 10x slower for our scale. Catching a name-only
    presence is not enough — we need to detect a value revert too.
    """

    def _module_default(self, name: str) -> int:
        """Read the module-level default for a constant from settings.py."""
        src = read("common/settings.py")
        # Match `NAME: int = <number>` or `NAME = <number>` at module level.
        m = re.search(rf"^{re.escape(name)}\s*(?::\s*\w+)?\s*=\s*(\d+)", src, re.MULTILINE)
        assert m, f"Could not find module-level default for {name} in common/settings.py"
        return int(m.group(1))

    def test_doc_bulk_size_present(self):
        src = read("common/settings.py")
        assert "DOC_BULK_SIZE" in src, (
            "DOC_BULK_SIZE missing from common/settings.py. "
            "Upstream merge may have removed our bulk-insert tuning."
        )

    def test_doc_bulk_size_at_least_64(self):
        """Upstream default is 4 — our minimum is 64 to keep Infinity round-trips down."""
        value = self._module_default("DOC_BULK_SIZE")
        assert value >= 64, (
            f"DOC_BULK_SIZE={value} in common/settings.py is below our perf floor of 64. "
            "Upstream default is 4 — reverting to it makes ingestion 10x slower. "
            "Likely an upstream merge silently reverted the value."
        )

    def test_embedding_batch_size_present(self):
        src = read("common/settings.py")
        assert "EMBEDDING_BATCH_SIZE" in src, (
            "EMBEDDING_BATCH_SIZE missing from common/settings.py."
        )

    def test_embedding_batch_size_at_least_256(self):
        """Upstream default is 16 — our minimum is 256 to saturate vLLM/Blackwell GPUs."""
        value = self._module_default("EMBEDDING_BATCH_SIZE")
        assert value >= 256, (
            f"EMBEDDING_BATCH_SIZE={value} in common/settings.py is below our perf floor of 256. "
            "Upstream default is 16 — at that batch size, vLLM/Blackwell GPUs sit idle "
            "and embedding throughput drops 10x+."
        )


# ---------------------------------------------------------------------------
# Task executor — pipelined embed+insert
# ---------------------------------------------------------------------------

class TestTaskExecutorPipeline:
    """_embed_insert_pipelined must remain — it's our concurrent embed→insert optimization."""

    def test_pipelined_function_present(self):
        src = read("rag/svr/task_executor.py")
        assert "_embed_insert_pipelined" in src, (
            "_embed_insert_pipelined not found in task_executor.py. "
            "Upstream may have reverted to sequential embed→insert. "
            "This is a significant throughput regression for document ingestion."
        )

    def test_set_progress_throttle_present(self):
        src = read("rag/svr/task_executor.py")
        assert "set_progress" in src, (
            "set_progress not found in task_executor.py."
        )

    def test_chunk_limiter_semaphore_present(self):
        """The chunking concurrency limiter (asyncio.Semaphore) must remain.

        Without it, every task ingests in parallel and we OOM on the embedding
        models. Upstream periodically removes this when refactoring the task loop.
        """
        src = read("rag/svr/task_executor.py")
        assert "chunk_limiter" in src and "Semaphore" in src, (
            "chunk_limiter (asyncio.Semaphore) not found in task_executor.py. "
            "Without it, parallel chunking will OOM the embedding model server."
        )

    def test_pipelined_call_site_present(self):
        """_embed_insert_pipelined must actually be CALLED — not just defined."""
        src = read("rag/svr/task_executor.py")
        # Look for an `await _embed_insert_pipelined(` call (not just the def line)
        assert re.search(r"await\s+_embed_insert_pipelined\(", src), (
            "_embed_insert_pipelined is defined but never awaited in task_executor.py. "
            "An upstream refactor may have replaced the call site with sequential code."
        )


# ---------------------------------------------------------------------------
# Connection pool sizing — Infinity & Redis
# ---------------------------------------------------------------------------

class TestPoolSizing:
    """Connection pools must auto-size from env vars, not be hardcoded.

    Upstream patterns we explicitly reject:
      - Infinity pool size hardcoded to "4"
      - Redis connections created per-call without a ConnectionPool
    """

    def test_infinity_pool_uses_worker_max_tasks(self):
        src = read("common/doc_store/infinity_conn_pool.py")
        assert "WORKER_MAX_TASKS" in src, (
            "WORKER_MAX_TASKS env var not referenced in infinity_conn_pool.py. "
            "The Infinity pool size must auto-track task concurrency — without "
            "this, the pool exhausts under load and tasks block waiting for connections."
        )

    def test_infinity_pool_not_hardcoded_to_four(self):
        """The upstream antipattern is INFINITY_POOL_MAX_SIZE = 4 (or "4")."""
        src = read("common/doc_store/infinity_conn_pool.py")
        # Reject only the bare "4" assignment, not "4" appearing in comments or other constants.
        bad = re.search(
            r'INFINITY_POOL_MAX_SIZE\s*=\s*(?:["\']4["\']|4)\s*(?:#|$)',
            src,
            re.MULTILINE,
        )
        assert not bad, (
            "INFINITY_POOL_MAX_SIZE is hardcoded to 4 — that is the upstream "
            "default which causes connection-pool exhaustion under our load. "
            "Restore the WORKER_MAX_TASKS-based auto-sizing."
        )

    def test_redis_uses_connection_pool(self):
        src = read("rag/utils/redis_conn.py")
        assert "ConnectionPool" in src and "REDIS_POOL_SIZE" in src, (
            "redis_conn.py is missing ConnectionPool / REDIS_POOL_SIZE. "
            "Upstream creates a fresh redis.Redis() per call which exhausts "
            "Redis maxclients under our concurrency. Restore the connection pool."
        )


# ---------------------------------------------------------------------------
# Tenant isolation — active_tenant_id() in critical handlers
# ---------------------------------------------------------------------------

class TestTenantIsolationInHandlers:
    """
    Key document and dataset handlers must use active_tenant_id() for data scoping,
    NOT current_user.id. Using current_user.id leaks data across workspaces.

    kb_app.py and canvas_app.py were deleted in the upstream REST migration.
    The equivalent routes now live in api/apps/restful_apis/dataset_api.py and
    api/apps/restful_apis/document_api.py, which use add_tenant_id_to_kwargs
    (which calls maybe_active_tenant_id()) for workspace-aware tenant resolution.
    """

    def test_document_app_uses_active_tenant(self):
        src = read("api/apps/document_app.py")
        assert "active_tenant_id()" in src, (
            "active_tenant_id() not found in document_app.py. "
            "Data scoping may be broken — verify handlers use active_tenant_id(), not current_user.id."
        )

    def test_dataset_api_uses_active_tenant(self):
        """dataset_api.py (replacement for deleted kb_app.py) must use
        add_tenant_id_to_kwargs, which calls maybe_active_tenant_id() internally."""
        src = read("api/apps/restful_apis/dataset_api.py")
        assert "add_tenant_id_to_kwargs" in src, (
            "add_tenant_id_to_kwargs not found in dataset_api.py. "
            "Workspace tenant resolution may be broken — handlers must use "
            "add_tenant_id_to_kwargs (which calls maybe_active_tenant_id())."
        )
        # Also verify the underlying helper uses maybe_active_tenant_id
        utils_src = read("api/utils/api_utils.py")
        assert "maybe_active_tenant_id" in utils_src, (
            "maybe_active_tenant_id() not found in api_utils.py add_tenant_id_to_kwargs. "
            "Workspace-aware tenant resolution is broken."
        )

    def test_document_list_maps_run_status(self):
        """The RESTful list handler (document_api.py) or its service layer must
        contain the run_mapping dict (string enum conversion)."""
        # Primary location: api/apps/services/document_api_service.py
        service_src = read("api/apps/services/document_api_service.py")
        assert '"UNSTART"' in service_src and '"RUNNING"' in service_src, (
            "run_mapping strings not found in document_api_service.py. "
            "The /datasets/<id>/documents endpoint may be returning raw integer run values."
        )

    def test_document_list_maps_chunk_count(self):
        """The RESTful list handler or its service layer must alias
        chunk_num → chunk_count for the frontend."""
        service_src = read("api/apps/services/document_api_service.py")
        assert "chunk_count" in service_src, (
            "'chunk_count' alias not found in document_api_service.py. "
            "Frontend will show blank chunk column."
        )


# ---------------------------------------------------------------------------
# Per-tenant DB lock in task service
# ---------------------------------------------------------------------------

class TestTaskServiceLock:
    """Per-tenant lock key must be present to prevent cross-tenant task contention."""

    def test_per_tenant_lock_key(self):
        src = read("api/db/services/task_service.py")
        # Our custom lock key pattern: f"get_task:{tenant_id}"
        assert "get_task:" in src, (
            "Per-tenant lock key 'get_task:{tenant_id}' not found in task_service.py. "
            "Upstream may have removed our per-tenant DB lock, causing task contention."
        )
