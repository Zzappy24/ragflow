"""
Pin TaskService.get_task against double-claim races.

Background: two task_executor workers can read the same Redis stream
message during a consumer-group rebalance. Without a row-lock on the Task
row, both call TaskService.get_task(task_id) and both get a usable
``docs[0]`` dict — both then chunk + embed the same document, doubling
token spend and writing duplicate vectors.

Fix (api/db/services/task_service.py): wrap the SELECT in a
``DB.atomic()`` transaction with ``for_update(nowait=True)``. The first
caller acquires the row lock, the second hits a MySQL OperationalError
(error code 3572 NOWAIT or 1205 lock-wait timeout), which we swallow and
return None — the message stays in the stream for the first caller to
finish.

We test by holding the row lock from one thread and asserting the second
thread's get_task call returns None within the NOWAIT budget (no blocking
wait). Run:

    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_task_pickup_race.py -v
"""
from __future__ import annotations

import os
import sys
import threading
import time
import uuid
import warnings
from pathlib import Path

import pytest

# task_service imports deepdoc → xgboost → pkg_resources, which raises a
# UserWarning that pyproject's `filterwarnings = ["error"]` escalates into
# an ImportError. Suppress UserWarnings module-wide so the import chain
# completes; the rest of the suite already does this in
# test_active_users_filter.py for the same reason.
warnings.filterwarnings("ignore", category=UserWarning)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def task_row(workspace_id):
    """Create a minimal Task + Document + Knowledgebase + Tenant chain.

    Yields the task_id and cleans up afterwards. Uses the ws_admin's tenant
    so RBAC plumbing is happy.
    """
    from api.db.db_models import DB, Document, Knowledgebase, Task, Tenant
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(workspace_id)
    assert ok, f"Workspace {workspace_id} not found"
    tenant_id = ws.tenant_id

    task_id = uuid.uuid4().hex
    doc_id = uuid.uuid4().hex
    kb_id = uuid.uuid4().hex

    with DB.connection_context():
        # Tenant + workspace tenant rows already exist in the test DB; we
        # only need the Knowledgebase + Document + Task chain.
        # Tenant row should already exist for `tenant_id`. Verify.
        if not Tenant.select().where(Tenant.id == tenant_id).exists():
            pytest.skip(f"Tenant {tenant_id} not in DB — fixture pre-condition unmet")
        Knowledgebase.create(
            id=kb_id,
            tenant_id=tenant_id,
            name=f"task-race-test-{kb_id[:8]}",
            language="English",
            embd_id="nomic-embed-text@Ollama",
            permission="me",
            created_by=tenant_id,
        )
        Document.create(
            id=doc_id,
            kb_id=kb_id,
            name="task-race-test.txt",
            location="task-race-test.txt",
            type="doc",
            created_by=tenant_id,
            parser_id="naive",
            parser_config={},
            size=42,
        )
        Task.create(
            id=task_id,
            doc_id=doc_id,
            from_page=0,
            to_page=100000000,
            retry_count=0,
            progress=0.0,
            progress_msg="",
        )

    yield task_id, doc_id, kb_id, tenant_id

    with DB.connection_context():
        Task.delete().where(Task.id == task_id).execute()
        Document.delete().where(Document.id == doc_id).execute()
        Knowledgebase.delete().where(Knowledgebase.id == kb_id).execute()


# ---------------------------------------------------------------------------

class TestTaskPickupRace:
    def test_first_call_succeeds_returns_task(self, task_row):
        """Sanity: with no contention, get_task returns the task dict and
        increments retry_count exactly once."""
        from api.db.db_models import DB, Task
        from api.db.services.task_service import TaskService

        task_id, _, _, tenant_id = task_row
        result = TaskService.get_task(task_id, tenant_id=tenant_id)
        assert result is not None
        assert result["id"] == task_id

        with DB.connection_context():
            row = Task.get_by_id(task_id)
            assert row.retry_count == 1

    def test_second_concurrent_call_returns_none(self, task_row):
        """When one thread holds the row lock inside a transaction, a
        concurrent get_task call must return None (NOWAIT failure) instead
        of blocking or duplicating the claim."""
        from api.db.db_models import DB, Task
        from api.db.services.task_service import TaskService

        task_id, _, _, tenant_id = task_row

        # The "blocker" thread opens a transaction, takes the row lock with
        # SELECT … FOR UPDATE, then waits. The "victim" thread calls
        # get_task while the lock is held.
        lock_acquired = threading.Event()
        release_lock = threading.Event()
        victim_result: list = []

        def _holder():
            with DB.connection_context():
                with DB.atomic():
                    list(Task.select().where(Task.id == task_id).for_update().execute())
                    lock_acquired.set()
                    # Hold for up to 5s — plenty for the victim's NOWAIT to fail
                    release_lock.wait(timeout=5)

        def _victim():
            lock_acquired.wait(timeout=5)
            t0 = time.monotonic()
            victim_result.append(
                (TaskService.get_task(task_id, tenant_id=tenant_id), time.monotonic() - t0)
            )

        h = threading.Thread(target=_holder, daemon=True)
        v = threading.Thread(target=_victim, daemon=True)
        h.start()
        v.start()
        v.join(timeout=10)
        release_lock.set()
        h.join(timeout=5)

        assert victim_result, "Victim thread did not finish in time"
        result, dt = victim_result[0]
        # Must NOT block waiting for the lock — NOWAIT means immediate failure
        assert dt < 2.0, f"Victim took {dt:.2f}s; NOWAIT should fail in <2s"
        assert result is None, f"Victim should have got None (lock conflict), got: {result}"

        # And the holder thread didn't actually call update — retry_count
        # must still be 0 because no one finished get_task successfully.
        with DB.connection_context():
            row = Task.get_by_id(task_id)
            assert row.retry_count == 0
