"""Pin du mode « DeepDOC (fast) » (2026-09-01).

DeepDOC complet sans l'auto-rotation des tableaux : l'orientation teste 4
angles avec une passe d'OCR chacun sur CHAQUE tableau — 70-90 % du temps de
tâche sur les manuels denses en tableaux (PDF Zabbix 2300 p. : Table
analysis jusqu'à 485 s/12 pages) et la cause des saturations d'arène GPU.
Même principe que paper_fast, mais branché proprement via le paramètre
pdf_cls de by_deepdoc au lieu de dupliquer chunk().
"""
import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
NAIVE = (REPO / "rag" / "app" / "naive.py").read_text()


def test_parsers_dict_has_fast_entry():
    tree = ast.parse(NAIVE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PARSERS" for t in node.targets):
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
            vals = [v.id for v in node.value.values if isinstance(v, ast.Name)]
            mapping = dict(zip(keys, vals))
            assert mapping.get("deepdoc (fast)") == "by_deepdoc_fast", mapping
            return
    raise AssertionError("dict PARSERS introuvable dans naive.py")


def test_by_deepdoc_fast_injects_fast_class():
    tree = ast.parse(NAIVE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "by_deepdoc_fast":
            dump = ast.dump(node)
            assert "PdfFastTables" in dump, "by_deepdoc_fast n'injecte plus PdfFastTables"
            assert "by_deepdoc" in dump, "by_deepdoc_fast ne délègue plus à by_deepdoc"
            return
    raise AssertionError("by_deepdoc_fast introuvable")


def test_fast_class_short_circuits_orientation():
    """L'override doit déclarer 0° directement (pas de boucle de rotations)."""
    tree = ast.parse(NAIVE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PdfFastTables":
            assert any(isinstance(b, ast.Name) and b.id == "Pdf" for b in node.bases), \
                "PdfFastTables doit hériter du Pdf de naive (callbacks de progression)"
            meth = next((m for m in node.body if isinstance(m, ast.FunctionDef)
                         and m.name == "_evaluate_table_orientation"), None)
            assert meth is not None, "override _evaluate_table_orientation absent"
            ret = next((s for s in meth.body if isinstance(s, ast.Return)), None)
            assert ret is not None and isinstance(ret.value, ast.Tuple)
            first = ret.value.elts[0]
            assert isinstance(first, ast.Constant) and first.value == 0, \
                "l'override doit renvoyer l'angle 0 directement"
            return
    raise AssertionError("classe PdfFastTables introuvable")


def test_front_exposes_fast_option():
    src = (REPO / "web" / "src" / "components" / "layout-recognize-form-field.tsx").read_text()
    assert "DeepDOC (fast)" in src, "option front « DeepDOC (fast) » absente du dropdown"
