"""
api/apps/extensions/rbac.py
Module central RBAC -- roles + filtrage dataset par groupe.

Axe 1 (roles)  : "Que peut-il FAIRE ?"  -> has_permission() / @require_permission
Axe 2 (groupes) : "Quels datasets ?"    -> get_visible_dataset_ids() / filter_chat_dataset_ids()
"""
from enum import Enum
from functools import wraps

from api.utils.api_utils import get_json_result


# -- Roles -----------------------------------------------------------------

class OrgRole(str, Enum):
    ORG_ADMIN = "org_admin"
    MEMBER = "member"


class WsRole(str, Enum):
    WS_ADMIN = "ws_admin"
    EDITOR = "editor"
    VIEWER = "viewer"


# -- Permissions (axe 1) ---------------------------------------------------

class Permission(str, Enum):
    DATASET_CREATE = "dataset.create"
    DATASET_READ = "dataset.read"
    DATASET_UPDATE = "dataset.update"
    DATASET_DELETE = "dataset.delete"
    DOCUMENT_CREATE = "document.create"
    DOCUMENT_READ = "document.read"
    DOCUMENT_DELETE = "document.delete"
    CHAT_CREATE = "chat.create"
    CHAT_READ = "chat.read"
    CHAT_UPDATE = "chat.update"
    CHAT_DELETE = "chat.delete"
    CHAT_USE = "chat.use"
    AGENT_CREATE = "agent.create"
    AGENT_READ = "agent.read"
    AGENT_UPDATE = "agent.update"
    AGENT_DELETE = "agent.delete"
    MEMBER_INVITE = "member.invite"
    MEMBER_REMOVE = "member.remove"
    MEMBER_LIST = "member.list"
    GROUP_MANAGE = "group.manage"
    AUDIT_READ = "audit.read"
    # CUSTOM B2B SaaS – see CLAUDE.md "Custom B2B SaaS Multi-Tenant Layer"
    LLM_CONFIGURE = "llm.configure"
    DATASOURCE_CONFIGURE = "datasource.configure"
    MCP_CONFIGURE = "mcp.configure"


ROLE_PERMISSIONS = {
    WsRole.VIEWER: {
        Permission.DATASET_READ, Permission.DOCUMENT_READ,
        Permission.CHAT_READ, Permission.CHAT_USE, Permission.AGENT_READ,
    },
    WsRole.EDITOR: {
        Permission.DATASET_CREATE, Permission.DATASET_READ,
        Permission.DATASET_UPDATE, Permission.DATASET_DELETE,
        Permission.DOCUMENT_CREATE, Permission.DOCUMENT_READ,
        Permission.DOCUMENT_DELETE,
        Permission.CHAT_CREATE, Permission.CHAT_READ,
        Permission.CHAT_UPDATE, Permission.CHAT_DELETE, Permission.CHAT_USE,
        Permission.AGENT_CREATE, Permission.AGENT_READ,
        Permission.AGENT_UPDATE, Permission.AGENT_DELETE,
    },
    # CUSTOM B2B SaaS: LLM_CONFIGURE restricted to ws_admin only (not editor/viewer)
    WsRole.WS_ADMIN: set(Permission),
}


# -- Context resolution -----------------------------------------------------

def resolve_workspace_from_tenant(tenant_id: str):
    """Resolve workspace from a RAGFlow tenant_id."""
    from api.db.services.workspace_service import WorkspaceService
    return WorkspaceService.get_by_tenant_id(tenant_id)


def get_user_ws_role(user_id: str, workspace_id: str) -> WsRole | None:
    """Return the WS role, or None if not a member."""
    from api.db.services.workspace_service import WsMemberService
    member = WsMemberService.get_membership(workspace_id, user_id)
    if not member:
        return None
    return WsRole(member.role)


def get_user_org_role(user_id: str, org_id: str) -> OrgRole | None:
    """Return the Org role, or None if not a member."""
    from api.db.services.org_service import OrgMemberService
    member = OrgMemberService.get_membership(org_id, user_id)
    if not member:
        return None
    return OrgRole(member.role)


# -- Axe 1: has_permission ("what can they DO?") ----------------------------

def has_permission(user_id: str, tenant_id: str, permission: Permission) -> bool:
    """
    1. Super admin -> True
    2. No workspace found -> legacy mode (tenant_id == user_id)
    3. Org admin of the workspace's org -> True
    4. Otherwise -> check ws_member.role against the matrix
    """
    from api.db.services.user_service import UserService
    users = UserService.query(email=user_id) if "@" in str(user_id) else []
    user = None
    try:
        e, user = UserService.get_by_id(user_id)
        if not e:
            user = None
    except Exception:
        pass

    if user and user.is_superuser:
        return True

    workspace = resolve_workspace_from_tenant(tenant_id)
    if not workspace:
        return tenant_id == user_id  # legacy mode

    org_role = get_user_org_role(user_id, workspace.org_id)
    if org_role == OrgRole.ORG_ADMIN:
        return True

    ws_role = get_user_ws_role(user_id, workspace.id)
    if not ws_role:
        return False

    return permission in ROLE_PERMISSIONS.get(ws_role, set())


