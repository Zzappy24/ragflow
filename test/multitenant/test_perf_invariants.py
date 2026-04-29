"""
Static invariant tests — verify that performance-critical code changes from our
fork haven't been silently reverted by an upstream merge.

These tests do NOT require a running server. They analyze source files directly.

Invariants tracked:
  1. np.vstack(batches) in embedding_model.py — O(n) vs upstream's O(n²) loop
  2. DOC_BULK_SIZE and EMBEDDING_BATCH_SIZE settings present in settings.py
  3. active_tenant_id() used in key handlers (not current_user.id for data scoping)
  4. Per-tenant DB lock key present in task_service.py
  5. _embed_insert_pipelined present in task_executor.py

Run:
    cd test/testcases
    uv run pytest test_multitenant/test_perf_invariants.py -v
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
    """Performance-tuned constants must remain in settings."""

    def test_doc_bulk_size_present(self):
        src = read("common/settings.py")
        assert "DOC_BULK_SIZE" in src, (
            "DOC_BULK_SIZE missing from common/settings.py. "
            "Upstream merge may have removed our bulk-insert tuning."
        )

    def test_embedding_batch_size_present(self):
        src = read("common/settings.py")
        assert "EMBEDDING_BATCH_SIZE" in src, (
            "EMBEDDING_BATCH_SIZE missing from common/settings.py."
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
