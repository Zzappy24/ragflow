import ast
import os

import pytest

from agent.sandbox.security_shared import DANGEROUS_IMPORTS, analyze_python_code

UPSTREAM = os.path.join(
    os.path.dirname(__file__), "..", "executor_manager", "services", "security.py"
)


def test_rejects_dangerous_import():
    ok, violations = analyze_python_code("import socket\ndef main():\n    return 1")
    assert not ok
    assert any("socket" in v for v in violations)


def test_rejects_dangerous_attribute_call():
    ok, violations = analyze_python_code("def main():\n    __import__('os')")
    assert not ok


def test_accepts_famat_recipe_shape():
    code = (
        "import famat_recipes as fr, json\n"
        "def main():\n"
        "    con = fr.load_url('http://minio:9000/x.csv')\n"
        "    return json.dumps(fr.drift(con, 'CORRECTION_X', 10), default=str)\n"
    )
    ok, violations = analyze_python_code(code)
    assert ok, violations


def test_syntax_error_is_unsafe():
    ok, violations = analyze_python_code("def main(:\n    pass")
    assert not ok


def test_dangerous_imports_match_upstream_executor_manager():
    """Garde anti-dérive : si upstream change sa liste noire, ce test casse."""
    tree = ast.parse(open(UPSTREAM, encoding="utf-8").read())
    upstream_sets = [
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "DANGEROUS_IMPORTS" for t in node.targets)
    ]
    assert upstream_sets, "DANGEROUS_IMPORTS introuvable dans le fichier upstream"
    assert DANGEROUS_IMPORTS == upstream_sets[0]
