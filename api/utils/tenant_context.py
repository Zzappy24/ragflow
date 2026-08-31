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

X-Workspace-Id is REQUIRED on every authenticated request. Every user belongs
to at least one workspace (the "général" default workspace created at org
onboarding). There is no personal-tenant fallback.

``active_tenant_id()`` raises 401 if no tenant could be resolved (missing
header, inactive workspace, or user not a member).
Routes that prefer to fail soft can use ``maybe_active_tenant_id()`` instead.
"""
import logging
import os
import time

from quart import g
from werkzeug.exceptions import Unauthorized

# CUSTOM B2B SaaS — cache de résolution workspace. La résolution complète
# coûte 2 à 4 requêtes MySQL (workspace, membership, éventuels bypass) sur
# CHAQUE appel API — c'est la latence plancher de toute la plateforme. On
# met en cache les résolutions POSITIVES uniquement ((ws_id, user_id) →
# tenant_id), TTL court : une révocation de membre ou une désactivation de
# workspace prend effet au pire en WORKSPACE_RESOLVE_CACHE_TTL_S secondes
# (défaut 30, 0 = cache désactivé). Les refus ne sont jamais cachés (un
# accès accordé prend effet immédiatement). Effet de bord assumé : les
# audits SUPERUSER_BYPASS / ORG_ADMIN_BYPASS sont enregistrés au plus une
# fois par TTL au lieu d'une fois par requête (moins de bruit d'audit).
WS_RESOLVE_CACHE_TTL_S = float(os.environ.get("WORKSPACE_RESOLVE_CACHE_TTL_S", "30"))
_WS_RESOLVE_CACHE_MAX = 4096
_ws_resolve_cache: dict[tuple[str, str], tuple[str, float]] = {}


def _ws_cache_get(ws_id: str, user_id: str) -> str | None:
    if WS_RESOLVE_CACHE_TTL_S <= 0:
        return None
    entry = _ws_resolve_cache.get((ws_id, user_id))
    if not entry:
        return None
    tenant_id, expires = entry
    if time.monotonic() >= expires:
        _ws_resolve_cache.pop((ws_id, user_id), None)
        return None
    return tenant_id


def _ws_cache_put(ws_id: str, user_id: str, tenant_id: str) -> None:
    if WS_RESOLVE_CACHE_TTL_S <= 0 or not tenant_id:
        return
    if len(_ws_resolve_cache) >= _WS_RESOLVE_CACHE_MAX:
        now = time.monotonic()
        expired = [k for k, (_, exp) in _ws_resolve_cache.items() if now >= exp]
        for k in expired:
            _ws_resolve_cache.pop(k, None)
        if len(_ws_resolve_cache) >= _WS_RESOLVE_CACHE_MAX:
            _ws_resolve_cache.clear()
    _ws_resolve_cache[(ws_id, user_id)] = (
        tenant_id, time.monotonic() + WS_RESOLVE_CACHE_TTL_S)


def invalidate_ws_resolve_cache(ws_id: str | None = None) -> None:
    """Purge le cache (tout, ou toutes les entrées d'un workspace donné).

    À appeler après une mutation de membership/statut si l'on veut un effet
    immédiat sur le pod courant ; les autres pods convergent au TTL.
    """
    if ws_id is None:
        _ws_resolve_cache.clear()
        return
    for k in [k for k in _ws_resolve_cache if k[0] == ws_id]:
        _ws_resolve_cache.pop(k, None)


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
        cached = _ws_cache_get(ws_id, current_user.id)
        if cached:
            return cached

        from api.db.services.workspace_service import WorkspaceService, WsMemberService
        ok, ws = WorkspaceService.get_by_id(ws_id)
        if not ok or not ws or ws.status != "1":
            return None

        membership = WsMemberService.get_membership(ws_id, current_user.id)
        if membership:
            _ws_cache_put(ws_id, current_user.id, ws.tenant_id)
            return ws.tenant_id

        if getattr(current_user, "is_superuser", False):
            logging.info(
                "RBAC superuser bypass: user=%s accessed workspace=%s tenant=%s",
                current_user.id, ws_id, ws.tenant_id,
            )
            try:
                from api.db.services.audit_service import AuditService
                AuditService.record(
                    workspace_id=ws_id,
                    user_id=current_user.id,
                    actor_email=getattr(current_user, "email", ""),
                    action="SUPERUSER_BYPASS",
                    resource_type="workspace",
                    resource_id=ws_id,
                )
            except Exception:
                pass
            _ws_cache_put(ws_id, current_user.id, ws.tenant_id)
            return ws.tenant_id

        # Org admins of the workspace's parent org can access without an
        # explicit WsMember row.
        try:
            from api.db.services.org_service import OrgMemberService
            om = OrgMemberService.get_membership(ws.org_id, current_user.id)
            if om and om.role == "org_admin":
                logging.info(
                    "RBAC org_admin bypass: user=%s org=%s accessed workspace=%s tenant=%s",
                    current_user.id, ws.org_id, ws_id, ws.tenant_id,
                )
                try:
                    from api.db.services.audit_service import AuditService
                    AuditService.record(
                        org_id=ws.org_id,
                        workspace_id=ws_id,
                        user_id=current_user.id,
                        actor_email=getattr(current_user, "email", ""),
                        action="ORG_ADMIN_BYPASS",
                        resource_type="workspace",
                        resource_id=ws_id,
                    )
                except Exception:
                    pass
                _ws_cache_put(ws_id, current_user.id, ws.tenant_id)
                return ws.tenant_id
        except Exception:
            pass

        return None  # not a member — deny

    # No workspace header — X-Workspace-Id is required in this B2B SaaS model.
    # Every user belongs to at least one workspace (default "général" workspace).
    return None


def _ensure_resolved() -> None:
    """Resolve once per request, cache the result on ``g``."""
    if getattr(g, "_tenant_resolved", False):
        return
    try:
        g.active_tenant_id = _resolve_tenant()
    except Exception:
        logging.exception("tenant resolution failed")
        g._tenant_resolved = True
        raise
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
