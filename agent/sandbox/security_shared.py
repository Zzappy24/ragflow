#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Analyse AST de sécurité — copie autonome de
executor_manager/services/security.py (non importable hors de son runtime :
dépendances core.logger / models.enums, et hors du contexte de build de son
image Docker). Le test test_dangerous_imports_match_upstream_executor_manager
casse si la copie dérive de l'upstream. Fichier upstream INTOUCHÉ.
"""
import ast
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


class SecurePythonAnalyzer(ast.NodeVisitor):
    """
    An AST-based analyzer for detecting unsafe Python code patterns.
    """

    DANGEROUS_IMPORTS = {"os", "subprocess", "sys", "shutil", "socket", "ctypes", "pickle", "threading", "multiprocessing", "asyncio", "http.client", "ftplib", "telnetlib", "builtins"}

    DANGEROUS_CALLS = {
        "eval",
        "exec",
        "open",
        "__import__",
        "compile",
        "input",
        "system",
        "popen",
        "remove",
        "rename",
        "rmdir",
        "chdir",
        "chmod",
        "chown",
        "getattr",
        "setattr",
        "globals",
        "locals",
        "shutil.rmtree",
        "subprocess.call",
        "subprocess.Popen",
        "ctypes",
        "pickle.load",
        "pickle.loads",
        "pickle.dump",
        "pickle.dumps",
    }

    def __init__(self):
        self.unsafe_items: List[Tuple[str, int]] = []

    def visit_Import(self, node: ast.Import):
        """Check for dangerous imports."""
        for alias in node.names:
            if alias.name.split(".")[0] in self.DANGEROUS_IMPORTS:
                self.unsafe_items.append((f"Import: {alias.name}", node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Check for dangerous imports from specific modules."""
        if node.module and node.module.split(".")[0] in self.DANGEROUS_IMPORTS:
            self.unsafe_items.append((f"From Import: {node.module}", node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        """Check for dangerous function calls."""
        if isinstance(node.func, ast.Name) and node.func.id in self.DANGEROUS_CALLS:
            self.unsafe_items.append((f"Call: {node.func.id}", node.lineno))
        elif isinstance(node.func, ast.Attribute) and node.func.attr in self.DANGEROUS_CALLS:
            # Surface the attribute-style match in the analyzer log so that
            # incident response can grep for it just like the other unsafe-item
            # findings; the bare append is invisible to operators.
            logger.warning(
                "[SafeCheck] Attribute-style dangerous call detected: %s (line %s)",
                node.func.attr,
                node.lineno,
            )
            self.unsafe_items.append((f"Call: {node.func.attr}", node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        """Check for dangerous attribute access."""
        if isinstance(node.value, ast.Name) and node.value.id in self.DANGEROUS_IMPORTS:
            self.unsafe_items.append((f"Attribute Access: {node.value.id}.{node.attr}", node.lineno))
        self.generic_visit(node)

    # CUSTOM B2B SaaS — ne flagger que les concaténations dont le RÉSULTAT
    # ressemble à une construction d'appel dangereux. L'heuristique upstream
    # flaggait TOUTE concaténation de deux littéraux — or c'est la façon la
    # plus naturelle de découper une longue URL présignée, et les vrais
    # dangers (eval/exec/__import__/os.system) sont déjà bloqués par leurs
    # propres règles. Faux positif observé en prod 2026-09-03.
    _SUSPICIOUS_CONCAT = ("os.", "subprocess", "eval", "exec", "__import__",
                          "system", "popen", "getattr", "builtins")

    def visit_BinOp(self, node: ast.BinOp):
        """Flag literal concatenations that assemble dangerous identifiers,
        e.g. eval("os." + "system") — not benign string/URL building."""
        if (
            isinstance(node.left, ast.Constant)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.left.value, str)
            and isinstance(node.right.value, str)
        ):
            joined = (node.left.value + node.right.value).lower()
            if any(tok in joined for tok in self._SUSPICIOUS_CONCAT):
                self.unsafe_items.append(("Possible unsafe string concatenation", node.lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Check for dangerous function definitions (e.g., user-defined eval)."""
        if node.name in self.DANGEROUS_CALLS:
            self.unsafe_items.append((f"Function Definition: {node.name}", node.lineno))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign):
        """Check for assignments to variables that might lead to dangerous operations."""
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in self.DANGEROUS_CALLS:
                self.unsafe_items.append((f"Assignment to dangerous variable: {target.id}", node.lineno))
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda):
        """Check for lambda functions with dangerous operations."""
        if isinstance(node.body, ast.Call) and isinstance(node.body.func, ast.Name) and node.body.func.id in self.DANGEROUS_CALLS:
            self.unsafe_items.append(("Lambda with dangerous function call", node.lineno))
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp):
        """Check for list comprehensions with dangerous operations."""
        # First, visit the generators to check for any issues there
        for elem in node.generators:
            if isinstance(elem, ast.comprehension):
                self.generic_visit(elem)

        if isinstance(node.elt, ast.Call) and isinstance(node.elt.func, ast.Name) and node.elt.func.id in self.DANGEROUS_CALLS:
            self.unsafe_items.append(("List comprehension with dangerous function call", node.lineno))
        self.generic_visit(node)

    def visit_DictComp(self, node: ast.DictComp):
        """Check for dictionary comprehensions with dangerous operations."""
        # Check for dangerous calls in both the key and value expressions of the dictionary comprehension
        if isinstance(node.key, ast.Call) and isinstance(node.key.func, ast.Name) and node.key.func.id in self.DANGEROUS_CALLS:
            self.unsafe_items.append(("Dict comprehension with dangerous function call in key", node.lineno))

        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in self.DANGEROUS_CALLS:
            self.unsafe_items.append(("Dict comprehension with dangerous function call in value", node.lineno))

        # Visit other sub-nodes (e.g., the generators in the comprehension)
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp):
        """Check for set comprehensions with dangerous operations."""
        for elt in node.generators:
            if isinstance(elt, ast.comprehension):
                self.generic_visit(elt)

        if isinstance(node.elt, ast.Call) and isinstance(node.elt.func, ast.Name) and node.elt.func.id in self.DANGEROUS_CALLS:
            self.unsafe_items.append(("Set comprehension with dangerous function call", node.lineno))

        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield):
        """Check for yield statements that could be used to produce unsafe values."""
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in self.DANGEROUS_CALLS:
            self.unsafe_items.append(("Yield with dangerous function call", node.lineno))
        self.generic_visit(node)


# Module-level alias so the anti-drift test can import DANGEROUS_IMPORTS
# directly, and compare it against the upstream class attribute of the
# same name.
DANGEROUS_IMPORTS = SecurePythonAnalyzer.DANGEROUS_IMPORTS


def analyze_python_code(code: str) -> Tuple[bool, List[str]]:
    """Run the AST security analysis and return (is_safe, violations).

    Violations are formatted as readable strings, e.g. "Line 2: Import: socket",
    built from the upstream analyzer's (description, lineno) tuples stored in
    `SecurePythonAnalyzer.unsafe_items`.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"SyntaxError: {exc}"]
    analyzer = SecurePythonAnalyzer()
    analyzer.visit(tree)
    violations = [f"Line {lineno}: {description}" for description, lineno in analyzer.unsafe_items]
    return (not violations, violations)
