"""
Bridge helper — re-exports upstream test classes into wrapper files in this
directory so pytest collects them under our workspace fixtures.

Usage in a wrapper file:

    # test/multitenant_http_api/test_<name>.py
    from _bridge import import_upstream_tests
    import_upstream_tests(
        "test/testcases/test_http_api/<subdir>/test_<name>.py",
        globals(),
    )
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def import_upstream_tests(upstream_relative_path: str, globals_dict: dict) -> None:
    """Load an upstream test file and re-export every TestXxx class.

    Parameters
    ----------
    upstream_relative_path : repo-root-relative path to the upstream test file.
    globals_dict : pass `globals()` from the wrapper file.
    """
    upstream_path = REPO_ROOT / upstream_relative_path
    if not upstream_path.exists():
        raise FileNotFoundError(f"Upstream test not found: {upstream_path}")

    module_name = "_upstream_" + upstream_path.stem
    spec = importlib.util.spec_from_file_location(module_name, str(upstream_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        attr = getattr(module, attr_name)
        # Re-export Test classes so pytest collects their methods.
        if attr_name.startswith("Test"):
            globals_dict[attr_name] = attr
            continue
        # Re-export module-level pytest fixtures defined in the upstream file
        # itself (some upstream tests declare per-file fixtures next to the
        # tests, not in a conftest). Pytest detects them by attribute.
        if hasattr(attr, "_fixture_function_marker") or hasattr(attr, "_pytestfixturefunction"):
            globals_dict[attr_name] = attr
