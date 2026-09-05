"""Pin de l'incident starvation API 2026-09-05 (4 pods api figés derrière Envoy,
« 1 login sur 2 », d'abord attribué à l'ingress).

Cause racine : Quart n'offloade une vue synchrone (``def``) dans un thread QUE
s'il la voit directement. Nos décorateurs de route (require_permission,
add_tenant_id_to_kwargs, token_required, …) sont des ``async def wrapper``
qui appelaient la vue sync INLINE → son corps (Peewee, doc-store, MinIO)
tournait SUR l'event loop du pod et gelait tout, probes comprises. 66 routes
``def`` étaient concernées, dont le listing des documents et le scan complet
des métadonnées aplaties.

Trois verrous :
  1. ``call_view`` exécute bien une vue sync hors du thread principal, avec
     ses contextvars, et laisse passer les coroutines.
  2. Aucun décorateur de route ne rappelle la vue inline (grep source).
  3. Aucune fonction async sous api/apps (+ dialog_service, metadata_utils,
     agent retrieval) n'appelle directement un helper bloquant connu.
"""

import ast
import asyncio
import contextvars
import pathlib
import re
import threading

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

DECORATOR_FILES = [
    "api/utils/api_utils.py",
    "api/apps/extensions/rbac.py",
    "api/apps/extensions/audit.py",
]

# Helpers synchrones qui touchent le doc-store / le stockage objet / le
# parsing (PDF) : interdits en appel direct dans une fonction async.
BLOCKING_HELPERS = {
    "get_flatted_meta_by_kbs",
    "retrieval_by_children",
    "remove_document",
    "delete_chunk_images",
    "queue_tasks",
    "update_document_metadata",
    "get_blob",
    "filter_doc_ids_by_meta_pushdown",
    "rerank_by_model",
    "upload_info",
}
BLOCKING_ATTR_OWNERS = {"docStoreConn", "STORAGE_IMPL"}
# Appels modèle synchrones (HTTP vLLM / ASR, centaines de ms à plusieurs s)
# sur un receveur nommé *_mdl : embd_mdl.encode(...), asr_mdl.transcription(...)
BLOCKING_MODEL_METHODS = {"encode", "encode_queries", "transcription", "similarity"}

SCAN_ROOTS = [
    "api/apps",
    "api/db/services/dialog_service.py",
    "common/metadata_utils.py",
    "agent/tools/retrieval.py",
    "rag/nlp/search.py",
]


# ---------------------------------------------------------------- verrou 1
def test_call_view_runs_sync_off_the_event_loop():
    from common.misc_utils import call_view

    seen = {}
    cv = contextvars.ContextVar("req")

    def sync_view(x, y=0):
        seen["thread"] = threading.current_thread()
        seen["ctx"] = cv.get(None)
        return x + y

    async def main():
        cv.set("ctx-42")
        return await call_view(sync_view, 1, y=2)

    assert asyncio.run(main()) == 3
    assert seen["thread"] is not threading.main_thread(), "vue sync exécutée SUR l'event loop"
    assert seen["ctx"] == "ctx-42", "contextvars (request/g) perdus dans le thread"


def test_call_view_awaits_coroutines_and_coroutine_returning_wrappers():
    from common.misc_utils import call_view

    async def async_view(x):
        return x * 2

    def sync_wrapper_returning_coro(x):
        return async_view(x)

    async def main():
        return await call_view(async_view, 2), await call_view(sync_wrapper_returning_coro, 3)

    assert asyncio.run(main()) == (4, 6)


# ---------------------------------------------------------------- verrou 2
_INLINE_CALL = re.compile(r"^\s*(return|result =)\s+func\(\*?_?args?[^\n]*\)\s*$|^\s*return func\(\*\*kwargs\)\s*$", re.MULTILINE)


def test_route_decorators_never_call_the_view_inline():
    bad = []
    for rel in DECORATOR_FILES:
        src = (REPO / rel).read_text()
        for m in _INLINE_CALL.finditer(src):
            line = src[: m.start()].count("\n") + 1
            bad.append(f"{rel}:{line}: {m.group(0).strip()}")
    assert not bad, "Décorateur qui rappelle la vue inline (une vue `def` tournera sur l'event loop) — passer par call_view :\n" + "\n".join(bad)


# ---------------------------------------------------------------- verrou 3
def _violations(path: pathlib.Path):
    src = path.read_text()
    if not any(h in src for h in BLOCKING_HELPERS | BLOCKING_ATTR_OWNERS | BLOCKING_MODEL_METHODS):
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

        def visit_Lambda(self, n):
            # un loader différé (metas_loader=lambda: ...) est consommé
            # ailleurs, via thread_pool_exec (cf. apply_meta_data_filter)
            return

        def visit_AsyncFunctionDef(self, n):
            self.async_depth += 1
            self.generic_visit(n)
            self.async_depth -= 1

        def visit_Await(self, n):
            # un appel awaité est une coroutine (route, service async) : pas un
            # helper sync. On ne regarde que ses arguments.
            if isinstance(n.value, ast.Call):
                for a in n.value.args:
                    self.visit(a)
                for k in n.value.keywords:
                    self.visit(k.value)
                return
            self.generic_visit(n)

        def visit_Call(self, n):
            f = n.func
            if isinstance(f, ast.Name) and f.id == "thread_pool_exec":
                return
            if self.async_depth > 0 and isinstance(f, ast.Attribute):
                owner = f.value.attr if isinstance(f.value, ast.Attribute) else (f.value.id if isinstance(f.value, ast.Name) else None)
                if owner in BLOCKING_ATTR_OWNERS or f.attr in BLOCKING_HELPERS:
                    bad.append((n.lineno, f"{owner}.{f.attr}"))
            if self.async_depth > 0 and isinstance(f, ast.Name) and f.id in BLOCKING_HELPERS:
                bad.append((n.lineno, f.id))
            if self.async_depth > 0 and isinstance(f, ast.Attribute) and f.attr in BLOCKING_MODEL_METHODS and isinstance(f.value, ast.Name) and f.value.id.endswith("_mdl"):
                bad.append((n.lineno, f"{f.value.id}.{f.attr}"))
            self.generic_visit(n)

    V().visit(tree)
    return bad


def _scan_paths():
    for root in SCAN_ROOTS:
        p = REPO / root
        if p.is_file():
            yield p
        else:
            yield from sorted(p.rglob("*.py"))


@pytest.mark.parametrize("path", list(_scan_paths()), ids=lambda p: str(p.relative_to(REPO)))
def test_no_blocking_helper_called_directly_in_async(path):
    bad = _violations(path)
    assert not bad, f"{path.relative_to(REPO)} : appel bloquant direct dans une fonction async (gèle l'event loop du pod api) — envelopper dans thread_pool_exec : " + ", ".join(
        f"l.{ln} {what}" for ln, what in bad
    )
