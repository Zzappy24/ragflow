from api.db.db_models import DB, AuditLog
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid
from common.time_utils import current_timestamp, datetime_format
from datetime import datetime


class AuditService(CommonService):
    model = AuditLog

    @classmethod
    @DB.connection_context()
    def record(cls, org_id=None, workspace_id=None, user_id=None,
               action=None, resource_type=None, resource_id=None,
               details=None, ip_address=None, user_agent=None):
        """Record an audit event. Never raises — audit must not block operations."""
        try:
            cls.model.create(
                id=get_uuid(),
                org_id=org_id or "",
                workspace_id=workspace_id or "",
                user_id=user_id or "",
                action=action or "",
                resource_type=resource_type or "",
                resource_id=resource_id or "",
                details=details or {},
                ip_address=ip_address or "",
                user_agent=(user_agent or "")[:512],
                create_time=current_timestamp(),
                create_date=datetime_format(datetime.now()),
                update_time=current_timestamp(),
                update_date=datetime_format(datetime.now()),
            )
        except Exception:
            pass

    @classmethod
    @DB.connection_context()
    def query_by_org(cls, org_id, page=1, page_size=50, user_id=None, action=None):
        q = cls.model.select().where(cls.model.org_id == org_id)
        if action:
            q = q.where(cls.model.action == action)
        if user_id:
            q = q.where(cls.model.user_id == user_id)
        offset = (page - 1) * page_size
        return list(q.order_by(cls.model.create_time.desc()).offset(offset).limit(page_size))

    @classmethod
    @DB.connection_context()
    def query_by_workspace(cls, workspace_id, page=1, page_size=50, user_id=None, action=None):
        q = cls.model.select().where(cls.model.workspace_id == workspace_id)
        if action:
            q = q.where(cls.model.action == action)
        if user_id:
            q = q.where(cls.model.user_id == user_id)
        offset = (page - 1) * page_size
        return list(q.order_by(cls.model.create_time.desc()).offset(offset).limit(page_size))

    @classmethod
    @DB.connection_context()
    def query_by_user(cls, user_id, limit=100, offset=0):
        return list(
            cls.model.select()
            .where(cls.model.user_id == user_id)
            .order_by(cls.model.create_time.desc())
            .offset(offset).limit(limit)
        )
