"""
Tenant context helper.

Resolves the *active* tenant for the current request. The active tenant is set
by the ``_rbac_resolve_tenant`` middleware in ``api/ragflow_server.py`` based
on the ``X-Workspace-Id`` header (or falls back to the user's personal tenant).

Use ``active_tenant_id()`` everywhere you need a tenant_id for data scoping —
NOT ``current_user.id``. Using ``current_user.id`` directly leaks data across
workspaces because, in the legacy single-tenant model, ``user.id == tenant.id``.

``active_tenant_id()`` raises 401 if no tenant could be resolved (e.g. unauth
request, or X-Workspace-Id pointing at a workspace the user is not a member of).
Routes that prefer to fail soft can use ``maybe_active_tenant_id()`` instead.
"""
from quart import g
from werkzeug.exceptions import Unauthorized


def maybe_active_tenant_id() -> str | None:
    """Return the active tenant id, or None if not resolved."""
    return getattr(g, "active_tenant_id", None) if g else None


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
