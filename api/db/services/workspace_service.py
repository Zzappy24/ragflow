from api.db.db_models import DB, Workspace, WsMember, ApiKeyScope
from api.db.services.common_service import CommonService


class WorkspaceService(CommonService):
    model = Workspace

    @classmethod
    @DB.connection_context()
    def get_by_tenant_id(cls, tenant_id):
        return cls.model.get_or_none(
            (cls.model.tenant_id == tenant_id) & (cls.model.status == "1")
        )

    @classmethod
    @DB.connection_context()
    def list_by_org(cls, org_id, include_deleted: bool = False):
        q = cls.model.select().where(cls.model.org_id == org_id)
        if not include_deleted:
            q = q.where(cls.model.status == "1")
        return list(q)

    @classmethod
    @DB.connection_context()
    def get_default_workspace(cls, org_id):
        """Return the default (oldest active) workspace for an org, or None."""
        return (
            cls.model
            .select()
            .where((cls.model.org_id == org_id) & (cls.model.status == "1"))
            .order_by(cls.model.create_time.asc())
            .first()
        )


class WsMemberService(CommonService):
    model = WsMember

    @classmethod
    @DB.connection_context()
    def get_membership(cls, workspace_id, user_id):
        return cls.model.get_or_none(
            (cls.model.workspace_id == workspace_id) &
            (cls.model.user_id == user_id) &
            (cls.model.status == "1")
        )

    @classmethod
    @DB.connection_context()
    def list_by_workspace(cls, workspace_id):
        return list(
            cls.model.select()
            .where((cls.model.workspace_id == workspace_id) & (cls.model.status == "1"))
        )

    @classmethod
    @DB.connection_context()
    def list_workspaces_for_user(cls, user_id):
        """Return all workspace memberships for a user."""
        return list(
            cls.model.select()
            .where((cls.model.user_id == user_id) & (cls.model.status == "1"))
        )


class ApiKeyScopeService(CommonService):
    model = ApiKeyScope

    @classmethod
    @DB.connection_context()
    def get_by_token(cls, token):
        return cls.model.get_or_none(
            (cls.model.token == token) & (cls.model.status == "1")
        )

    @classmethod
    @DB.connection_context()
    def list_by_workspace(cls, workspace_id):
        return list(
            cls.model.select()
            .where((cls.model.workspace_id == workspace_id) & (cls.model.status == "1"))
        )

    # Drop UPDATEs more recent than this many seconds. Under burst load
    # (50+ concurrent SDK clients with the same key) this collapses ~99%
    # of the writes that would otherwise contend on the same row, while
    # keeping the displayed "last used" within ~1s of reality.
    _LAST_USED_DEBOUNCE_SECONDS = 1

    @classmethod
    @DB.connection_context()
    def touch_last_used(cls, token):
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        threshold = now - timedelta(seconds=cls._LAST_USED_DEBOUNCE_SECONDS)
        # Conditional UPDATE: skip when an even fresher timestamp is already
        # in the row (NULL counts as stale, so first-ever touch always wins).
        cls.model.update(last_used_at=now).where(
            (cls.model.token == token)
            & ((cls.model.last_used_at.is_null(True)) | (cls.model.last_used_at < threshold))
        ).execute()

    @classmethod
    @DB.connection_context()
    def list_by_creator(cls, workspace_id, user_id):
        """Per-user keys: only the rows the caller created in this workspace."""
        return list(
            cls.model.select()
            .where(
                (cls.model.workspace_id == workspace_id)
                & (cls.model.created_by == user_id)
                & (cls.model.status == "1")
            )
        )
