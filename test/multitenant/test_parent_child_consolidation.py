"""Pin du fix parent-child retrieval (grep `CUSTOM B2B SaaS — parent-child`).

Incident mesuré (CRAG 2026-08-28) : le pipeline upstream rerankait/sélectionnait
sur les textes d'ENFANTS (fragments de ligne) puis résolvait les parents trop
tard → −7,6 points appariés. Le fix consolide enfants → parents AVANT le
scoring dans Dealer.retrieval via `_consolidate_children_to_moms` :
  - le top-k devient des parents DISTINCTS ;
  - le texte servi au reranker est celui du PARENT ;
  - le score moteur pré-rerank agrégé = MAX des enfants (pas la moyenne) ;
  - parent introuvable → repli sur les enfants (jamais de perte) ;
  - le filet caller-side `retrieval_by_children` agrège aussi en MAX.
"""

import asyncio

from common import settings  # noqa: F401 — casse les cycles d'import
from rag.nlp.search import Dealer


class _FakeStore:
    def __init__(self, moms):
        self.moms = moms
        self.get_calls = []

    def get(self, mid, idx, kb_ids):
        self.get_calls.append(mid)
        return self.moms.get(mid)


def _dealer(moms):
    d = Dealer.__new__(Dealer)  # sans __init__ (FulltextQueryer inutile ici)
    d.dataStore = _FakeStore(moms)
    return d


def _child(cid, mom_id, score, ltks, kwd=None):
    return {
        "mom_id": mom_id,
        "_score": score,
        "content_ltks": ltks,
        "content_with_weight": ltks,
        "important_kwd": kwd or [],
        "doc_id": "doc-x",
        "docnm_kwd": "doc-x.html",
        "kb_id": "kb1",
        "q_4_vec": [score] * 4,
    }


def _sres(ids, field):
    return Dealer.SearchResult(total=len(ids), ids=list(ids), field=dict(field),
                               query_vector=[0.0] * 4, highlight={})


class TestConsolidation:
    def _run(self, dealer, sres):
        return asyncio.run(dealer._consolidate_children_to_moms(sres, ["idx1"], ["kb1"]))

    def test_children_become_distinct_parents_with_max_score(self):
        moms = {"MA": {"content_with_weight": "parent A full text", "doc_id": "dA",
                       "docnm_kwd": "A.html", "kb_id": "kb1", "position_int": [[1] * 5]}}
        field = {
            "c1": _child("c1", "MA", 0.9, "frag un", ["k1"]),
            "c2": _child("c2", "MA", 0.4, "frag deux", ["k2"]),
            "s1": {**_child("s1", "", 0.7, "standalone"), "mom_id": ""},
        }
        out = self._run(_dealer(moms), _sres(["c1", "c2", "s1"], field))
        # 2 candidats : le parent MA (une seule fois) + le standalone
        assert out.ids == ["MA", "s1"]
        e = out.field["MA"]
        assert e["_score"] == 0.9, "agrégation MAX attendue (0.9), pas moyenne (0.65)"
        assert e["content_with_weight"] == "parent A full text", "le texte servi au scoring doit être celui du PARENT"
        assert e["content_ltks"], "content_ltks du parent requis pour le reranker/token-sim"
        assert not e.get("mom_id"), "le parent ne doit pas porter de mom_id (sinon re-résolution caller-side)"
        assert e["important_kwd"] == ["k1", "k2"], "mots-clés = union des enfants"
        assert e["q_4_vec"] == [0.9] * 4, "le vecteur vient du MEILLEUR enfant"
        assert e["doc_id"] == "dA" and out.total == 2

    def test_missing_parent_falls_back_to_children(self):
        field = {"c1": _child("c1", "GHOST", 0.8, "frag")}
        out = self._run(_dealer({}), _sres(["c1"], field))
        assert out.ids == ["c1"], "parent introuvable → les enfants restent candidats (zéro perte)"

    def test_no_children_is_a_noop(self):
        field = {"s1": {**_child("s1", "", 0.7, "txt"), "mom_id": ""}}
        sres = _sres(["s1"], field)
        out = self._run(_dealer({}), sres)
        assert out.ids == ["s1"] and out.field["s1"]["content_ltks"] == "txt"


class TestFallbackAggregationIsMax:
    def test_retrieval_by_children_uses_max(self):
        moms = {"MA": {"content_with_weight": "parent", "doc_id": "dA", "kb_id": "kb1"}}
        d = _dealer(moms)
        chunks = [
            {"mom_id": "MA", "similarity": 0.9, "content_ltks": "a", "kb_id": "kb1", "important_kwd": []},
            {"mom_id": "MA", "similarity": 0.1, "content_ltks": "b", "kb_id": "kb1", "important_kwd": []},
        ]
        out = d.retrieval_by_children(chunks, ["t1"])
        assert len(out) == 1
        assert out[0]["similarity"] == 0.9, "MAX attendu — la moyenne (0.5) diluait le signal du meilleur enfant"
