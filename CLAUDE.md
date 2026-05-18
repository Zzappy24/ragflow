# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Tool Usage

Use the fff MCP tools (`mcp__fff__grep`, `mcp__fff__find_files`, `mcp__fff__multi_grep`) for all file search operations instead of default Grep/Glob tools.

## Project Overview

RAGFlow is an open-source RAG (Retrieval-Augmented Generation) engine based on deep document understanding. It's a full-stack application with:

- Python backend (Flask-based API server)
- React/TypeScript frontend (built with vitejs)
- Microservices architecture with Docker deployment
- Multiple data stores (MySQL, Elasticsearch/Infinity, Redis, MinIO)

## Architecture

### Backend (`/api/`)

- **Main Server**: `api/ragflow_server.py` - Flask application entry point
- **Apps**: Modular Flask blueprints in `api/apps/` for different functionalities:
  - `kb_app.py` - Knowledge base management
  - `dialog_app.py` - Chat/conversation handling
  - `document_app.py` - Document processing
  - `canvas_app.py` - Agent workflow canvas
  - `file_app.py` - File upload/management
- **Services**: Business logic in `api/db/services/`
- **Models**: Database models in `api/db/db_models.py`

### Core Processing (`/rag/`)

- **Document Processing**: `deepdoc/` - PDF parsing, OCR, layout analysis
- **LLM Integration**: `rag/llm/` - Model abstractions for chat, embedding, reranking
- **RAG Pipeline**: `rag/flow/` - Chunking, parsing, tokenization
- **Graph RAG**: `rag/graphrag/` - Knowledge graph construction and querying

### Agent System (`/agent/`)

- **Components**: Modular workflow components (LLM, retrieval, categorize, etc.)
- **Templates**: Pre-built agent workflows in `agent/templates/`
- **Tools**: External API integrations (Tavily, Wikipedia, SQL execution, etc.)

### Frontend (`/web/`)

- React/TypeScript with vitejs framework
- shadcn/ui components
- State management with Zustand
- Tailwind CSS for styling

## Common Development Commands

### Backend Development

```bash
# Install Python dependencies
uv sync --python 3.12 --all-extras
uv run python3 download_deps.py
pre-commit install

# Start dependent services
docker compose -f docker/docker-compose-base.yml up -d

# Run backend (requires services to be running)
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh

# Local dev shortcuts (custom — not upstream):
#   scripts/dev_simple.sh   # 1× server (hot-reload) + 1× task_executor — daily dev
#   scripts/dev_scaled.sh   # 4× hypercorn + 4× task_executor — full-bridge runs
# Scaled is ~1.8x faster on the slow batch, ~6-10 GB more RAM, no hot-reload.
# See api/asgi.py for the multi-worker entry point.

# Run tests
uv run pytest

# Linting
ruff check
ruff format
```

### Frontend Development

```bash
cd web
npm install
npm run dev        # Development server
npm run build      # Production build
npm run lint       # ESLint
npm run test       # Jest tests
```

### Docker Development

```bash
# Full stack with Docker
cd docker
docker compose -f docker-compose.yml up -d

# Check server status
docker logs -f ragflow-server

# Rebuild images
docker build --platform linux/amd64 -f Dockerfile -t infiniflow/ragflow:nightly .
```

## Key Configuration Files

- `docker/.env` - Environment variables for Docker deployment
- `docker/service_conf.yaml.template` - Backend service configuration
- `pyproject.toml` - Python dependencies and project configuration
- `web/package.json` - Frontend dependencies and scripts

## Testing

- **Python**: pytest with markers (p1/p2/p3 priority levels)
- **Frontend**: Jest with React Testing Library
- **API Tests**: HTTP API and SDK tests in `test/` and `sdk/python/test/`

## Database Engines

RAGFlow supports switching between Elasticsearch (default) and Infinity:

- Set `DOC_ENGINE=infinity` in `docker/.env` to use Infinity
- Requires container restart: `docker compose down -v && docker compose up -d`

## Development Environment Requirements

- Python 3.10-3.12
- Node.js >=18.20.4
- Docker & Docker Compose
- uv package manager
- 16GB+ RAM, 50GB+ disk space

## Known issues & post-mortems

- [PDG demo postmortem](docs/known-issues/pdg-demo-postmortem.md) — caches stale, `is_tools=False` default, Ollama tool-call parsing bug, RBAC 403 on citation clicks, etc. Read before debugging the agentic flow.
- [Multi-tenant SaaS roadmap](docs/roadmap-multitenant-saas.md) — game-changers à activer (MCP, Categorize, GraphRAG, Webhook/Cron) + manquants critiques pour scaler (analytics, audit RGPD, schema validation, CI agents).

