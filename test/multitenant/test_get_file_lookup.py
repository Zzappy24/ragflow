"""CUSTOM B2B SaaS — épingle la résolution de nom du tool get_file.

Incident 2026-09-03 : le LLM appelle get_file avec « santé/export.xml »
(casse normalisée + chemin) alors que la base stocke le basename
« export.xml » sous un dossier « Santé » — l'ancien lookup exact renvoyait
« not found » à tort. Contrat : basename insensible à la casse, chemin
« dossier/fichier » compris, match de casse exact préféré, et aucun filtre
de confort ne peut produire un « not found » s'il reste un candidat.
"""

from types import SimpleNamespace

from agent.tools.get_file import _select_candidates


def f(name, parent="root"):
    return SimpleNamespace(name=name, parent_id=parent)


def parents(mapping):
    return lambda file: mapping.get(file.parent_id)


class TestSelectCandidates:
    def test_single_candidate_passes_through(self):
        files = [f("export.xml", "p1")]
        out = _select_candidates(files, "santé", "export.xml", parents({"p1": "Santé"}))
        assert out == files

    def test_folder_hint_filters_homonyms_case_insensitively(self):
        a, b = f("export.xml", "p1"), f("export.xml", "p2")
        out = _select_candidates(
            [a, b], "santé", "export.xml", parents({"p1": "Santé", "p2": "Archives"})
        )
        assert out == [a]

    def test_exact_case_preferred_over_ci_match(self):
        lower, upper = f("readme.md", "p1"), f("README.md", "p1")
        out = _select_candidates([lower, upper], "", "README.md", parents({}))
        assert out == [upper]

    def test_folder_filter_never_empties_result(self):
        # le dossier demandé ne matche aucun parent -> on garde les candidats
        # plutôt que de fabriquer un « not found »
        a = f("export.xml", "p1")
        out = _select_candidates([a], "inexistant", "export.xml", parents({"p1": "Santé"}))
        assert out == [a]

    def test_parent_resolution_failure_is_tolerated(self):
        a = f("export.xml", "p1")
        out = _select_candidates([a], "santé", "export.xml", lambda _: None)
        assert out == [a]

    def test_empty_input_stays_empty(self):
        assert _select_candidates([], "santé", "export.xml", parents({})) == []
