"""
Quota checking for organisation resources and token usage.

Lives under ``api/db/services/`` rather than ``api/apps/extensions/`` because
the management admin panel imports it from a non-Flask context. Any import
under ``api.apps.*`` triggers ``api/apps/__init__.py`` which calls
``settings.init_settings()`` and tries to connect to Elasticsearch — the admin
server is an identity/RBAC service and must not require a doc-store to boot.
"""
import logging


def check_quota(org_id: str, resource_type: str) -> tuple[bool, str]:
    """
    Check if an org has exceeded its quota for a resource type.
    Returns (ok, message). If ok is False, the message explains why.
    """
    from api.db.services.org_service import OrgService

    e, org = OrgService.get_by_id(org_id)
    if not e or not org:
        return True, ""  # legacy / no org

    counts = OrgService.get_resource_counts(org_id)
    limits = {
        "user": (counts.get("users", 0), org.max_users),
        "workspace": (counts.get("workspaces", 0), org.max_workspaces),
        "dataset": (counts.get("datasets", 0), org.max_datasets),
        "document": (counts.get("documents", 0), org.max_documents),
    }
    if resource_type in limits:
        current, maximum = limits[resource_type]
        if current >= maximum:
            return False, f"Quota exceeded: {resource_type} ({current}/{maximum})"
    return True, ""


def check_storage_quota(kb_tenant_id: str, incoming_bytes: int) -> tuple[bool, str]:
    """CIA-9 phase 2 — enforcement du plafond de stockage org à l'upload.

    Compare ``fichiers (SUM(document.size), temps réel) + index Infinity
    (dernier relevé connu, best-effort) + fichiers entrants`` au
    ``max_storage_gb`` de l'org.

    L'index est inclus en best-effort : on lit le cache du scan (rempli par
    l'endpoint interne, ~15 min de fraîcheur) sans JAMAIS déclencher de
    scan à l'upload (latence nulle). Pas de relevé disponible → on dégrade
    en fichiers-seul (jamais de blocage à tort sur une mesure absente).
    Tenant personnel (hors workspace) : pas de quota.
    """
    from api.db.services.workspace_service import WorkspaceService
    from api.db.services.org_service import OrgService

    ws = WorkspaceService.get_by_tenant_id(kb_tenant_id)
    if not ws:
        return True, ""  # tenant personnel — pas de quota org
    e, org = OrgService.get_by_id(ws.org_id)
    if not e or not org or not org.max_storage_gb:
        return True, ""
    max_bytes = int(org.max_storage_gb) * 1024 ** 3
    minio_bytes = OrgService.get_minio_storage_bytes(org.id)

    # Index Infinity : dernier relevé connu pour les tenants de l'org.
    # Import lazy — ce module est aussi importé par le mgmt-backend, qui ne
    # doit jamais toucher le SDK infinity (get_cached ne scanne pas).
    infinity_bytes = 0
    try:
        from api.db.db_models import Workspace
        from api.db.services.infinity_storage_scan import cached_bytes_for_tenants
        tenant_ids = [
            w.tenant_id for w in Workspace.select().where(
                (Workspace.org_id == org.id) & (Workspace.status == "1")
            )
        ]
        infinity_bytes = cached_bytes_for_tenants(tenant_ids) or 0
    except Exception:
        logging.warning("check_storage_quota: relevé Infinity indisponible", exc_info=True)

    if minio_bytes + infinity_bytes + int(incoming_bytes or 0) > max_bytes:
        used_gb = (minio_bytes + infinity_bytes) / 1024 ** 3
        return False, (
            f"Storage quota exceeded: {used_gb:.2f} GB used "
            f"(files + vector index) of {org.max_storage_gb} GB. Contact "
            f"your administrator to raise the organisation storage limit."
        )
    return True, ""


# ---------------------------------------------------------------------------
# Token quota
# ---------------------------------------------------------------------------

_QUOTA_CACHE_TTL = 300  # 5 minutes


def _redis_quota_cache_key(org_id: str, period_start: str) -> str:
    # Org-scoped: the limit is org-wide, so the cached usage must be too.
    # (Was tenant-scoped before 2026-07-13 — each workspace then compared its
    # OWN usage to the org limit, letting an org consume N x its quota.)
    return f"quota_period:org:{org_id}:{period_start}"


def _get_period_tokens_from_mysql(tenant_ids: list[str], period_start, period_end) -> int:
    """Sum tokens from TokenUsageDaily over the given tenants between
    period_start and period_end (inclusive)."""
    from api.db.db_models import DB, TokenUsageDaily
    from peewee import fn
    if not tenant_ids:
        return 0
    try:
        with DB.connection_context():
            result = (TokenUsageDaily
                      .select(fn.COALESCE(fn.SUM(TokenUsageDaily.tokens), 0))
                      .where(
                          (TokenUsageDaily.tenant_id.in_(tenant_ids)) &
                          (TokenUsageDaily.date >= str(period_start)) &
                          (TokenUsageDaily.date <= str(period_end))
                      )
                      .scalar())
            return int(result or 0)
    except Exception:
        logging.exception("quota_service: failed to sum tokens from MySQL for tenants=%s", tenant_ids)
        return 0


