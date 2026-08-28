"""Pin du seuil de taille minimale des enfants parent-child (PC v2).

Sans plancher, chaque fragment non-vide devenait un enfant — le split par
ligne produisait des enfants de quelques caractères : inutiles au retrieval
ET générateurs de postings dégénérés qui crashent Infinity (#3418).
`split_with_pattern` fusionne désormais les fragments jusqu'à
CHILD_MIN_CHARS (défaut 120 ; 0 = comportement legacy), la queue étant
absorbée par le dernier enfant. Grep `CUSTOM B2B SaaS — parent-child v2`.
"""

import os

from common import settings  # noqa: F401
from rag.nlp import split_with_pattern


def _run(content, min_chars):
    old = os.environ.get("CHILD_MIN_CHARS")
    os.environ["CHILD_MIN_CHARS"] = str(min_chars)
    try:
        return split_with_pattern({"docnm_kwd": "t"}, "\n", content, True)
    finally:
        if old is None:
            os.environ.pop("CHILD_MIN_CHARS", None)
        else:
            os.environ["CHILD_MIN_CHARS"] = old


class TestChildMinChars:
    def test_tiny_fragments_are_merged(self):
        content = "\n".join(["short line %02d" % i for i in range(20)])  # 20 lignes de ~13 car
        docs = _run(content, 120)
        assert 1 <= len(docs) < 10, f"fragments fusionnés attendus, obtenu {len(docs)} enfants"
        bodies = [d["content_with_weight"] for d in docs]
        # tous sauf le dernier atteignent le plancher
        assert all(len(b.strip()) >= 120 for b in bodies[:-1])
        # rien n'est perdu
        assert "short line 00" in bodies[0] and "short line 19" in bodies[-1]

    def test_zero_restores_legacy_per_fragment(self):
        content = "\n".join(["short line %02d" % i for i in range(20)])
        docs = _run(content, 0)
        assert len(docs) == 20, "CHILD_MIN_CHARS=0 doit restaurer le comportement historique"

    def test_tail_merges_into_previous_child(self):
        content = ("A" * 130) + "\n" + "tiny tail"
        docs = _run(content, 120)
        assert len(docs) == 1 or (len(docs) >= 1 and "tiny tail" in docs[-1]["content_with_weight"])
        joined = "".join(d["content_with_weight"] for d in docs)
        assert "tiny tail" in joined
