"""Pin du fix « Received multiple values for 'timeout' » (2026-08-31).

Le client elasticsearch-py 8.x fusionne les kwargs body-fields (timeout,
track_total_hits) DANS le dict body passé, en le mutant. La boucle de retry
d'ESConnection.search réutilisait le même objet query → toute 2e tentative
levait ValueError au lieu de retenter (le retry plantait précisément quand
on en avait besoin, sur les requêtes lentes). _es_search_once doit donc
passer une copie et purger ces clés à chaque tentative.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from common import settings  # noqa: F401,E402 — casse les cycles d'import
from rag.utils.es_conn import sanitize_search_body  # noqa: E402


class MutatingClient:
    """Simule es-py 8.x : fusionne les kwargs dans le body (mutation) et
    refuse les doublons body/param, comme _merge_body_fields_no_duplicates."""

    def __init__(self):
        self.calls = 0

    def search(self, index, body, timeout, track_total_hits):
        self.calls += 1
        if "timeout" in body or "track_total_hits" in body:
            raise ValueError(
                "Received multiple values for 'timeout', specify parameters "
                "using either body or parameters, not both."
            )
        # mutation à la es-py : les kwargs atterrissent dans le body passé
        body["timeout"] = timeout
        body["track_total_hits"] = track_total_hits
        return {"hits": {"hits": []}, "timed_out": False}


def _search(client, q):
    """Reproduit _es_search_once : sanitize puis appel client."""
    return client.search(index=["idx"], body=sanitize_search_body(q),
                         timeout="600s", track_total_hits=True)


def test_retry_with_same_query_object_does_not_raise():
    """Deux appels successifs avec LE MÊME dict (le scénario retry) doivent
    passer — l'ancien code levait ValueError au 2e appel."""
    client = MutatingClient()
    q = {"query": {"match_all": {}}, "size": 10}
    _search(client, q)
    _search(client, q)  # retry
    assert client.calls == 2


def test_poisoned_body_is_cleaned():
    """Même un body déjà pollué (vieux appelant, replay) est purgé avant envoi."""
    client = MutatingClient()
    q = {"query": {"match_all": {}}, "timeout": "600s", "track_total_hits": True}
    _search(client, q)
    assert client.calls == 1


def test_es_search_once_uses_sanitizer():
    """Pin AST : _es_search_once doit passer par sanitize_search_body."""
    import ast, os
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "rag/utils/es_conn.py")) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_es_search_once":
            assert "sanitize_search_body" in ast.dump(node), "_es_search_once n'utilise plus sanitize_search_body"
            return
    raise AssertionError("_es_search_once introuvable")
