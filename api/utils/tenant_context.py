"""
Tenant context helper.

Resolves the *active* tenant for the current request. Resolution is lazy —
the ``before_request`` middleware stashes the raw ``X-Workspace-Id`` header,
and the first call to ``active_tenant_id()`` performs the actual DB lookup
(at which point ``current_user`` is guaranteed to be authenticated by
``@login_required``).

Use ``active_tenant_id()`` everywhere you need a tenant_id for data scoping —
NOT ``current_user.id``. Using ``current_user.id`` directly leaks data across
workspaces because, in the legacy single-tenant model, ``user.id == tenant.id``.

``active_tenant_id()`` raises 401 if no tenant could be resolved (e.g. unauth
request, or X-Workspace-Id pointing at a workspace the user is not a member of).
Routes that prefer to fail soft can use ``maybe_active_tenant_id()`` instead.
"""
import logging

from quart import g
from werkzeug.exceptions import Unauthorized


def _resolve_tenant() -> str | None:
    """
    Perform the actual tenant resolution. Called once per request, on first
    access. Requires ``current_user`` to be authenticated.
    """
    from api.apps import current_user

    if not current_user or not hasattr(current_user, "id"):
        return None

    g.rbac_user_id = current_user.id

    ws_id = getattr(g, "_ws_header", None)
    if ws_id:
        from api.db.services.workspace_service import WorkspaceService, WsMemberService
        ok, ws = WorkspaceService.get_by_id(ws_id)
        if not ok or not ws or ws.status != "1":
            return None

        membership = WsMemberService.get_membership(ws_id, current_user.id)
        if membership:
            return ws.tenant_id

        if getattr(current_user, "is_superuser", False):
            return ws.tenant_id

        # Org admins of the workspace's parent org can access without an
        # explicit WsMember row.
        try:
            from api.db.services.org_service import OrgMemberService
            om = OrgMemberService.get_membership(ws.org_id, current_user.id)
            if om and om.role == "org_admin":
                return ws.tenant_id
        except Exception:
            pass

        return None  # not a member — deny

    # No workspace header — legacy personal tenant
    return current_user.id


def _ensure_resolved() -> None:
    """Resolve once per request, cache the result on ``g``."""
    if getattr(g, "_tenant_resolved", False):
        return
    try:
        g.active_tenant_id = _resolve_tenant()
    except Exception:
        logging.exception("tenant resolution failed")
        g.active_tenant_id = None
    g._tenant_resolved = True


def maybe_active_tenant_id() -> str | None:
    """Return the active tenant id, or None if not resolved."""
    if not g:
        return None
    _ensure_resolved()
    return g.active_tenant_id


def active_tenant_id() -> str:
    """
    Return the active tenant id, raising 401 if missing.

    Use this in route handlers where a tenant context is required for the
    operation to make sense (creating a KB, listing documents, etc.).
    """
    tid = maybe_active_tenant_id()
    if not tid:
        raise Unauthorized("No active workspace. Send X-Workspace-Id header.")
    return tid
