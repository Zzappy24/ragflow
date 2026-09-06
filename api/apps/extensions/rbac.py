"""
api/apps/extensions/rbac.py
Module central RBAC -- roles + filtrage dataset par groupe.

Axe 1 (roles)  : "Que peut-il FAIRE ?"  -> has_permission() / @require_permission
Axe 2 (groupes) : "Quels datasets ?"    -> get_visible_dataset_ids() / filter_chat_dataset_ids()
"""
import logging
from enum import Enum
from functools import wraps

from api.utils.api_utils import get_json_result, call_view


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
    API_KEY_MANAGE = "api_key.manage"


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

def _resolve_rbac_context(user_id: str, tenant_id: str) -> dict | None:
    """Single JOIN: User + Workspace (by tenant_id) + OrgMember + WsMember.

    CUSTOM PERF: replaces 4 sequential queries (UserService.get_by_id +
    WorkspaceService.get_by_tenant_id + OrgMember.get_membership +
    WsMember.get_membership) with one LEFT JOIN. ~25ms -> ~5-8ms measured.

    Returns a dict with keys: is_superuser, email, workspace_id, workspace_org_id,
    org_role (OrgRole|None), ws_role (WsRole|None). Returns None if user not found.
    """
    from api.db.db_models import User, Workspace, OrgMember, WsMember
    from peewee import JOIN

    row = (
        User.select(
            User.is_superuser, User.email,
            Workspace.id.alias("workspace_id"),
            Workspace.org_id.alias("workspace_org_id"),
            OrgMember.role.alias("org_role"),
            WsMember.role.alias("ws_role"),
        )
        .join(Workspace, JOIN.LEFT_OUTER, on=(Workspace.tenant_id == tenant_id))
        .join(
            OrgMember, JOIN.LEFT_OUTER,
            on=((OrgMember.org_id == Workspace.org_id) & (OrgMember.user_id == User.id)),
        )
        .join(
            WsMember, JOIN.LEFT_OUTER,
            on=((WsMember.workspace_id == Workspace.id) & (WsMember.user_id == User.id)),
        )
        .where(User.id == user_id)
        .dicts()
        .first()
    )
    if not row:
        return None
    return {
        "is_superuser": bool(row.get("is_superuser")),
        "email": row.get("email"),
        "workspace_id": row.get("workspace_id"),
        "workspace_org_id": row.get("workspace_org_id"),
        "org_role": OrgRole(row["org_role"]) if row.get("org_role") else None,
        "ws_role": WsRole(row["ws_role"]) if row.get("ws_role") else None,
    }


def _resolve_rbac_context_cached(user_id: str, tenant_id: str) -> dict | None:
    """Same as _resolve_rbac_context but cached on quart.g for the request lifetime.

    Outside a request context (cron, tests), bypasses the cache.
    """
    try:
        from quart import g, has_request_context
        in_req = has_request_context()
    except Exception:
        in_req = False
    if not in_req:
        return _resolve_rbac_context(user_id, tenant_id)
    cache = getattr(g, "_rbac_ctx", None)
    if cache is None:
        cache = {}
        g._rbac_ctx = cache
    key = (user_id, tenant_id)
    if key not in cache:
        cache[key] = _resolve_rbac_context(user_id, tenant_id)
    return cache[key]


