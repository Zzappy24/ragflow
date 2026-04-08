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

    @classmethod
    @DB.connection_context()
    def touch_last_used(cls, token):
        from datetime import datetime
        cls.model.update(last_used_at=datetime.utcnow()).where(
            cls.model.token == token
        ).execute()
