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


def _scan_elasticsearch() -> list[dict]:
    """Équivalent ES du scan Infinity, VENTILÉ PAR KB.

    ES = un index `ragflow_<tenant_id>` par tenant : les octets exacts ne
    sont connus qu'au niveau index. Pour conserver la ventilation par
    dataset du panel admin (identique au scan Infinity), une agrégation
    `terms` sur `kb_id` (mappé keyword) donne les chunks par KB, et les
    octets de l'index sont attribués au prorata des chunks — approximation
    honnête, sommes par tenant exactes à l'arrondi près. Repli : entrée
    tenant-niveau (kb_id vide) si l'agrégation échoue."""
    from common import settings as common_settings
    # Timeout court dédié : le client ES global est configuré à 600 s — un
    # scan lancé pendant un burst d'ingestion campait sur un ES occupé, le
    # proxy mgmt attendait le scan, la gateway coupait (504 sur /stats,
    # constaté 2026-09-02). Une mesure de stockage est du best-effort : au
    # pire, relevé vide et jauges sans index jusqu'au prochain tick.
    es = common_settings.docStoreConn.es.options(request_timeout=15)
    stats = es.indices.stats(index="ragflow_*", metric="store,docs")
    tables = []
    for idx_name, s in (stats.get("indices") or {}).items():
        # `ragflow_<tenant>` = chunks ; `ragflow_doc_meta_<tenant>` = index de
        # métadonnées du même tenant. Avant (2026-09-07) le second était
        # attribué à un faux tenant "doc_meta_<id>" : ses octets sortaient du
        # total du workspace et polluaient la liste.
        m = re.match(r"^ragflow_(doc_meta_)?([0-9a-f]{32})$", idx_name)
        if not m:
            continue
        tenant_id = m.group(2)
        primaries = s.get("primaries") or {}
        total_rows = int(((primaries.get("docs") or {}).get("count")) or 0)
        total_bytes = int(((primaries.get("store") or {}).get("size_in_bytes")) or 0)
        if m.group(1):  # index de métadonnées : octets au tenant, pas de "lignes" (ce ne sont pas des chunks)
            tables.append({"tenant_id": tenant_id, "kb_id": "", "rows": 0, "bytes": total_bytes})
            continue

        buckets = []
        if total_rows > 0:
            try:
                agg = es.search(index=idx_name, size=0,
                                aggs={"kb": {"terms": {"field": "kb_id", "size": 10000}}})
                buckets = (((agg.get("aggregations") or {}).get("kb") or {}).get("buckets")) or []
            except Exception as e:
                logging.warning(f"storage scan ES: agrégation kb_id sur {idx_name}: {e}")

        if buckets:
            for b in buckets:
                kb_rows = int(b.get("doc_count") or 0)
                tables.append({
                    "tenant_id": tenant_id,
                    "kb_id": str(b.get("key") or ""),
                    "rows": kb_rows,
                    "bytes": int(total_bytes * kb_rows / total_rows),
                })
        else:
            tables.append({"tenant_id": tenant_id, "kb_id": "",
                           "rows": total_rows, "bytes": total_bytes})
    return tables


def scan() -> dict:
    """Scan bloquant — appeler depuis un thread. Les erreurs par table sont
    avalées (table en cours de drop, etc.). Dispatch selon DOC_ENGINE :
    la prod est passée sur Elasticsearch le 2026-08-29, le chemin Infinity
    levait KeyError('uri') faute de config → panel admin sans quotas et
    stacktrace toutes les 15 min (constaté 2026-09-01)."""
    from common import settings as common_settings

    if not getattr(common_settings, "DOC_ENGINE_INFINITY", False):
        engine = getattr(common_settings, "DOC_ENGINE", "")
        if engine != "elasticsearch":
            raise RuntimeError(f"storage scan: moteur {engine!r} non mesuré")
        try:
            tables = _scan_elasticsearch()
        except Exception as e:
            # Ne PAS mettre en cache un relevé vide : le panel afficherait
            # "0 Ko" pendant 2 h alors que la mesure est indisponible ("—").
            logging.warning(f"storage scan (elasticsearch): {e}")
            raise
        if not tables:
            logging.warning("storage scan (elasticsearch): aucun index ragflow_* vu par ce pod — vérifier ES_HOST / droits du compte")
        data = {
            "scanned_at": time.time(),
            "tables": tables,
            "total_bytes": sum(t["bytes"] for t in tables),
            "total_rows": sum(t["rows"] for t in tables),
        }
        _cache.update(ts=time.time(), data=data)
        _redis_put(data)
        return data

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
