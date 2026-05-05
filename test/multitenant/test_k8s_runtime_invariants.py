"""
Static AST invariants — verify the K8s heartbeat + worker-recycle injection
in task_executor.py survives upstream merges.

The actual logic lives in `rag/svr/_k8s_runtime.py` (a custom file, immune
to upstream conflicts). The injection into upstream's `task_executor.py`
is intentionally minimal — one import line and two call sites — so a merge
conflict is easy to resolve. This test makes sure the resolution actually
happened: if either the import or one of the call sites disappears
(silent revert from a merge), CI fails.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 \\
      uv run python -m pytest test/multitenant/test_k8s_runtime_invariants.py -v

No backend required — pure AST parsing.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TASK_EXECUTOR = REPO / "rag" / "svr" / "task_executor.py"
K8S_RUNTIME = REPO / "rag" / "svr" / "_k8s_runtime.py"


# ---------------------------------------------------------------------------
# 1. The custom module must exist and expose the two required symbols.
# ---------------------------------------------------------------------------

class TestK8sRuntimeModule:
    def test_module_file_present(self):
        assert K8S_RUNTIME.is_file(), (
            f"{K8S_RUNTIME.relative_to(REPO)} is missing — the K8s liveness "
            "heartbeat + worker recycle module was deleted. Restore it before "
            "deploying to Cyllene; the Helm chart's livenessProbe and the "
            "WORKER_RECYCLE_AFTER_TASKS env var depend on it."
        )

    def test_module_exposes_required_functions(self):
        tree = ast.parse(K8S_RUNTIME.read_text())
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for required in ("touch_heartbeat", "should_recycle"):
            assert required in defined, (
                f"_k8s_runtime.py no longer defines `{required}`. "
                "task_executor.py imports this symbol — restore it or update "
                "the import + call sites in lockstep."
            )


# ---------------------------------------------------------------------------
# 2. task_executor.py must IMPORT both symbols from the custom module.
# ---------------------------------------------------------------------------

class TestTaskExecutorImport:
    def test_imports_from_k8s_runtime(self):
        tree = ast.parse(TASK_EXECUTOR.read_text())
        imported_pairs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "rag.svr._k8s_runtime":
                for alias in node.names:
                    imported_pairs.add((node.module, alias.name))

        for required in ("touch_heartbeat", "should_recycle"):
            assert ("rag.svr._k8s_runtime", required) in imported_pairs, (
                f"task_executor.py no longer imports `{required}` from "
                "rag.svr._k8s_runtime. The K8s liveness/recycle hooks have "
                "been silently reverted (likely an upstream merge). "
                "Re-apply the customisation — search the file for "
                "`CUSTOM B2B SaaS — K8s` to find the intended injection sites."
            )


# ---------------------------------------------------------------------------
# 3. Both symbols must actually be CALLED somewhere in task_executor.py
# (an import alone proves nothing — the merge could have kept the import
# line and dropped the call sites).
# ---------------------------------------------------------------------------

class TestTaskExecutorCallSites:
    @pytest.fixture(scope="class")
    def call_names(self) -> set[str]:
        """All bare-name function call targets in task_executor.py."""
        tree = ast.parse(TASK_EXECUTOR.read_text())
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
        return names

    def test_touch_heartbeat_is_called(self, call_names):
        assert "touch_heartbeat" in call_names, (
            "touch_heartbeat() is imported but never called in task_executor.py. "
            "The kubelet liveness probe will fail (no heartbeat file refreshed) "
            "and pods will restart in a loop. Re-add the call inside report_status, "
            "right after `redis_lock.release()` (search `CUSTOM B2B SaaS — K8s`)."
        )

    def test_should_recycle_is_called(self, call_names):
        assert "should_recycle" in call_names, (
            "should_recycle() is imported but never called in task_executor.py. "
            "Workers will never recycle, and slow memory leaks (sentence-"
            "transformers, pdfminer) will eventually OOM-kill pods. Re-add the "
            "call inside report_status (search `CUSTOM B2B SaaS — K8s`)."
        )


# ---------------------------------------------------------------------------
# 4. should_recycle()'s return value must be wired to stop_event.set().
# Without that the recycle threshold is just logged but never enacted.
# ---------------------------------------------------------------------------

class TestRecycleWiring:
    def test_should_recycle_triggers_stop_event(self):
        """Find an `if should_recycle(...)` whose body sets stop_event."""
        tree = ast.parse(TASK_EXECUTOR.read_text())

        def _calls_stop_event_set(node: ast.AST) -> bool:
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "set"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "stop_event"
                ):
                    return True
            return False

        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            # Look for `if should_recycle(...)` as the test expression.
            test = node.test
            if (
                isinstance(test, ast.Call)
                and isinstance(test.func, ast.Name)
                and test.func.id == "should_recycle"
                and any(_calls_stop_event_set(b) for b in node.body)
            ):
                return  # invariant satisfied

        pytest.fail(
            "Did not find `if should_recycle(...): stop_event.set()` "
            "in task_executor.py. The recycle threshold will be evaluated "
            "but the worker will never actually exit when it's reached. "
            "Re-add the wiring (search `CUSTOM B2B SaaS — K8s`)."
        )
