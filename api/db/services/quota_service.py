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


# ---------------------------------------------------------------------------
# Token quota
# ---------------------------------------------------------------------------

_QUOTA_CACHE_TTL = 300  # 5 minutes


def _redis_quota_cache_key(tenant_id: str, period_start: str) -> str:
    return f"quota_period:{tenant_id}:{period_start}"


def _get_period_tokens_from_mysql(tenant_id: str, period_start, period_end) -> int:
    """Sum tokens from TokenUsageDaily between period_start and period_end (inclusive)."""
    from api.db.db_models import DB, TokenUsageDaily
    from peewee import fn
    try:
        with DB.connection_context():
            result = (TokenUsageDaily
                      .select(fn.COALESCE(fn.SUM(TokenUsageDaily.tokens), 0))
                      .where(
                          (TokenUsageDaily.tenant_id == tenant_id) &
                          (TokenUsageDaily.date >= str(period_start)) &
                          (TokenUsageDaily.date <= str(period_end))
                      )
                      .scalar())
            return int(result or 0)
    except Exception:
        logging.exception("quota_service: failed to sum tokens from MySQL for tenant=%s", tenant_id)
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


def check_token_quota(tenant_id: str) -> dict:
    """
    Check token quota for a workspace tenant.

    Returns a dict:
        {
            "enabled": bool,          # False if no quota configured
            "quota_exceeded": bool,
            "allow_overage": bool,
            "current_usage": int,     # tokens used this period
            "limit": int,             # max_tokens_monthly (0 = unlimited)
            "period_start": str,
            "period_end": str,
        }
    """
    from api.db.db_models import Workspace, Organisation, DB

    # Default: permissive (no quota configured)
    _default = {
        "enabled": False,
        "quota_exceeded": False,
        "allow_overage": True,
        "current_usage": 0,
        "limit": 0,
        "period_start": None,
        "period_end": None,
    }

    try:
        with DB.connection_context():
            ws = Workspace.get_or_none(Workspace.tenant_id == tenant_id)
            if not ws:
                return _default
            org = Organisation.get_or_none(Organisation.id == ws.org_id)
            if not org:
                return _default
    except Exception:
        logging.exception("check_token_quota: DB lookup failed for tenant=%s", tenant_id)
        return _default

    # No quota configured
    if not org.max_tokens_monthly or org.max_tokens_monthly <= 0:
        return _default

    # No billing period — permissive fallback
    if not org.current_period_start or not org.current_period_end:
        return _default

    period_start = str(org.current_period_start)
    period_end = str(org.current_period_end)

    # Try Redis cache first
    cache_key = _redis_quota_cache_key(tenant_id, period_start)
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
        # Sum from MySQL (flushed tokens) + live Redis (today's unflushed)
        period_tokens = _get_period_tokens_from_mysql(tenant_id, period_start, period_end)
        live = _get_live_redis_tokens(tenant_id)
        period_tokens += live
        # Cache the result
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


def invalidate_quota_cache(tenant_id: str, period_start: str) -> None:
    """Invalidate the quota cache after a new period starts (call from billing system)."""
    try:
        from rag.utils.redis_conn import REDIS_CONN
        REDIS_CONN.REDIS.delete(_redis_quota_cache_key(tenant_id, period_start))
    except Exception:
        pass
