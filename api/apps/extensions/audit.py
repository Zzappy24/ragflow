"""
api/apps/extensions/audit.py
Audit log decorator for route functions.
"""
from functools import wraps
import inspect


def audit_log(action: str, resource_type: str = None):
    """
    Decorator that records an audit event after the route executes.
    Audit logging never blocks the main operation.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if inspect.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
                if inspect.iscoroutine(result):
                    result = await result
            try:
                from api.db.services.audit_service import AuditService
                from api.apps.extensions.rbac import resolve_workspace_from_tenant, _extract_user_id
                from quart import request

                tenant_id = kwargs.get("tenant_id")
                ws = resolve_workspace_from_tenant(tenant_id) if tenant_id else None
                user_id = _extract_user_id(kwargs)

                AuditService.record(
                    org_id=ws.org_id if ws else None,
                    workspace_id=ws.id if ws else None,
                    user_id=user_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=kwargs.get("dataset_id") or kwargs.get("document_id"),
                    details={"method": request.method, "path": request.path},
                    ip_address=request.remote_addr,
                    user_agent=request.headers.get("User-Agent", "")[:512],
                )
            except Exception:
                pass  # audit must never block
            return result
        return wrapper
    return decorator
