"""
Static test — detect upstream divergences we need to act on.

The merge process leaves two failure modes that no other test catches:

1. **Silent Go migration**: upstream deletes a Python route file
   (api/apps/restful_apis/<name>_api.py) once their Go port is complete.
   Our fork still ships the Python — fine for now, but if we don't notice
   the deletion we will accumulate ever-growing drift until a runtime
   404 surprises us in prod.

2. **Silent custom-code introduction**: a contributor adds a new Python
   route file (or removes one) without realising upstream has the same
   path. We end up with a name collision or a dead file.

This test compares our `api/apps/restful_apis/*.py` (and `api/apps/sdk/*.py`)
to upstream/main's file list. Every divergence must be either:
- Listed in INTENTIONAL_KEPT (we deliberately keep what upstream deleted)
- Listed in CUSTOM_FORK_FILES (we own this file, never existed upstream)

Run:
    git fetch upstream  # required — test skips if upstream remote missing
    uv run pytest test/multitenant/test_upstream_drift.py -v

If it fails:
- A NEW upstream-deleted file → decide: port the Go upstream impl, OR
  add to INTENTIONAL_KEPT with a clear justification (per-user vs tenant
  semantic difference, etc.)
- A NEW custom file appeared in our fork → add to CUSTOM_FORK_FILES
  with what it's for (so the next maintainer doesn't think upstream
  has it and try to merge changes from a non-existent upstream file).
"""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories where we compare our Python route files to upstream/main.
# The check is symmetric: any file in {ours, upstream} that's missing in
# the other side counts as a divergence and MUST be classified below.
SCANNED_DIRS = (
    "api/apps/restful_apis",
    "api/apps/sdk",
)


# Files upstream USED TO HAVE and DELETED, that we INTENTIONALLY KEEP.
# Each entry requires a justification — reviewers should question new entries.
# Format: relative path → reason
INTENTIONAL_KEPT: dict[str, str] = {
    "api/apps/restful_apis/api_key_api.py": (
        "Custom B2B SaaS — per-user API keys. Upstream deleted this file when "
        "they completed the Go migration of API tokens (tenant-scoped via "
        "internal/handler/api_token.go, routes /v1/tokens). Our endpoint at "
        "/api/v1/api_keys is semantically different (per-user, not per-tenant) "
        "and is the one our frontend uses. Keeping ours; the Go fallback for "
        "tenant-level admin tokens stays untouched."
    ),
    "api/apps/restful_apis/agents.py": (
        "CUSTOM B2B SaaS — agent webhook routes (/webhook/<agent_id>, /webhook_test, "
        "/webhook_trace) for our externally-triggered agent flows (audit logging, "
        "WEBHOOK_REJECTED_NO_SECURITY guard — see CLAUDE.md). Upstream renamed/"
        "reorganised agent routes into agent_api.py (30 routes) but didn't include "
        "our webhook surface. Keep alongside agent_api.py."
    ),
    "api/apps/sdk/doc.py": (
        "Python SDK routes for datasets/documents/chunks/retrieval. Upstream "
        "DELETED the entire api/apps/sdk/ directory during a recent reorganisation "
        "(the equivalent endpoints now live in restful_apis/). We still ship this "
        "for any downstream Python SDK clients that hit /api/v1/datasets/<id>/... "
        "via the old SDK surface. TODO: confirm no consumer uses these paths AND "
        "that restful_apis/document_api.py exposes equivalent routes — if so, "
        "we can drop this file. Tracked separately from the merge."
    ),
}


# Files WE created that NEVER existed upstream (pure custom B2B SaaS code).
# These are visible in `our_files - upstream_files` but are not divergences
# we should worry about — they're our own additions.
CUSTOM_FORK_FILES: dict[str, str] = {
    "api/apps/restful_apis/internal_api.py": (
        "Custom B2B SaaS — service-to-service /verify endpoint called by "
        "the slim management backend image (which doesn't ship rag.llm). "
        "Auth via X-Internal-Secret shared secret. See models.py:verify_workspace_model."
    ),
}


