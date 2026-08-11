"""
CIA-9 — Consommation stockage Infinity par tenant, via l'endpoint interne
de l'api-server (/api/v1/internal/storage/infinity).

L'image slim du mgmt-backend n'embarque pas le SDK infinity (numpy/pandas/
pyarrow) : la mesure vit dans l'api-server, on ne fait ici que l'appel
service-to-service (X-Internal-Secret, même pattern que le verify LLM de
routers/models.py) + un cache process de 5 min.

Toute erreur (api down, secret absent, timeout) retourne None : les stats
org affichent alors le stockage fichiers seul, jamais une 500.
"""
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict = {"ts": 0.0, "by_tenant": None}
_CACHE_TTL_S = int(os.environ.get("MGMT_STORAGE_CACHE_TTL_S", "300"))
# Le premier hit déclenche un scan complet côté api (2 appels thrift par
# table) — timeout large ; les hits suivants tapent le cache api (900 s).
_TIMEOUT_S = float(os.environ.get("MGMT_STORAGE_PROXY_TIMEOUT_S", "60"))


def get_infinity_usage_by_tenant() -> dict[str, dict] | None:
    """{tenant_id: {"bytes": int, "rows": int}} agrégé sur les KB, ou None
    si la mesure est indisponible."""
    now = time.time()
    if _CACHE["by_tenant"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL_S:
        return _CACHE["by_tenant"]

    api_base = os.environ.get("RAGFLOW_API_URL", "").rstrip("/")
    secret = os.environ.get("INTERNAL_API_SECRET", "")
    if not api_base or not secret:
        logger.warning("storage_usage: RAGFLOW_API_URL/INTERNAL_API_SECRET manquants")
        return None

    try:
        resp = httpx.get(
            f"{api_base}/api/v1/internal/storage/infinity",
            headers={"X-Internal-Secret": secret},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        payload = resp.json().get("data") or {}
    except Exception as e:
        logger.warning(f"storage_usage: mesure Infinity indisponible: {e}")
        return None

    by_tenant: dict[str, dict] = {}
    for t in payload.get("tables", []):
        agg = by_tenant.setdefault(t["tenant_id"], {"bytes": 0, "rows": 0})
        agg["bytes"] += int(t.get("bytes", 0))
        agg["rows"] += int(t.get("rows", 0))

    _CACHE.update(ts=now, by_tenant=by_tenant)
    return by_tenant
