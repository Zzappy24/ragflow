"""
Pin ApiKeyScopeService.touch_last_used against contention amplification.

Background: every API-authenticated request calls touch_last_used to update
the last_used_at column. Under burst load (50+ concurrent SDK clients
sharing the same key) the unconditional UPDATE turns into a write storm on
a single row. We added a 1-second debounce in the WHERE clause so only one
worker per second actually persists.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
    uv run python -m pytest test/multitenant/test_last_used_debounce.py -v
"""
from __future__ import annotations

import sys
import time
import uuid
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Avoid the warnings-as-errors trap when api.* imports trigger UserWarnings
# (xgboost / pkg_resources). Same workaround used in test_active_users_filter.
warnings.filterwarnings("ignore", category=UserWarning)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def scope_row(workspace_id):
    """Insert a throwaway ApiKeyScope row + matching APIToken (the FK
    invariant assumed by other code), yield its token, clean up."""
    from api.db.db_models import APIToken, ApiKeyScope, DB
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(workspace_id)
    assert ok, workspace_id

    token = "ragflow-debounce-test-" + uuid.uuid4().hex
    scope_id = uuid.uuid4().hex
    with DB.connection_context():
        APIToken.create(tenant_id=ws.tenant_id, token=token, source="none")
        ApiKeyScope.create(
            id=scope_id,
            token=token,
            workspace_id=workspace_id,
            permissions=["dataset.read"],
            name="debounce-test",
            created_by=ws.created_by,
            status="1",
        )

    yield token

    with DB.connection_context():
        try:
            ApiKeyScope.delete().where(ApiKeyScope.token == token).execute()
            APIToken.delete().where(APIToken.token == token).execute()
        except Exception:
            pass


class TestLastUsedDebounce:
    def test_first_touch_persists(self, scope_row):
        """A fresh row (last_used_at NULL) accepts the first touch."""
        from api.db.db_models import ApiKeyScope, DB
        from api.db.services.workspace_service import ApiKeyScopeService

        token = scope_row
        ApiKeyScopeService.touch_last_used(token)
        with DB.connection_context():
            row = ApiKeyScope.get(ApiKeyScope.token == token)
            assert row.last_used_at is not None

    def test_second_touch_within_debounce_skipped(self, scope_row):
        """Two touches within 1s — the second leaves the timestamp untouched."""
        from api.db.db_models import ApiKeyScope, DB
        from api.db.services.workspace_service import ApiKeyScopeService

        token = scope_row
        ApiKeyScopeService.touch_last_used(token)
        with DB.connection_context():
            t1 = ApiKeyScope.get(ApiKeyScope.token == token).last_used_at

        # Sleep way under the 1s debounce.
        time.sleep(0.1)
        ApiKeyScopeService.touch_last_used(token)
        with DB.connection_context():
            t2 = ApiKeyScope.get(ApiKeyScope.token == token).last_used_at

        assert t1 == t2, f"Second touch within debounce should be a no-op: {t1} -> {t2}"

    def test_touch_after_debounce_window_persists(self, scope_row):
        """Past the 1s window the touch lands."""
        from api.db.db_models import ApiKeyScope, DB
        from api.db.services.workspace_service import ApiKeyScopeService

        token = scope_row
        # Manually backdate so we don't have to actually wait.
        with DB.connection_context():
            ApiKeyScope.update(
                last_used_at=datetime.utcnow() - timedelta(seconds=5)
            ).where(ApiKeyScope.token == token).execute()
            t0 = ApiKeyScope.get(ApiKeyScope.token == token).last_used_at

        ApiKeyScopeService.touch_last_used(token)
        with DB.connection_context():
            t1 = ApiKeyScope.get(ApiKeyScope.token == token).last_used_at

        assert t1 > t0, f"Touch past debounce window should refresh: {t0} -> {t1}"
