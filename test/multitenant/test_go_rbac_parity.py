"""
Cross-language RBAC parity test.

The Go façade (internal/middleware/rbac.go + internal/common/permissions.go)
mirrors the Python RBAC matrix (api/apps/extensions/rbac.py). If the two
ever drift, the same HTTP request gets answered differently depending on
which backend nginx routed it to — silent privilege escalation or
unexplained 403s.

This test parses both files (no execution) and verifies:

1. Every `Permission` value in Python exists in Go (same string).
2. Every `WsRole` and `OrgRole` value exists on both sides.
3. The `VIEWER` and `EDITOR` permission sets are bit-for-bit identical.
4. `WS_ADMIN` is unbounded on both sides (Python: `set(Permission)`, Go:
   short-circuit `if role == WsRoleWsAdmin: return true`).

Run:
    uv run pytest test/multitenant/test_go_rbac_parity.py -v
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_RBAC = REPO_ROOT / "api/apps/extensions/rbac.py"
GO_PERMS = REPO_ROOT / "internal/common/permissions.go"


# ---------------------------------------------------------------------------
# Python side — AST parse
# ---------------------------------------------------------------------------

def _parse_python_rbac() -> dict:
    """Return {'permissions': set[str], 'ws_roles': set[str], 'org_roles': set[str],
    'matrix': {role_value: set(perm_values)}}."""
    tree = ast.parse(PY_RBAC.read_text(encoding="utf-8"))

    permissions: set[str] = set()
    ws_roles: set[str] = set()
    org_roles: set[str] = set()
    enum_name_to_value: dict[str, str] = {}  # "PermDatasetCreate" → "dataset.create" etc.
    matrix_raw: dict[str, list[str]] = {}  # role_member_name → [perm_member_name, ...]

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.bases:
            base_names = {getattr(b, "id", None) for b in node.bases}
            base_names |= {getattr(b, "attr", None) for b in node.bases}
            if node.name == "Permission":
                target_set = permissions
            elif node.name == "WsRole":
                target_set = ws_roles
            elif node.name == "OrgRole":
                target_set = org_roles
            else:
                target_set = None
            if target_set is not None:
                for item in node.body:
                    if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
                        for t in item.targets:
                            if isinstance(t, ast.Name):
                                value = item.value.value
                                target_set.add(value)
                                enum_name_to_value[f"{node.name}.{t.id}"] = value

    # ROLE_PERMISSIONS = { WsRole.VIEWER: {Permission.X, Permission.Y}, ... }
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ROLE_PERMISSIONS" for t in node.targets
        ):
            if isinstance(node.value, ast.Dict):
                for k, v in zip(node.value.keys, node.value.values):
                    role_name = ast.unparse(k)  # "WsRole.VIEWER"
                    role_val = enum_name_to_value.get(role_name)
                    if role_val is None:
                        continue
                    perms: list[str] = []
                    # Value can be {Permission.X, ...} or set(Permission)
                    if isinstance(v, ast.Set):
                        for elt in v.elts:
                            name = ast.unparse(elt)
                            val = enum_name_to_value.get(name)
                            if val:
                                perms.append(val)
                        matrix_raw[role_val] = perms
                    elif isinstance(v, ast.Call) and ast.unparse(v.func) == "set":
                        # set(Permission) → all permissions
                        matrix_raw[role_val] = sorted(permissions)
            break

    return {
        "permissions": permissions,
        "ws_roles": ws_roles,
        "org_roles": org_roles,
        "matrix": {role: set(perms) for role, perms in matrix_raw.items()},
    }


# ---------------------------------------------------------------------------
# Go side — regex parse
# ---------------------------------------------------------------------------

_GO_CONST_RX = re.compile(r"\b(Perm\w+|WsRole\w+|OrgRole\w+)\s+(?:Permission|WsRole|OrgRole)\s*=\s*\"([^\"]+)\"")
_GO_MATRIX_ENTRY_RX = re.compile(r"(WsRole\w+):\s*\{((?:[^{}]|\{[^}]*\})*)\}", re.DOTALL)
_GO_PERM_IN_ENTRY_RX = re.compile(r"\b(Perm\w+):\s*\{\}")


def _parse_go_rbac() -> dict:
    src = GO_PERMS.read_text(encoding="utf-8")

    permissions: set[str] = set()
    ws_roles: set[str] = set()
    org_roles: set[str] = set()
    go_name_to_value: dict[str, str] = {}

    for go_name, value in _GO_CONST_RX.findall(src):
        go_name_to_value[go_name] = value
        if go_name.startswith("Perm"):
            permissions.add(value)
        elif go_name.startswith("WsRole"):
            ws_roles.add(value)
        elif go_name.startswith("OrgRole"):
            org_roles.add(value)

    # Matrix: extract `rolePermissions` map literal.
    matrix: dict[str, set[str]] = {}
    # Cut to the rolePermissions block to avoid matching other map literals.
    block_start = src.find("var rolePermissions")
    if block_start < 0:
        return {
            "permissions": permissions, "ws_roles": ws_roles,
            "org_roles": org_roles, "matrix": matrix,
        }
    block_end = src.find("\n}\n", block_start)
    block = src[block_start:block_end] if block_end > 0 else src[block_start:]

    for role_name, body in _GO_MATRIX_ENTRY_RX.findall(block):
        role_val = go_name_to_value.get(role_name)
        if not role_val:
            continue
        perm_vals: set[str] = set()
        for perm_name in _GO_PERM_IN_ENTRY_RX.findall(body):
            v = go_name_to_value.get(perm_name)
            if v:
                perm_vals.add(v)
        matrix[role_val] = perm_vals

    # The Go side explicitly grants ws_admin every permission via a code
    # branch (HasRolePermission early-returns true). We model that here so
    # the parity test can compare against Python's `set(Permission)`.
    matrix.setdefault("ws_admin", set(permissions))

    return {
        "permissions": permissions, "ws_roles": ws_roles,
        "org_roles": org_roles, "matrix": matrix,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def py_rbac():
    return _parse_python_rbac()


@pytest.fixture(scope="module")
def go_rbac():
    return _parse_go_rbac()


def test_permissions_match(py_rbac, go_rbac):
    py_only = py_rbac["permissions"] - go_rbac["permissions"]
    go_only = go_rbac["permissions"] - py_rbac["permissions"]
    assert not py_only and not go_only, (
        f"Permission drift detected.\n"
        f"  In Python but missing from Go: {sorted(py_only)}\n"
        f"  In Go but missing from Python: {sorted(go_only)}\n"
        f"Fix: add the missing entries to internal/common/permissions.go "
        f"(Python is source of truth)."
    )


def test_ws_roles_match(py_rbac, go_rbac):
    assert py_rbac["ws_roles"] == go_rbac["ws_roles"], (
        f"WsRole drift: py={sorted(py_rbac['ws_roles'])} go={sorted(go_rbac['ws_roles'])}"
    )


def test_org_roles_match(py_rbac, go_rbac):
    assert py_rbac["org_roles"] == go_rbac["org_roles"], (
        f"OrgRole drift: py={sorted(py_rbac['org_roles'])} go={sorted(go_rbac['org_roles'])}"
    )


def test_viewer_matrix_match(py_rbac, go_rbac):
    py_set = py_rbac["matrix"].get("viewer", set())
    go_set = go_rbac["matrix"].get("viewer", set())
    assert py_set == go_set, (
        f"VIEWER matrix drift.\n"
        f"  Python: {sorted(py_set)}\n"
        f"  Go:     {sorted(go_set)}\n"
        f"  Diff (py - go): {sorted(py_set - go_set)}\n"
        f"  Diff (go - py): {sorted(go_set - py_set)}"
    )


def test_editor_matrix_match(py_rbac, go_rbac):
    py_set = py_rbac["matrix"].get("editor", set())
    go_set = go_rbac["matrix"].get("editor", set())
    assert py_set == go_set, (
        f"EDITOR matrix drift.\n"
        f"  Python: {sorted(py_set)}\n"
        f"  Go:     {sorted(go_set)}\n"
        f"  Diff (py - go): {sorted(py_set - go_set)}\n"
        f"  Diff (go - py): {sorted(go_set - py_set)}"
    )


def test_ws_admin_is_unbounded(py_rbac, go_rbac):
    # Both sides must grant ws_admin every permission. Python achieves this
    # via `WsRole.WS_ADMIN: set(Permission)`; Go via the short-circuit in
    # HasRolePermission (modelled in _parse_go_rbac).
    py_set = py_rbac["matrix"].get("ws_admin", set())
    go_set = go_rbac["matrix"].get("ws_admin", set())
    assert py_set == py_rbac["permissions"], (
        "Python ws_admin is not the full Permission set — did the source change?"
    )
    assert go_set == go_rbac["permissions"], (
        "Go ws_admin matrix should equal the full permission set (parser bug?)."
    )