# -- Axe 2: get_visible_dataset_ids ("which datasets?") --------------------

def get_visible_dataset_ids(user_id: str, tenant_id: str) -> set[str] | None:
    """
    Return the set of visible dataset_ids, or None if no filter applies
    (admin, org_admin, user without groups, legacy mode).
    """
    from api.db.services.user_service import UserService
    try:
        e, user = UserService.get_by_id(user_id)
        if e and user and user.is_superuser:
            return None
    except Exception:
        pass

    workspace = resolve_workspace_from_tenant(tenant_id)
    if not workspace:
        return None  # legacy, no filter

    org_role = get_user_org_role(user_id, workspace.org_id)
    if org_role == OrgRole.ORG_ADMIN:
        return None

    ws_role = get_user_ws_role(user_id, workspace.id)
    if ws_role == WsRole.WS_ADMIN:
        return None

    from api.db.services.group_service import GroupService
    groups = GroupService.get_user_groups(workspace.id, user_id)

    if not groups:
        return None  # no groups = no filter (retrocompat)

    group_ids = [g.id for g in groups]
    return GroupService.get_datasets_for_groups(group_ids)


def assert_dataset_access(user_id: str, tenant_id: str, dataset_ids: list[str]):
    """
    Raise 403 if any dataset_id is not visible to the user.
    Used on creation operations (chat.create, agent.create).
    """
    visible = get_visible_dataset_ids(user_id, tenant_id)
    if visible is None:
        return
    forbidden = set(dataset_ids) - visible
    if forbidden:
        from werkzeug.exceptions import Forbidden
        raise Forbidden(f"Access denied to datasets: {forbidden}")


def filter_chat_dataset_ids(user_id: str, tenant_id: str, chat_dataset_ids: list[str]) -> list[str]:
    """
    Filter dataset_ids to only those visible to the user.
    Does NOT raise -- returns a subset.
    Used at RUNTIME (completions, retrieval) for cross-group agents.
    """
    visible = get_visible_dataset_ids(user_id, tenant_id)
    if visible is None:
        return chat_dataset_ids
    return [did for did in chat_dataset_ids if did in visible]


# -- Decorators -------------------------------------------------------------

def _extract_user_id(kwargs):
    """Extract user_id from current_user or kwargs."""
    try:
        from api.apps import current_user
        if current_user and hasattr(current_user, "id"):
            return current_user.id
    except Exception:
        pass
    return kwargs.get("user_id")


def _extract_tenant_id(kwargs):
    """Extract tenant_id from kwargs or from the lazy-resolved active tenant."""
    tenant_id = kwargs.get("tenant_id")
    if tenant_id:
        return tenant_id
    try:
        from api.utils.tenant_context import maybe_active_tenant_id
        return maybe_active_tenant_id()
    except Exception:
        return None


def require_permission(permission: Permission):
    """
    Use AFTER @login_required or @token_required.
    Checks role (axe 1). Dataset filtering (axe 2) is done in business logic.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            tenant_id = _extract_tenant_id(kwargs)
            user_id = _extract_user_id(kwargs)
            if not user_id or not tenant_id:
                return func(*args, **kwargs) if not _is_coroutine(func) else await func(*args, **kwargs)
            if not has_permission(user_id, tenant_id, permission):
                return get_json_result(
                    data=False, message=f"Permission denied: {permission.value}",
                    code=403
                )
            result = func(*args, **kwargs)
            import inspect
            if inspect.iscoroutine(result):
                return await result
            return result
        return wrapper
    return decorator


def require_org_admin(func):
    """Decorator for org admin routes."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        org_id = kwargs.get("org_id")
        user_id = _extract_user_id(kwargs)
        from api.db.services.user_service import UserService
        try:
            e, user = UserService.get_by_id(user_id)
            if e and user and user.is_superuser:
                result = func(*args, **kwargs)
                import inspect
                if inspect.iscoroutine(result):
                    return await result
                return result
        except Exception:
            pass
        role = get_user_org_role(user_id, org_id)
        if role != OrgRole.ORG_ADMIN:
            return get_json_result(data=False, message="Org admin required", code=403)
        result = func(*args, **kwargs)
        import inspect
        if inspect.iscoroutine(result):
            return await result
        return result
    return wrapper


def _is_coroutine(func):
    import inspect
    return inspect.iscoroutinefunction(func)
