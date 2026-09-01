"""Pin de la classe de bug « gel de la boucle d'événements » (2026-08-31).

Un appel doc-store SYNCHRONE (es-py/infinity) exécuté directement dans une
route/fonction ASYNC gèle la boucle du pod api pendant toute la durée de la
requête (jusqu'au timeout, 600 s) — probes /healthz comprises. Constaté en
prod : 3 pods api sur 4 NotReady en navigant un dataset de 960 docs, et les
deletes (delete_by_query, ~7 s) gelaient chaque suppression de document.

Règle : dans api/apps/**, tout appel `X.docStoreConn.<méthode>(...)` situé
dans une fonction async DOIT passer par thread_pool_exec. Les fonctions sync
(fermetures _run_sync/_switch_sync destinées au thread pool, services
appelés via thread_pool_exec depuis la route) restent libres.
"""
import ast
import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]

# Fichiers pas encore assainis (suivis, à vider au fil des passes) :
# - doc_metadata_service est partagé avec le mgmt-backend (contexte sync) —
#   l'offload doit se faire chez ses appelants async, pass dédiée à prévoir.
WHITELIST = set()


def _violations_in(path: pathlib.Path):
    src = path.read_text()
    if "docStoreConn" not in src:
        return []
    tree = ast.parse(src)
    bad = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.async_depth = 0

        def visit_FunctionDef(self, n):
            saved = self.async_depth
            self.async_depth = 0
            self.generic_visit(n)
            self.async_depth = saved

        def visit_AsyncFunctionDef(self, n):
            self.async_depth += 1
            self.generic_visit(n)
            self.async_depth -= 1

        def visit_Call(self, n):
            f = n.func
            # thread_pool_exec(settings.docStoreConn.x, ...) : les arguments
            # contiennent l'attribut mais PAS d'appel direct — on ne descend
            # pas dans les args d'un thread_pool_exec pour les Call imbriqués
            # légitimes (lambdas offloadées).
            if isinstance(f, ast.Name) and f.id == "thread_pool_exec":
                return
            if (self.async_depth > 0 and isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Attribute)
                    and f.value.attr == "docStoreConn"):
                bad.append((n.lineno, f.attr))
            self.generic_visit(n)

    V().visit(tree)
    return bad


def test_all_api_pages_compile():
    """Toute page sous api/apps/** doit COMPILER. register_page importe ces
    fichiers au boot : une SyntaxError (ex. un `await` ajouté dans une route
    restée `def` lors d'une passe d'offload — incident prod 2026-09-01,
    pods api en CrashLoop) tue le pod entier. py_compile au commit ne
    suffit pas si on ne compile que les fichiers du dernier commit."""
    errors = []
    for path in sorted((REPO / "api" / "apps").rglob("*.py")):
        try:
            compile(path.read_text(), str(path), "exec")
        except SyntaxError as e:
            errors.append(f"{path.relative_to(REPO)}: {e}")
    assert not errors, "Pages API qui ne compilent pas :\n" + "\n".join(errors)


def test_no_direct_docstore_calls_in_async_api_routes():
    offenders = {}
    for path in sorted((REPO / "api" / "apps").rglob("*.py")):
        rel = str(path.relative_to(REPO))
        if rel in WHITELIST:
            continue
        v = _violations_in(path)
        if v:
            offenders[rel] = v
    assert not offenders, (
        "Appels doc-store synchrones dans des fonctions async (gel de la boucle "
        "du pod api — utiliser `await thread_pool_exec(settings.docStoreConn.X, ...)`) :\n"
        + "\n".join(f"  {f}: {v}" for f, v in offenders.items())
    )
