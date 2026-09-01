"""Pin du support XML dans le chunker General (2026-09-01).

L'upload acceptait .xml (filename_type → DOC) mais naive.chunk n'avait
aucune branche XML → NotImplementedError au parsing. La branche ajoutée
aplatit le document en lignes « chemin/balise@attr: texte » puis découpe
comme du texte ; un XML invalide est indexé en texte brut.
"""
import ast
import os
import pathlib
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

REPO = pathlib.Path(__file__).resolve().parents[2]


def _load_flattener():
    src = (REPO / "rag" / "app" / "naive.py").read_text()
    ns = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_xml_to_flat_lines":
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<naive>", "exec"), ns)
            return ns["_xml_to_flat_lines"]
    raise AssertionError("_xml_to_flat_lines introuvable dans rag/app/naive.py")


def test_flattener_keeps_hierarchy_attrs_and_strips_namespaces():
    f = _load_flattener()
    out = f('<machines xmlns="urn:x"><machine id="M1"><temp unit="C">42.5</temp>'
            "<status>OK</status></machine></machines>")
    assert "machines/machine@id: M1" in out
    assert "machines/machine/temp@unit: C" in out
    assert "machines/machine/temp: 42.5" in out
    assert "machines/machine/status: OK" in out
    assert "urn:x" not in out


def test_flattener_invalid_xml_falls_back_to_raw_text():
    f = _load_flattener()
    assert f("pas du xml <<<") == "pas du xml <<<"
    assert f("") == ""


def test_flattener_keeps_tail_text():
    f = _load_flattener()
    out = f("<a><b>x</b>queue</a>")
    assert "a/b: x" in out and "a: queue" in out


def test_naive_chunk_has_xml_branch():
    """La branche .xml doit exister dans naive.chunk — sans elle, un XML
    uploadé (accepté par filename_type) plante au parsing."""
    src = (REPO / "rag" / "app" / "naive.py").read_text()
    assert r"\.xml$" in src, "branche .xml absente de naive.chunk"
    assert "_xml_to_flat_lines" in src


def test_storage_scan_dispatches_on_doc_engine():
    """scan() ne doit plus toucher le pool Infinity quand DOC_ENGINE n'est
    pas infinity (KeyError('uri') toutes les 15 min en prod ES, 2026-09-01) :
    le garde DOC_ENGINE_INFINITY doit précéder l'import du pool."""
    src = (REPO / "api" / "db" / "services" / "infinity_storage_scan.py").read_text()
    guard = src.index("DOC_ENGINE_INFINITY")
    pool_import = src.index("from common.doc_store.infinity_conn_pool import INFINITY_CONN",
                            src.index("def scan()"))
    assert guard < pool_import, "le garde moteur doit précéder l'import du pool Infinity"
    assert "_scan_elasticsearch" in src, "le scan ES (quotas sur ES) a disparu"