## Custom B2B SaaS Multi-Tenant Layer

This fork adds a multi-tenant RBAC system (workspaces, organizations, roles) on top of upstream RAGFlow's single-tenant model.

### Critical upstream assumption: `user_id == tenant_id`

In upstream RAGFlow, `Tenant.id = User.id` (set in `api/db/init_data.py`). Our workspace tenants break this — a workspace has its own `tenant_id` that is NOT a user_id. **If upstream ever changes the user_id == tenant_id mapping, all our workspace logic must be revisited.**

### Custom files to watch on upstream merges

These files contain custom multi-tenant code that will likely conflict with upstream changes:

| File | What's custom |
|------|--------------|
| `api/common/check_team_permission.py` | Workspace membership check (WsMemberService) |
| `api/db/joint_services/tenant_model_service.py` | Workspace → personal tenant model fallback |
| `api/apps/extensions/rbac.py` | Entire RBAC module (roles, permissions, groups) |
| `api/utils/tenant_context.py` | Workspace-aware tenant resolution via X-Workspace-Id |
| `api/db/services/workspace_service.py` | Custom workspace/org DB services |
| `api/ragflow_server.py` | before_request middleware caching X-Workspace-Id |
| `web/src/utils/request.ts` | X-Workspace-Id header injection in interceptor |
| `web/src/components/image/index.tsx` | Authenticated image fetch (blob URL) |
| `web/src/layouts/components/workspace-switcher.tsx` | Workspace switcher component |

### Go server — upstream migration watch

Upstream is actively migrating routes from Python to Go (8+ PRs since we froze: search CRUD,
datasets update, file lookup, models). Every new Go handler arrives **without** workspace tenant
isolation. On every upstream merge that touches `internal/`:

1. **Grep for new `user.ID` usages in handlers:**
   ```
   grep -rn "user\.ID" internal/handler/*.go
   ```
2. **Any new handler using `user.ID` for data-scoping must be changed to `GetTenantID(c)`.**
   - Exception OK: `Accessible(kbID, user.ID)` access-check calls — these verify per-user KB visibility.
   - Exception OK: `memory.go` comparison `GetTenantID(c) != user.ID` — detects workspace mode.
   - **DANGER — listing ops**: When a service calls `GetTenantIDsByUserID(userID)` or `GetJoinedTenantsByUserID(userID)` with no `ownerIDs`, it leaks data from ALL the user's workspaces. Fix: in the handler, before calling the service, inject the active workspace tenant when `ownerIDs` is empty:
     ```go
     if len(ownerIDs) == 0 {
         if tid := GetTenantID(c); tid != user.ID {
             ownerIDs = []string{tid}
         }
     }
     ```
     Already applied to: `ListKbs` (kb.go), `ListDatasets` (datasets.go).
   - Ownership/create ops (`CreateXxx`, `UpdateXxx`, `DeleteXxx`) must use `GetTenantID(c)`.
3. **New router groups added to `authorized` automatically get workspace isolation** via the
   single `authorized.Use(middleware.NewWorkspaceMiddleware().Resolve())` line — no action needed
   unless upstream adds a new top-level group outside `authorized`.

Custom Go files (never conflict upstream):
| File | What's custom |
|------|--------------|
| `internal/middleware/workspace.go` | X-Workspace-Id → tenant_id resolution, 403 if missing |
| `internal/dao/workspace.go` | Read-only access to workspace/ws_member/org_member tables |

Upstream Go files with our one-line touch-point:
| File | Change |
|------|--------|
| `internal/router/router.go` | `authorized.Use(middleware.NewWorkspaceMiddleware().Resolve())` |
| `internal/handler/common.go` | `GetTenantID(c)` helper added |

### Merge procedure

On every upstream merge, grep for `_fallback_personal_tenant_id`, `WorkspaceService`,
`WsMemberService`, `X-Workspace-Id`, `active_tenant_id`, `OrgMemberService` to identify
Python conflict zones. For Go, grep `user\.ID` in `internal/handler/` as described above.

#### Step-by-step checklist (distilled from 2026-04-20 merge)

**Before starting:**
```bash
# Find the true merge base — upstream sometimes rebases, so HEAD..origin/main
# can show 100+ commits when only ~10 are genuinely new.
# Use the last known-merged commit as base:
git log --oneline HEAD | grep -i "merge.*upstream"   # find last merge commit
git diff <last_merge_base_sha>..origin/main --stat    # true diff
```

