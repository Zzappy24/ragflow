"""Pin du fix des angles morts de `get_unfinished_docs` (incident 2026-08-28).

Sous stress (tempête max_user_connections + restarts d'executors), des
documents atterrissent dans des états hybrides que le filtre d'origine ne
ré-examinait JAMAIS — figés RUNNING pour toujours alors que leurs tâches
sont finies :
  * progress >= 1 avec run == RUNNING (144 docs observés en prod) ;
  * progress == -1 avec run == RUNNING (7 docs — la condition de re-sync
    des FAIL exige run == FAIL).

Le fix ajoute deux conditions au filtre (grep
`CUSTOM B2B SaaS — update_progress blind spots`). Ce test épingle leur
présence structurelle dans la requête : on inspecte l'AST de la méthode
pour vérifier que les deux comparaisons (progress >= 1 / == -1 combinées à
run == RUNNING) existent, sans dépendre du texte exact des commentaires.
"""

import ast
import inspect
import textwrap

from api.db.services.document_service import DocumentService


def _get_unfinished_docs_ast():
    src = textwrap.dedent(inspect.getsource(DocumentService.get_unfinished_docs))
    return ast.parse(src)


def _comparisons(tree):
    """Liste (attr, op, valeur_source) pour chaque comparaison sur cls.model.<attr>."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left = node.left
        if isinstance(left, ast.Attribute) and isinstance(left.value, ast.Attribute):
            attr = left.attr  # progress / run / ...
            op = type(node.ops[0]).__name__
            comp = ast.unparse(node.comparators[0])
            out.append((attr, op, comp))
    return out


class TestBlindspotConditionsPinned:
    def test_progress_ge_1_running_condition_present(self):
        comps = _comparisons(_get_unfinished_docs_ast())
        assert ("progress", "GtE", "1") in comps, (
            "condition (progress >= 1) absente de get_unfinished_docs — "
            "les docs 'finis mais run=RUNNING' redeviennent invisibles à jamais "
            "(incident 2026-08-28, 144 docs figés)"
        )

    def test_running_comparisons_cover_both_hybrid_states(self):
        comps = _comparisons(_get_unfinished_docs_ast())
        run_running = [c for c in comps if c[0] == "run" and "RUNNING" in c[2]]
        assert len(run_running) >= 2, (
            "moins de 2 comparaisons run == RUNNING dans le filtre — l'un des "
            "deux angles morts (progress>=1 ou progress==-1 avec run=RUNNING) "
            "n'est plus couvert"
        )

    def test_progress_minus_one_still_covered(self):
        comps = _comparisons(_get_unfinished_docs_ast())
        minus_one = [c for c in comps if c[0] == "progress" and c[1] == "Eq" and c[2] == "-1"]
        assert len(minus_one) >= 2, (
            "la double couverture progress == -1 (run FAIL re-sync + run RUNNING "
            "angle mort) n'est plus complète"
        )
