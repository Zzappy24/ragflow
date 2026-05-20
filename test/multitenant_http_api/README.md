# Workspace bridge for upstream HTTP-API tests

Re-runs upstream's `test/testcases/test_http_api/` suite against our workspace
tenant. Every test added (or fixed) by upstream lands here automatically at
the next merge — no copy-paste, no drift.

## Add an upstream test in 4 lines

```python
# test/multitenant_http_api/test_<name>.py
from _bridge import import_upstream_tests

import_upstream_tests(
    "test/testcases/test_http_api/<subdir>/test_<name>.py",
    globals(),
)
```

That's it. Pytest collects every `Test…` class from the upstream file under
this directory, and the `conftest.py` here resolves auth fixtures against
the workspace tenant (`X-Workspace-Id` injected on every request).

## Run

```bash
RAGFLOW_TEST_LOCAL_AUTH=1 \
  uv run python -m pytest test/multitenant_http_api/ -v
```

The bridge **skips** itself when `RAGFLOW_TEST_LOCAL_AUTH` is unset and no
`TEST_AUTH_TOKEN` / `TEST_WORKSPACE_ID` env vars are provided — safe to
include in CI without credentials.

## DESTRUCTIVE — read this before running

Many upstream fixtures call `delete_all_datasets` / `delete_all_chat_assistants`
at teardown. They wipe **every** dataset/chat in the workspace, not just the
ones the test created. Run only against a **dedicated CI workspace**:

```bash
export CI_WORKSPACE_NAME='ci-test'   # never your real workspace name
```

Default (`Général`) only exists for local dev convenience.

## When a test fails

A failure here means **either**:

1. **Real divergence** — our backend returns a different code/shape than
   upstream's tests expect. This is the bridge's whole point: surface drift
   before it ships. Investigate and fix the backend (or document why the
   divergence is intentional).
2. **Bridge gap** — a fixture is missing because we haven't loaded the right
   upstream sub-conftest. The auto-loader in `conftest.py::_import_upstream_subconftests`
   pulls every `test_http_api/<subdir>/conftest.py` automatically; if the
   missing fixture lives elsewhere (e.g. `utils/`), add an explicit import.

If a failure is environmental (missing local LLM, embedding server down),
mark it `@pytest.mark.skipif(...)` upstream-side or skip the wrapper —
don't paper over real divergences.

## Files

| File | Role |
|---|---|
| `conftest.py` | sys.path setup, namespace cache-bust, workspace-aware fixtures, auto-load of upstream sub-conftests |
| `_bridge.py` | 1-function helper: load upstream test module + re-export `Test…` classes |
| `test_*.py` | 4-line wrappers — one per upstream test file we want to inherit |

## Why a bridge instead of copy-paste

| | Copy-paste | Bridge |
|---|---|---|
| First-time cost | 1× per file | 1× total (the conftest) |
| Per-merge cost | Re-merge each file's diff manually | Zero |
| Drift risk | High (forget to update one file) | None |
| Diagnosing failures | Mixed local + upstream blame | Pure upstream — the wrapper has no logic |

## Known systemic divergence: SDK route shadow

**Discovered 2026-04-30** by this very bridge — see also [project memo].

`api/apps/sdk/doc.py` ships 11 legacy routes (POST/PUT/GET/DELETE on
`/datasets/<id>/documents`, `metadata/summary`, etc.) that shadow upstream's
new `restful_apis/document_api.py` routes. SDK is registered first, so:

- Our `@require_permission` additions in `restful_apis/document_api.py` are
  partially dead code for these URLs — the SDK versions intercept first.
- SDK routes use `@token_required` (returns `code:0` on missing auth);
  RESTful uses `@login_required` (returns `code:401`). Upstream test
  contract expects `code:401`.
- Implementations diverge in payload validation, error messages, and
  edge-case handling.

**Result**: ~50 of the currently-failing tests in this bridge fail because
of this shadow, not because of real workspace bugs. Treat new failures with
suspicion — they're often the SDK route disagreeing with the RESTful contract,
not your code.

**Fix**: 2-3h pass to delete the 11 redundant SDK routes after porting any
unique workspace/RBAC logic into the RESTful equivalent. Tracked in memory
under `project_sdk_vs_restful_route_shadow.md`.

## Limits

- Tests requiring `hypothesis`, `concurrent.futures`, or other deps need those
  deps installed (`uv pip install hypothesis`).
- Some upstream tests assume a fully configured ZHIPU LLM. We stub
  `ZHIPU_AI_API_KEY` to bypass the conftest's hardline check, but tests that
  actually invoke ZHIPU at runtime will fail. Skip those wrappers.
- The bridge requires `_load_user`'s JWT-decode path to accept our session
  token via `Authorization: Bearer <jwt>`. If upstream changes auth handling,
  the `token` fixture comment in `conftest.py` flags what to update.
