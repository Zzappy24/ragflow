"""
CIA-9 — Scan du stockage Infinity par table ragflow_<tenant>_<kb>.

Module neutre (pas de dépendance Quart) pour être importable à la fois par
l'endpoint interne (api/apps/restful_apis/internal_api.py) et par
l'enforcement de quota (api/db/services/quota_service.py).

TOUS les imports lourds (pool Infinity) sont lazy, à l'intérieur des
fonctions : le mgmt-backend importe quota_service au boot, et il ne doit
jamais tenter de se connecter à Infinity (son image n'a pas le SDK ; et
INFINITY_CONN se connecte à l'import). Seul le process api-server exécute
réellement le scan.
"""
import logging
import re
import time

_TABLE_RE = re.compile(r"^ragflow_([0-9a-f]{32})_([0-9a-f]{32})$")
_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(B|KB|MB|GB|TB)?\s*$", re.IGNORECASE)
_SIZE_MULT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}

# Cache à deux niveaux : process (rapide) + Redis (partagé entre les
# replicas/workers de l'api — sans lui, l'upload tomberait souvent sur un
# process qui n'a jamais servi l'endpoint interne et l'enforcement
# dégraderait en fichiers-seul). Redis est best-effort : indisponible →
# cache process seul.
_cache: dict = {"ts": 0.0, "data": None}
_REDIS_KEY = "infinity_storage_scan:v1"
_REDIS_TTL_S = 2 * 3600


def _redis_put(data: dict) -> None:
    try:
        import json
        from rag.utils.redis_conn import REDIS_CONN
        REDIS_CONN.set_obj(_REDIS_KEY, data, _REDIS_TTL_S) if hasattr(REDIS_CONN, "set_obj") else REDIS_CONN.set(_REDIS_KEY, json.dumps(data), _REDIS_TTL_S)
    except Exception:
        logging.debug("infinity_storage_scan: Redis put failed", exc_info=True)


def _redis_get() -> dict | None:
    try:
        import json
        from rag.utils.redis_conn import REDIS_CONN
        raw = REDIS_CONN.get(_REDIS_KEY)
        if not raw:
            return None
        return raw if isinstance(raw, dict) else json.loads(raw)
    except Exception:
        logging.debug("infinity_storage_scan: Redis get failed", exc_info=True)
        return None


def parse_size_to_bytes(val) -> int:
    """Infinity renvoie les tailles de segments en strings ('1.50MB')."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    m = _SIZE_RE.match(str(val))
    if not m:
        return 0
    unit = (m.group(2) or "B").upper()
    return int(float(m.group(1)) * _SIZE_MULT[unit])


def scan() -> dict:
    """Scan bloquant (2 appels thrift par table) — appeler depuis un thread.
    Les erreurs par table sont avalées (table en cours de drop, etc.)."""
    from common import settings as common_settings
    from common.doc_store.infinity_conn_pool import INFINITY_CONN

    db_name = common_settings.INFINITY.get("db_name", "default_db") if hasattr(common_settings, "INFINITY") else "default_db"
    pool = INFINITY_CONN.get_conn_pool()
    inf_conn = pool.get_conn()
    tables = []
    try:
        db = inf_conn.get_database(db_name)
        names = db.list_tables().table_names or []
        for name in names:
            m = _TABLE_RE.match(name)
            if not m:
                continue
            entry = {"tenant_id": m.group(1), "kb_id": m.group(2), "rows": 0, "bytes": 0}
            try:
                info = db.show_table(name)
                entry["rows"] = int(getattr(info, "row_count", 0) or 0)
                segs = db.get_table(name).show_segments()  # polars DataFrame
                if segs is not None and "size" in segs.columns:
                    entry["bytes"] = sum(parse_size_to_bytes(s) for s in segs["size"].to_list())
            except Exception as e:
                logging.warning(f"storage scan: table {name} skipped: {e}")
            tables.append(entry)
    finally:
        try:
            pool.release_conn(inf_conn)
        except Exception:
            pass

    data = {
        "scanned_at": time.time(),
        "tables": tables,
        "total_bytes": sum(t["bytes"] for t in tables),
        "total_rows": sum(t["rows"] for t in tables),
    }
    _cache.update(ts=time.time(), data=data)
    _redis_put(data)
    return data


def get_cached(max_age_s: float | None = None) -> dict | None:
    """Dernier scan connu, sans jamais déclencher de scan (usage :
    enforcement à l'upload — best-effort, latence nulle). None si aucun
    scan n'a encore eu lieu ou si le cache est plus vieux que max_age_s."""
    data = _cache.get("data")
    if data is not None and (max_age_s is None or (time.time() - _cache["ts"]) <= max_age_s):
        return data
    # Fallback : relevé d'un autre process via Redis
    data = _redis_get()
    if data is None:
        return None
    if max_age_s is not None and (time.time() - float(data.get("scanned_at", 0))) > max_age_s:
        return None
    _cache.update(ts=float(data.get("scanned_at", time.time())), data=data)
    return data


def cached_bytes_for_tenants(tenant_ids: list[str], max_age_s: float | None = None) -> int | None:
    """Octets Infinity connus pour un ensemble de tenants, ou None si pas
    de relevé exploitable."""
    data = get_cached(max_age_s)
    if data is None:
        return None
    wanted = set(tenant_ids)
    return sum(t["bytes"] for t in data.get("tables", []) if t["tenant_id"] in wanted)
