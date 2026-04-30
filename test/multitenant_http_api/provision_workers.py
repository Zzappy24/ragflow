"""
One-shot provisioning helper: create N test workspaces for pytest-xdist.

Why this is a separate script (not a conftest fixture)
------------------------------------------------------
Our bridge `conftest.py` poisons the `common` module name in sys.modules
so upstream test files can `from common import …` and resolve to the
upstream test_http_api/common.py shim. The side effect is that any
RAGFlow business code calling `from common import settings` (and many
do, transitively, when `provision_workspace` runs) crashes inside a
pytest session.

This script runs OUTSIDE the test runtime, so the `common` package is
never shadowed. It calls `management.server.services.provisioning.
provision_workspace` directly to create `ci-test-w0`…`ci-test-w{N-1}`
workspaces, owned by the CI test user (`ci.internal@cyllene.com` by
default).

Usage
-----
    PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \\
      uv run python test/multitenant_http_api/provision_workers.py 4

After running once, you can pytest-xdist normally:

    RAGFLOW_TEST_LOCAL_AUTH=1 \\
      VIEWER_EMAIL=viewer.internal@cyllene.com \\
      EDITOR_EMAIL=editor.internal@cyllene.com \\
      uv run python -m pytest test/multitenant_http_api/ -n 4

Idempotent — re-running with a higher N only creates the missing ones.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main(num_workers: int, ci_email: str = "ci.internal@cyllene.com") -> None:
    from api.db.db_models import DB
    from api.db.services.user_service import UserService
    from api.db.services.workspace_service import WorkspaceService
    from api.db.services.org_service import OrgMemberService
    from management.server.services.provisioning import provision_workspace

    with DB.connection_context():
        users = list(UserService.query(email=ci_email))
        if not users:
            sys.exit(f"❌ User {ci_email} not found.")
        user = users[0]

        org_memberships = list(OrgMemberService.query(user_id=user.id))
        if not org_memberships:
            sys.exit(
                f"❌ User {ci_email} has no organization membership. "
                "Provision an org first."
            )
        org_id = org_memberships[0].org_id

        existing = {ws.name for ws in WorkspaceService.query()}
        created, skipped = 0, 0
        for i in range(num_workers):
            name = f"ci-test-w{i}"
            if name in existing:
                skipped += 1
                continue
            provision_workspace(
                org_id=org_id,
                name=name,
                description="auto-provisioned for pytest-xdist worker",
                created_by=user.id,
            )
            created += 1
            print(f"  ✓ created {name}")

        print(f"\nDone. created={created}, already_present={skipped}, total={num_workers}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print(f"Usage: {sys.argv[0]} <num_workers>", file=sys.stderr)
        sys.exit(2)
    n = int(sys.argv[1])
    if n < 1 or n > 32:
        sys.exit(f"❌ num_workers must be 1..32, got {n}")
    ci_email = os.environ.get("CI_EMAIL", "ci.internal@cyllene.com")
    main(n, ci_email)