def _get_live_redis_tokens(tenant_id: str) -> int:
    """Get today's live (unflushed) token counter from Redis for this tenant."""
    from datetime import date
    today = date.today().isoformat()
    try:
        from rag.utils.redis_conn import REDIS_CONN
        pattern = f"token_usage:{today}:{tenant_id}:*"
        cursor = 0
        total = 0
        while True:
            cursor, keys = REDIS_CONN.REDIS.scan(cursor, match=pattern, count=100)
            for key in keys:
                val = REDIS_CONN.REDIS.get(key)
                total += int(val or 0)
            if cursor == 0:
                break
        return total
    except Exception:
        return 0


_DEFAULT_QUOTA = {
    "enabled": False,
    "quota_exceeded": False,
    "allow_overage": True,
    "current_usage": 0,
    "limit": 0,
    "period_start": None,
    "period_end": None,
}


def check_org_token_quota(org_id: str) -> dict:
    """Org-wide token quota status: usage summed over ALL the org's
    workspaces (any status — archived workspaces' past consumption still
    counts toward the billing period), compared to org.max_tokens_monthly.

    Returns:
        {
            "enabled": bool,          # False if no quota configured
            "quota_exceeded": bool,
            "allow_overage": bool,
            "current_usage": int,     # tokens used this period, ORG-WIDE
            "limit": int,             # max_tokens_monthly (0 = unlimited)
            "period_start": str,
            "period_end": str,
        }
    """
    from api.db.db_models import Workspace, Organisation, DB

    try:
        with DB.connection_context():
            org = Organisation.get_or_none(Organisation.id == org_id)
            if not org:
                return dict(_DEFAULT_QUOTA)
            tenant_ids = [w.tenant_id for w in Workspace.select(Workspace.tenant_id)
                          .where(Workspace.org_id == org_id)]
    except Exception:
        logging.exception("check_org_token_quota: DB lookup failed for org=%s", org_id)
        return dict(_DEFAULT_QUOTA)

    # No quota configured
    if not org.max_tokens_monthly or org.max_tokens_monthly <= 0:
        return dict(_DEFAULT_QUOTA)

    # No billing period — permissive fallback
    if not org.current_period_start or not org.current_period_end:
        return dict(_DEFAULT_QUOTA)

    period_start = str(org.current_period_start)
    period_end = str(org.current_period_end)

    # Try Redis cache first (org-scoped)
    cache_key = _redis_quota_cache_key(org_id, period_start)
    cached_usage = None
    try:
        from rag.utils.redis_conn import REDIS_CONN
        val = REDIS_CONN.REDIS.get(cache_key)
        if val is not None:
            cached_usage = int(val)
    except Exception:
        pass

    if cached_usage is not None:
        period_tokens = cached_usage
    else:
        # Sum from MySQL (flushed tokens) + live Redis (today's unflushed),
        # over every workspace tenant of the org.
        period_tokens = _get_period_tokens_from_mysql(tenant_ids, period_start, period_end)
        period_tokens += sum(_get_live_redis_tokens(t) for t in tenant_ids)
        try:
            from rag.utils.redis_conn import REDIS_CONN
            REDIS_CONN.REDIS.setex(cache_key, _QUOTA_CACHE_TTL, period_tokens)
        except Exception:
            pass

    quota_exceeded = period_tokens >= org.max_tokens_monthly

    return {
        "enabled": True,
        "quota_exceeded": quota_exceeded,
        "allow_overage": bool(org.allow_overage),
        "current_usage": period_tokens,
        "limit": org.max_tokens_monthly,
        "period_start": period_start,
        "period_end": period_end,
    }


def check_token_quota(tenant_id: str) -> dict:
    """Token quota status for the org owning this workspace tenant.

    ENFORCEMENT entry point (LLMBundle). The usage compared to the limit is
    the ORG-WIDE sum over all its workspaces — before 2026-07-13 it was the
    single workspace's usage, which let an org with N workspaces consume
    N x max_tokens_monthly."""
    from api.db.db_models import Workspace, DB

    try:
        with DB.connection_context():
            ws = Workspace.get_or_none(Workspace.tenant_id == tenant_id)
        if not ws:
            return dict(_DEFAULT_QUOTA)
    except Exception:
        logging.exception("check_token_quota: DB lookup failed for tenant=%s", tenant_id)
        return dict(_DEFAULT_QUOTA)
    return check_org_token_quota(ws.org_id)


def workspace_period_usage(tenant_id: str, period_start, period_end) -> int:
    """Per-workspace consumption for DISPLAY (panel breakdown). Enforcement
    uses the org-wide sum — never compare this figure to the org limit."""
    return (_get_period_tokens_from_mysql([tenant_id], period_start, period_end)
            + _get_live_redis_tokens(tenant_id))


def invalidate_org_quota_cache(org_id: str, period_start: str) -> None:
    """Invalidate the org's quota cache after a new period starts."""
    try:
        from rag.utils.redis_conn import REDIS_CONN
        REDIS_CONN.REDIS.delete(_redis_quota_cache_key(org_id, period_start))
    except Exception:
        pass