def has_permission(user_id: str, tenant_id: str, permission: Permission) -> bool:
    """
    1. Super admin -> True
    2. No workspace found -> deny
    3. Org admin of the workspace's org -> True
    4. Otherwise -> check ws_member.role against the matrix

    CUSTOM PERF: backed by a single JOIN query and a request-scoped cache —
    repeated calls within the same request cost zero queries.
    """
    ctx = _resolve_rbac_context_cached(user_id, tenant_id)
    if ctx is None:
        return False

    if ctx["is_superuser"]:
        try:
            from api.db.services.audit_service import AuditService
            AuditService.record(
                user_id=user_id,
                actor_email=ctx["email"] or "",
                action="SUPERUSER_BYPASS",
                resource_type="tenant",
                resource_id=tenant_id,
            )
        except Exception:
            pass
        return True

    if ctx["workspace_id"] is None:
        logging.warning(
            "RBAC: no workspace for tenant_id=%s user_id=%s — access denied",
            tenant_id, user_id,
        )
        return False

    if ctx["org_role"] == OrgRole.ORG_ADMIN:
        return True

    ws_role = ctx["ws_role"]
    if ws_role is None:
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
        logging.warning("RBAC: no workspace for tenant_id=%s user_id=%s — returning empty visible set (deny-all)", tenant_id, user_id)
        return set()  # no workspace = deny all datasets (not None which would mean no filter)

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
    """Extract user_id from current_user, request context, or kwargs."""
    try:
        from api.apps import current_user
        if current_user and hasattr(current_user, "id"):
            return current_user.id
    except Exception:
        pass
    # login-token-as-API-key path: token_required stores the real user_id here
    try:
        from quart import g as _g
        uid = getattr(_g, "_rbac_user_id", None)
        if uid:
            return uid
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


def _is_superuser_without_workspace(user_id: str) -> bool:
    """Superuser bypass for routes hit without any workspace context.

    Mirror of the has_permission() bypass (including the SUPERUSER_BYPASS
    audit trail) for the no-tenant path — fail closed on any error.
    """
    try:
        from api.db.services.user_service import UserService
        e, user = UserService.get_by_id(user_id)
        if not (e and user and user.is_superuser):
            return False
        try:
            from api.db.services.audit_service import AuditService
            AuditService.record(
                user_id=user_id,
                actor_email=getattr(user, "email", "") or "",
                action="SUPERUSER_BYPASS",
                resource_type="tenant",
                resource_id="(no-workspace-context)",
            )
        except Exception:
            pass
        return True
    except Exception:
        return False


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
            if not user_id and not tenant_id:
                # No authentication context at all — fail closed.
                return get_json_result(
                    data=False, message=f"Permission denied: {permission.value}", code=403
                )
            # CUSTOM B2B SaaS — clé API scopée : la permission demandée doit
            # figurer dans celles frappées sur la clé, AVANT le rôle du
            # créateur (audit 2026-09-06 : les permissions de clé n'étaient
            # jamais lues, une clé « dataset.read » supprimait des bases).
            try:
                from quart import g as _g
                scoped = getattr(_g, "api_key_permissions", None)
            except Exception:
                scoped = None
            if scoped is not None and permission.value not in scoped:
                return get_json_result(
                    data=False, message=f"Permission denied by API key scope: {permission.value}", code=403
                )
            if not user_id:
                # Pure API key caller: tenant_id is set but no individual user identity.
                # API keys are workspace-level service tokens — role RBAC does not apply.
                # Login-token callers always have user_id set (stored in g._rbac_user_id).
                return await call_view(func, *args, **kwargs)
            if not tenant_id:
                # Authenticated user but no workspace context. Superusers keep
                # their global bypass here too (admin scripts / CLI without
                # X-Workspace-Id) — before this check, the bypass only lived in
                # has_permission(), unreachable without tenant_id, so a
                # superuser got 403 on every @require_permission route unless
                # a workspace header was set (bug découvert chantier get_file,
                # 2026-08-18). Audité comme le bypass nominal. Tout autre
                # utilisateur sans workspace : fail closed, comme avant.
                if _is_superuser_without_workspace(user_id):
                    return await call_view(func, *args, **kwargs)
                return get_json_result(
                    data=False, message=f"Permission denied: {permission.value}", code=403
                )
            if not has_permission(user_id, tenant_id, permission):
                return get_json_result(
                    data=False, message=f"Permission denied: {permission.value}",
                    code=403
                )
            return await call_view(func, *args, **kwargs)
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
                return await call_view(func, *args, **kwargs)
        except Exception:
            pass
        role = get_user_org_role(user_id, org_id)
        if role != OrgRole.ORG_ADMIN:
            return get_json_result(data=False, message="Org admin required", code=403)
        return await call_view(func, *args, **kwargs)
    return wrapper


def _is_coroutine(func):
    import inspect
    return inspect.iscoroutinefunction(func)