**Launch the merge:**
```bash
git checkout -b merge/upstream-$(date +%Y-%m-%d)
git merge origin/main --no-commit   # stop before auto-commit to resolve conflicts
```

**Resolve conflicts — file-by-file rules:**

| File / pattern | Rule |
|---|---|
| `internal/handler/*.go` — `user.ID` vs `GetTenantID(c)` | **Always keep `GetTenantID(c)`** (except `Accessible()` checks and `memory.go` comparison) |
| `internal/handler/kb.go` — `ListKbs`, `DeleteKB` | **Keep ours** — custom workspace-scoped routes |
| `internal/service/tenant.go` — `GetModels`/`SetModels` | **Exception: use `user.ID`** — `ListTenantDefaultModels` calls `GetInfoByUserID` which needs a real user ID |
| `internal/service/tenant.go` — model type names | Take upstream renames (`"llm"→"chat"`, `"image2text"→"vision"`, new `"ocr"`) |
| `rag/svr/task_executor.py` | **Keep ours** — `_embed_insert_pipelined`, `set_progress` throttle, tenant limiter release. Plus a 5-line K8s injection (search `CUSTOM B2B SaaS — K8s` to find both sites): an `import` line and the `touch_heartbeat` / `should_recycle` calls inside `report_status`. The actual logic lives in `rag/svr/_k8s_runtime.py` (custom file, no upstream conflicts) |
| `rag/llm/embedding_model.py` | **Keep ours** — `np.vstack(batches)` is O(n); upstream's `np.concatenate` loop is O(n²) |
| `api/utils/api_utils.py` | **Keep ours** — workspace tenant resolution in `token_required` |
| `api/apps/system_app.py`, `api/apps/api_app.py` | **Keep ours** — token routes scoped to `active_tenant_id()` |
| `api/apps/document_app.py`, `api/apps/sdk/doc.py` | **Keep ours** — workspace permission checks on routes |
| `common/doc_store/infinity_conn_pool.py` | **Keep ours** — pool auto-sizing from `WORKER_MAX_TASKS` |
| `common/settings.py` | **Keep ours** — `DOC_BULK_SIZE=128`, `EMBEDDING_BATCH_SIZE=512` |
| `rag/utils/redis_conn.py` | **Keep ours** — Redis connection pool |
| `api/db/services/task_service.py` | **Keep ours** — per-tenant DB lock `lock_key = f"get_task:{tenant_id}"` |
| `web/src/components/image/index.tsx` | **Keep ours** — authenticated image fetch with `Authorization` header |
| `internal/entity/model.go` — `Provider` struct | Take upstream (`URL map[string]string` for multi-region, `Tags` not in JSON configs) |
| `internal/cli/*.go` | Take upstream (new CLI commands, no workspace concerns) |
| `api/apps/restful_apis/document_api.py` | Take upstream (new RESTful `list_docs` route + imports) |
| `web/src/utils/api.ts`, `use-rename-document.ts` | Take upstream (RESTful URL functions, `dataset_id` rename) |
| `web/src/services/knowledge-service.ts` — `listDocument` | Take upstream (RESTful GET) |
| `web/src/services/knowledge-service.ts` — `uploadDocument` | **Keep ours** — `X-Workspace-Id` header |
| Test files | Take upstream |

**After resolving:**
```bash
go build ./internal/...          # must be zero errors before committing
# Smoke import: catches silent import losses from auto-merge (e.g. an
# `import concurrent.futures` we relied on can disappear if upstream
# reorders the imports around it).
PYTHONPATH=. uv run python -c "import rag.svr.task_executor, api.asgi, api.apps"
git add -A
PATH=/opt/homebrew/bin:$PATH git commit   # homebrew PATH needed for pre-commit hook (npx)
git checkout dev && git merge merge/upstream-$(date +%Y-%m-%d) --no-ff
```

**Run the full test suite before merging to dev** (distilled from 2026-05-15 merge — combining test dirs in a single pytest invocation corrupts Python module resolution, so launch each separately):

