# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Tool Usage

Use the fff MCP tools (`mcp__fff__grep`, `mcp__fff__find_files`, `mcp__fff__multi_grep`) for all file search operations instead of default Grep/Glob tools.

## Project Overview

RAGFlow is an open-source RAG (Retrieval-Augmented Generation) engine based on deep document understanding. It's a full-stack application with:
- Python backend (Flask-based API server)
- React/TypeScript frontend (built with UmiJS)
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
- React/TypeScript with UmiJS framework
- Ant Design + shadcn/ui components
- State management with Zustand
- Tailwind CSS for styling

## Common Development Commands

### Backend Development
```bash
# Install Python dependencies
uv sync --python 3.12 --all-extras
uv run download_deps.py
pre-commit install

# Start dependent services
docker compose -f docker/docker-compose-base.yml up -d

# Run backend (requires services to be running)
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh

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
