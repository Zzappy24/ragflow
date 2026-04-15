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
               actor_email=None, action=None, status="success",
               resource_type=None, resource_id=None,
               details=None, diff=None,
               ip_address=None, user_agent=None):
        """Record an audit event. Never raises — audit must not block operations.

        Args:
            actor_email: Email of the actor at event time (survives user purge).
            status: "success" or "failure".
            diff: Dict with {"before": {...}, "after": {...}} for state changes.
        """
        try:
            cls.model.create(
                id=get_uuid(),
                org_id=org_id or "",
                workspace_id=workspace_id or "",
                user_id=user_id or "",
                actor_email=actor_email or "",
                action=action or "",
                status=status,
                resource_type=resource_type or "",
                resource_id=resource_id or "",
                details=details or {},
                diff=diff,
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
    def _apply_filters(cls, q, action=None, actor_email=None, resource_type=None,
                       status=None, date_from=None, date_to=None):
        """Apply common optional filters to a query."""
        if action:
            # Support prefix match: "USER_" matches USER_DELETE, USER_INVITE, etc.
            if action.endswith("_"):
                q = q.where(cls.model.action.startswith(action))
            else:
                q = q.where(cls.model.action == action)
        if actor_email:
            q = q.where(cls.model.actor_email.contains(actor_email))
        if resource_type:
            q = q.where(cls.model.resource_type == resource_type)
        if status:
            q = q.where(cls.model.status == status)
        if date_from:
            q = q.where(cls.model.create_time >= date_from)
        if date_to:
            q = q.where(cls.model.create_time <= date_to)
        return q

    @classmethod
    @DB.connection_context()
    def query_by_org(cls, org_id, page=1, page_size=50,
                     action=None, actor_email=None, resource_type=None,
                     status=None, date_from=None, date_to=None):
        q = cls.model.select().where(cls.model.org_id == org_id)
        q = cls._apply_filters(q, action=action, actor_email=actor_email,
                                resource_type=resource_type, status=status,
                                date_from=date_from, date_to=date_to)
        offset = (page - 1) * page_size
        return list(q.order_by(cls.model.create_time.desc()).offset(offset).limit(page_size))

    @classmethod
    @DB.connection_context()
    def query_by_workspace(cls, workspace_id, page=1, page_size=50,
                           action=None, actor_email=None, resource_type=None,
                           status=None, date_from=None, date_to=None):
        q = cls.model.select().where(cls.model.workspace_id == workspace_id)
        q = cls._apply_filters(q, action=action, actor_email=actor_email,
                                resource_type=resource_type, status=status,
                                date_from=date_from, date_to=date_to)
        offset = (page - 1) * page_size
        return list(q.order_by(cls.model.create_time.desc()).offset(offset).limit(page_size))

    @classmethod
    @DB.connection_context()
    def query_global(cls, page=1, page_size=50, org_id=None,
                     action=None, actor_email=None, resource_type=None,
                     status=None, date_from=None, date_to=None):
        q = cls.model.select()
        if org_id:
            q = q.where(cls.model.org_id == org_id)
        q = cls._apply_filters(q, action=action, actor_email=actor_email,
                                resource_type=resource_type, status=status,
                                date_from=date_from, date_to=date_to)
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