```bash
# 1. Wipe Infinity volume if upstream bumped the version (nightly format breaks)
#    Symptom: container Up Restarting, logs show "Segmentation fault" during WAL Replay.
docker stop docker-infinity-1 && docker rm docker-infinity-1 \
  && docker volume rm docker_infinity_data \
  && bash scripts/dev_up.sh --full

# 2. Canonical pytest env (RSA + ADMIN_JWT_SECRET MUST come from .env.local,
#    not dev_up.sh — dev_up exports a placeholder which .env.local overwrites
#    at server boot, so the placeholder rejects management-panel tokens).
REAL_JWT=$(grep "^ADMIN_JWT_SECRET=" .env.local | cut -d= -f2-)
export RAGFLOW_TEST_LOCAL_AUTH=1 RSA_PASSPHRASE=Welcome
export ADMIN_JWT_SECRET="$REAL_JWT"
export VIEWER_EMAIL=viewer.internal@cyllene.com EDITOR_EMAIL=editor.internal@cyllene.com
export HOST_ADDRESS=http://127.0.0.1:9380 ZHIPU_AI_API_KEY=dummy PYTHONPATH=.
# 3. Run each dir SEPARATELY — combining triggers Python module shadowing
#    (notably `infinity` SDK gets shadowed → 30+ collection errors).
uv run python -m pytest test/multitenant \
  && uv run python -m pytest test/unit_test \
       --ignore=test/unit_test/agent/sandbox/test_local_provider.py \
  && uv run python -m pytest test/multitenant_http_api
```

Expected baseline (2026-05-15): 2132 passed / 206 skipped / 9 failed. The 9
fails are EPUB tests that pass in isolation — a pre-existing test isolation
issue where Python loses the `infinity` package when certain imports precede.
The 164 skips in multitenant_http_api are upstream `@pytest.mark.skipif(DOC_ENGINE == "infinity")` markers on known Infinity bugs they have decided to skip (issues/6104, issues/5851, issues/6509, #8208, …). We inherit those — not a coverage hole on our side.

**Excluded test directories** (and why):
- `test/playwright` — browser E2E, separate runner (Playwright)
- `test/benchmark` — perf benchmarks, not correctness
- `test/testcases` — upstream QA suite with a conflict between `test_http_api/common.py` and `test_sdk_api/common.py` that breaks collection (`ImportError: cannot import name 'delete_all_chats'`). Fixable by either renaming or `__init__.py`-ing each `common.py`; out of scope for the merge itself.
- `test/unit_test/agent/sandbox/test_local_provider.py` — Tablestore native C extension wrong arch on macOS arm64 (`slice is not valid mach-o file`); collection error, not a real failure.

**Watch for these upstream regressions (reject silently):**
- Removing our `_embed_insert_pipelined` or reverting to sequential embed→insert
- Removing `set_progress` throttling (1 UPDATE/s per task)
- Changing `GetTenantID(c)` back to `user.ID` in any handler
- Hardcoding pool sizes (Infinity `"4"`, Redis no pool)
- `np.concatenate` in a loop in `embedding_model.py` (O(n²))
- Bumping Infinity image (`docker/docker-compose-base.yml` + `pyproject.toml`) — nightly format breaks the local WAL silently. Our Helm chart pins `dev5` explicitly; **do not** propagate upstream bumps to `helm/ragflow/values.yaml` without a migration plan.

**Watch for these upstream breaking changes (require full audit):**
- Any change to `user_id == tenant_id` invariant in `api/db/init_data.py`
- New top-level route group added outside `authorized` in `router.go` (won't get workspace middleware)
- Rename of `GetInfoByUserID` in `internal/dao/tenant.go` (breaks `ListTenantDefaultModels`)
- Any new Langfuse-related route or service that uses `user_id` instead of `active_tenant_id()` — would leak workspace traces into a wrong project. See [docs/roadmap-multitenant-saas.md](docs/roadmap-multitenant-saas.md) Tier 3 prerequisites.
- **Service-layer `get_joined_tenants_by_user_id(tenant_id)` anti-pattern** — upstream refactors regularly introduce a service function that takes `tenant_id` (which in our fork is the **workspace tenant_id**, not a user_id) and feeds it to `TenantService.get_joined_tenants_by_user_id(...)`. The lookup returns empty for workspace tenants → service silently returns 0 rows. Caught twice in the 2026-05-15 merge: `dataset_api_service.list_datasets` (broke MCP + browser dataset list) and `knowledgebase_service.accessible` (broke a unit test for team-dataset access). The `test/multitenant/test_perf_invariants.py::TestUpstreamJoinedTenantsMisuse` AST test flags new occurrences automatically — if it fails after a merge, the fix is either (a) workspace-strict scoping `tenant_ids = [tenant_id]` (see `dataset_api_service.list_datasets`) or (b) adopt the upstream impl as-is when the parameter is actually named `user_id` and both meanings work (see `knowledgebase_service.accessible`).