def _list_files_in_upstream(path: str) -> set[str] | None:
    """Return the set of file paths under `path/` in upstream/main, or None
    if the upstream remote is not configured (e.g. headless CI without
    git remote upstream)."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "upstream/main", path],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        return None
    return {line for line in result.stdout.strip().split("\n") if line}


def _list_files_in_ours(path: str) -> set[str]:
    return {
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / path).glob("*.py")
        if p.name != "__init__.py"
    }


def test_no_silent_upstream_python_drift():
    """Compare our route files to upstream/main. Every diff must be
    classified in INTENTIONAL_KEPT or CUSTOM_FORK_FILES."""
    # Probe once — if upstream is not configured, skip the whole test
    # rather than fail (CI runners that don't have it shouldn't break).
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "upstream/main"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if probe.returncode != 0:
        pytest.skip(
            "git remote 'upstream' not configured (or not fetched). "
            "Run: git remote add upstream https://github.com/infiniflow/ragflow && "
            "git fetch upstream"
        )

    ours_total: set[str] = set()
    upstream_total: set[str] = set()
    for d in SCANNED_DIRS:
        ours_total |= _list_files_in_ours(d)
        up = _list_files_in_upstream(d)
        if up is None:
            pytest.skip(f"could not list {d} on upstream/main")
        upstream_total |= up

    # Files we have but upstream deleted (or never had).
    extras = sorted(ours_total - upstream_total)
    unknown_extras = [
        f for f in extras
        if f not in INTENTIONAL_KEPT and f not in CUSTOM_FORK_FILES
    ]

    # Files upstream has but we deleted (suspicious — usually means we
    # missed a new upstream feature during a merge).
    missing = sorted(upstream_total - ours_total)

    if unknown_extras or missing:
        lines = []
        if unknown_extras:
            lines.append("The following Python route files exist in our fork")
            lines.append("but NOT in upstream/main. Classify each one:")
            lines.append("")
            for f in unknown_extras:
                lines.append(f"  - {f}")
            lines.append("")
            lines.append("ACTION — add ONE entry to test/multitenant/test_upstream_drift.py:")
            lines.append("  • If WE created this file (custom feature) → CUSTOM_FORK_FILES")
            lines.append("  • If UPSTREAM deleted it after a Go migration we're not")
            lines.append("    following yet → INTENTIONAL_KEPT  (with justification)")
            lines.append("")
        if missing:
            lines.append("The following upstream/main Python route files are")
            lines.append("ABSENT from our fork. Either a merge dropped them or")
            lines.append("they're brand new in upstream — check:")
            lines.append("")
            for f in missing:
                lines.append(f"  - {f}")
            lines.append("")
            lines.append("ACTION — git log upstream/main -- <file> to see what's new,")
            lines.append("then either:")
            lines.append("  • Port the file in (cherry-pick or manual copy)")
            lines.append("  • If the route is intentionally not surfaced in our fork,")
            lines.append("    add a one-line skip comment here documenting why.")
        raise AssertionError("\n".join(lines))


def test_intentional_kept_entries_still_match_reality():
    """Defensive: if we list a file in INTENTIONAL_KEPT but it doesn't
    exist in our fork OR it DOES exist upstream, the entry is stale.
    Clean it up so the divergence list stays meaningful."""
    stale = []
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", "upstream/main"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    if probe.returncode != 0:
        pytest.skip("upstream remote not configured")

    upstream_paths_by_dir = {}
    for d in SCANNED_DIRS:
        upstream_paths_by_dir[d] = _list_files_in_upstream(d) or set()

    for path in INTENTIONAL_KEPT:
        full = REPO_ROOT / path
        if not full.exists():
            stale.append(f"  - {path} (listed in INTENTIONAL_KEPT but missing in our fork)")
            continue
        # Did upstream re-add it?
        d = "/".join(path.split("/")[:-1])
        if path in upstream_paths_by_dir.get(d, set()):
            stale.append(
                f"  - {path} (listed in INTENTIONAL_KEPT but upstream/main has it now — "
                "promote to a regular merge / remove the divergence note)"
            )

    if stale:
        raise AssertionError(
            "Stale INTENTIONAL_KEPT entries:\n" + "\n".join(stale)
        )
