"""Pin du cache de résolution workspace (2026-08-31).

La résolution X-Workspace-Id → tenant_id coûte 2 à 4 requêtes MySQL par
appel API (latence plancher de toute la plateforme). Le cache mémorise les
résolutions POSITIVES uniquement, TTL court (défaut 30 s) : une révocation
prend effet au pire en un TTL, un accès accordé prend effet immédiatement
(les refus ne sont jamais cachés).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from api.utils import tenant_context as tc


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    monkeypatch.setattr(tc, "_ws_resolve_cache", {})
    monkeypatch.setattr(tc, "WS_RESOLVE_CACHE_TTL_S", 30.0)
    yield


def test_put_then_get_within_ttl():
    tc._ws_cache_put("ws1", "u1", "tenant1")
    assert tc._ws_cache_get("ws1", "u1") == "tenant1"


def test_get_miss_returns_none():
    assert tc._ws_cache_get("ws1", "u1") is None


def test_entry_expires_after_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tc.time, "monotonic", lambda: now[0])
    tc._ws_cache_put("ws1", "u1", "tenant1")
    now[0] += 29.9
    assert tc._ws_cache_get("ws1", "u1") == "tenant1"
    now[0] += 0.2  # au-delà du TTL
    assert tc._ws_cache_get("ws1", "u1") is None
    assert ("ws1", "u1") not in tc._ws_resolve_cache  # purgée à la lecture


def test_ttl_zero_disables_cache(monkeypatch):
    monkeypatch.setattr(tc, "WS_RESOLVE_CACHE_TTL_S", 0.0)
    tc._ws_cache_put("ws1", "u1", "tenant1")
    assert tc._ws_resolve_cache == {}
    assert tc._ws_cache_get("ws1", "u1") is None


def test_empty_tenant_never_cached():
    tc._ws_cache_put("ws1", "u1", "")
    assert tc._ws_resolve_cache == {}


def test_overflow_evicts_expired_first(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(tc, "_WS_RESOLVE_CACHE_MAX", 3)
    tc._ws_cache_put("ws1", "u1", "t1")
    tc._ws_cache_put("ws1", "u2", "t2")
    now[0] += 31  # les deux premières expirent
    tc._ws_cache_put("ws1", "u3", "t3")
    tc._ws_cache_put("ws1", "u4", "t4")  # déclenche la purge des expirées
    assert tc._ws_cache_get("ws1", "u3") == "t3"
    assert tc._ws_cache_get("ws1", "u4") == "t4"
    assert len(tc._ws_resolve_cache) == 2


def test_overflow_full_of_fresh_entries_clears_all(monkeypatch):
    monkeypatch.setattr(tc, "_WS_RESOLVE_CACHE_MAX", 2)
    tc._ws_cache_put("ws1", "u1", "t1")
    tc._ws_cache_put("ws1", "u2", "t2")
    tc._ws_cache_put("ws1", "u3", "t3")  # rien d'expiré → clear all puis insert
    assert tc._ws_cache_get("ws1", "u3") == "t3"
    assert len(tc._ws_resolve_cache) == 1


def test_invalidate_by_workspace():
    tc._ws_cache_put("ws1", "u1", "t1")
    tc._ws_cache_put("ws1", "u2", "t1")
    tc._ws_cache_put("ws2", "u1", "t2")
    tc.invalidate_ws_resolve_cache("ws1")
    assert tc._ws_cache_get("ws1", "u1") is None
    assert tc._ws_cache_get("ws1", "u2") is None
    assert tc._ws_cache_get("ws2", "u1") == "t2"


def test_invalidate_all():
    tc._ws_cache_put("ws1", "u1", "t1")
    tc._ws_cache_put("ws2", "u1", "t2")
    tc.invalidate_ws_resolve_cache()
    assert tc._ws_resolve_cache == {}


def test_resolve_tenant_consults_cache_before_db():
    """Pin AST : _resolve_tenant doit consulter _ws_cache_get AVANT les
    imports de services (donc avant tout lookup DB) et remplir le cache sur
    chaque chemin positif (membre, superuser, org_admin = 3 puts)."""
    import ast
    import inspect
    src = inspect.getsource(tc._resolve_tenant)
    tree = ast.parse(src)
    dump = ast.dump(tree)
    assert dump.count("'_ws_cache_get'") == 1, "_resolve_tenant ne consulte plus le cache"
    assert dump.count("'_ws_cache_put'") == 3, (
        "chaque chemin positif (membership, superuser, org_admin) doit remplir le cache"
    )
    get_pos = src.index("_ws_cache_get")
    import_pos = src.index("from api.db.services.workspace_service")
    assert get_pos < import_pos, "le cache doit être consulté avant le lookup DB"
