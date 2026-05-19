"""Static invariant — every call from `api/apps/restful_apis/*.py` into
`api/apps/services/*` must use keyword args that exist in the target
function's signature.

Background:
  On 2026-05-19 a workspace-RBAC refactor renamed the service param
  `user_id` → `tenant_id` in file_api_service.get_parent_folder /
  get_all_parent_folders. The two restful handlers were not updated and
  kept passing `user_id=`, producing a TypeError at every request and
  a generic 500 in the UI.

  A hand-written lambda mock in test/testcases/restful_api/ had the same
  stale `user_id=` signature, so the test passed against a fiction and
  never exercised the real code path. Moreover that test directory is
  excluded from our canonical pytest runs (see CLAUDE.md).

  This test catches the bug class statically — no server, no mocks, no
  imports. Pure AST inspection of source files. It also surfaced a
  second identical bug on file_api.py:345 the first time it ran.

Run:
    uv run pytest test/multitenant/test_handler_service_kwargs.py -v
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HANDLERS_DIR = REPO_ROOT / "api/apps/restful_apis"
SERVICES_DIR = REPO_ROOT / "api/apps/services"
SERVICES_PKG = "api.apps.services"


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(), filename=str(path))
    except SyntaxError:
        return None


def _signatures_of(tree: ast.Module) -> dict[str, tuple[set[str], bool]]:
    """{func_name: (accepted_param_names, accepts_var_kwargs)} for top-level defs."""
    out: dict[str, tuple[set[str], bool]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        params: set[str] = set()
        for a in args.args:
            params.add(a.arg)
        for a in args.kwonlyargs:
            params.add(a.arg)
        if args.vararg:
            params.add(args.vararg.arg)
        accepts_var_kw = args.kwarg is not None
        if args.kwarg:
            params.add(args.kwarg.arg)
        out[node.name] = (params, accepts_var_kw)
    return out


def _service_signature_index() -> dict[str, dict[str, tuple[set[str], bool]]]:
    """{'api.apps.services.foo': {'fn_name': (params, var_kw)}}"""
    index: dict[str, dict[str, tuple[set[str], bool]]] = {}
    for f in sorted(SERVICES_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        tree = _parse(f)
        if tree is None:
            continue
        index[f"{SERVICES_PKG}.{f.stem}"] = _signatures_of(tree)
    return index


def _service_aliases(tree: ast.Module) -> dict[str, str]:
    """Returns a mapping from each handler-local name to its target:
      'mod_alias' → 'api.apps.services.foo'          (module imports)
      'func_alias' → 'api.apps.services.foo::func'   (direct func imports)
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module == SERVICES_PKG:
            for n in node.names:
                aliases[n.asname or n.name] = f"{SERVICES_PKG}.{n.name}"
        elif node.module.startswith(SERVICES_PKG + "."):
            for n in node.names:
                aliases[n.asname or n.name] = f"{node.module}::{n.name}"
    return aliases


def _scan_handler(
    handler: Path, sig_index: dict[str, dict[str, tuple[set[str], bool]]]
) -> list[tuple[int, str, str, list[str]]]:
    tree = _parse(handler)
    if tree is None:
        return []
    aliases = _service_aliases(tree)
    if not aliases:
        return []

    issues: list[tuple[int, str, str, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        f = node.func
        sig_entry = None
        target_label = None

        # Pattern A: `module_alias.func(...)`
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            alias = f.value.id
            method = f.attr
            tgt = aliases.get(alias)
            if tgt and "::" not in tgt and tgt in sig_index:
                sig_entry = sig_index[tgt].get(method)
                target_label = f"{tgt}.{method}"
        # Pattern B: `func(...)` where func was directly imported
        elif isinstance(f, ast.Name):
            tgt = aliases.get(f.id)
            if tgt and "::" in tgt:
                mod, name = tgt.split("::", 1)
                if mod in sig_index:
                    sig_entry = sig_index[mod].get(name)
                    target_label = f"{mod}.{name}"

        if sig_entry is None or target_label is None:
            continue

        params, var_kw = sig_entry
        if var_kw:
            continue  # function accepts **kwargs — anything goes

        for kw in node.keywords:
            if kw.arg is None:
                continue  # **dict unpacking at call site — cannot statically check
            if kw.arg not in params:
                issues.append((node.lineno, target_label, kw.arg, sorted(params)))
    return issues


class TestHandlerServiceKwargs:
    """Every keyword arg passed from a restful handler to a service function
    must exist in that function's signature.

    Catches the family of bugs where a service param is renamed (e.g.
    `user_id` → `tenant_id` during the multi-tenant refactor) but a caller
    is missed. Python raises TypeError only at runtime, on the first hit.
    """

    def test_no_handler_to_service_kwarg_mismatch(self):
        sig_index = _service_signature_index()
        assert sig_index, "Service signature index is empty — services dir missing?"

        all_issues: list[str] = []
        for handler in sorted(HANDLERS_DIR.glob("*.py")):
            for line, target, bad_kw, params in _scan_handler(handler, sig_index):
                rel = handler.relative_to(REPO_ROOT)
                preview = params[:8] + (["..."] if len(params) > 8 else [])
                all_issues.append(
                    f"  {rel}:{line}\n"
                    f"    calls   {target}(...)\n"
                    f"    bad kw  {bad_kw!r}\n"
                    f"    accepts {preview}"
                )

        assert not all_issues, (
            "Handler→service keyword-argument mismatch (TypeError at runtime, "
            "generic 500 in UI). Either rename the call to match the service "
            "signature, or update the service signature.\n\n"
            + "\n\n".join(all_issues)
        )
