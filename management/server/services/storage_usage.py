"""
CIA-9 — Consommation stockage Infinity par tenant/KB, via l'endpoint interne
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

_CACHE: dict = {"ts": 0.0, "tables": None, "fail_ts": 0.0}
_CACHE_TTL_S = int(os.environ.get("MGMT_STORAGE_CACHE_TTL_S", "300"))
# 60 → 8 s (2026-09-02) : pendant l'incident image .16.3 (api pods morts),
# chaque affichage d'une fiche org bloquait des threads mgmt 60 s sur ce
# proxy → 504 gateway sur la fiche ET recherches gelées derrière (le pool
# de threads FastAPI faisait la queue). Le scan ES est rapide (~1-2 s) ;
# 8 s suffisent largement, et l'échec doit être RAPIDE : les stats
# dégradent proprement (infinity_bytes=None) par design.
_TIMEOUT_S = float(os.environ.get("MGMT_STORAGE_PROXY_TIMEOUT_S", "8"))
# Mémoire d'échec : sans elle, CHAQUE hit re-payait le timeout complet
# tant que l'api était injoignable.
_FAIL_TTL_S = float(os.environ.get("MGMT_STORAGE_FAIL_TTL_S", "45"))


def _fetch_tables() -> list[dict] | None:
    """Liste brute [{tenant_id, kb_id, rows, bytes}] ou None si indisponible."""
    now = time.time()
    if _CACHE["tables"] is not None and (now - _CACHE["ts"]) < _CACHE_TTL_S:
        return _CACHE["tables"]
    if (now - _CACHE["fail_ts"]) < _FAIL_TTL_S:
        return None  # échec récent : dégrader tout de suite, ne pas re-bloquer

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
        tables = (resp.json().get("data") or {}).get("tables", [])
    except Exception as e:
        logger.warning(f"storage_usage: mesure de l'index indisponible: {e}")
        _CACHE["fail_ts"] = time.time()
        return None

    _CACHE.update(ts=now, tables=tables)
    return tables


def get_infinity_usage_by_tenant() -> dict[str, dict] | None:
    """{tenant_id: {"bytes": int, "rows": int}} agrégé sur les KB, ou None."""
    tables = _fetch_tables()
    if tables is None:
        return None
    by_tenant: dict[str, dict] = {}
    for t in tables:
        agg = by_tenant.setdefault(t["tenant_id"], {"bytes": 0, "rows": 0})
        agg["bytes"] += int(t.get("bytes", 0))
        agg["rows"] += int(t.get("rows", 0))
    return by_tenant


def get_infinity_usage_by_kb() -> dict[str, dict] | None:
    """{kb_id: {"bytes": int, "rows": int}} — pour la ventilation par
    dataset/groupe. None si indisponible."""
    tables = _fetch_tables()
    if tables is None:
        return None
    by_kb: dict[str, dict] = {}
    for t in tables:
        agg = by_kb.setdefault(t["kb_id"], {"bytes": 0, "rows": 0})
        agg["bytes"] += int(t.get("bytes", 0))
        agg["rows"] += int(t.get("rows", 0))
    return by_kb
