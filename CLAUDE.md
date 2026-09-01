# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Tool Usage

Use the fff MCP tools (`mcp__fff__grep`, `mcp__fff__find_files`, `mcp__fff__multi_grep`) for all file search operations instead of default Grep/Glob tools.

## Project Overview

RAGFlow is an open-source RAG (Retrieval-Augmented Generation) engine based on deep document understanding. It's a full-stack application with:

- Python backend (Quart-based async API server — Quart is the async reimplementation of Flask)
- React/TypeScript frontend (built with vitejs)
- Background task executor workers (separate Python processes, Redis-queue-driven)
- Peewee ORM for database models (not SQLAlchemy)
- Multiple data stores (MySQL/PostgreSQL, Elasticsearch/Infinity/OpenSearch/OceanBase, Redis, MinIO)

## Architecture

### Runtime Architecture

RAGFlow runs as **two separate Python process types**, orchestrated by `docker/launch_backend_service.sh`:

- **API Server** (`api/ragflow_server.py`): Quart-based async HTTP server
- **Task Executors** (`rag/svr/task_executor.py`): Background workers processing documents from Redis streams. Multiple instances run in parallel (controlled by `WS` env var). Each consumes from priority-ordered Redis streams (`te.1.common`, `te.0.common`), using consumer groups for load distribution.

Key consequence: task executors import a different code surface than the API server, so always check which process a module is meant for.

### Backend API (`/api/`)

- **App factory**: `api/apps/__init__.py` — creates the Quart app, configures auth (`login_required` decorator, JWT + API token + session fallback), and dynamically discovers/registers blueprints
- **Two API coexisting patterns**:
  - **RESTful APIs** in `api/apps/restful_apis/` — newer pattern with Pydantic request validation, service layer in `api/apps/services/`, routes registered under `/api/v1`
  - **Legacy APIs** in `api/apps/*_app.py` — older pattern using `@validate_request()`, routes registered under `/v1/<page_name>`
  - **SDK APIs** in `api/apps/sdk/` — registered under `/v1/`
- **Services**: `api/db/services/` — business logic wrapping Peewee model operations. `api/apps/services/` — service layer for the RESTful APIs
- **Models**: `api/db/db_models.py` — Peewee ORM models with pooled MySQL/PostgreSQL connections, custom `JSONField`/`ListField` types, retry logic on connection loss

### Core Processing (`/rag/`)

- **Document ingestion pipeline**: `rag/flow/pipeline.py` — `Pipeline` (extends `agent.canvas.Graph`) orchestrates the ingestion DAG. Components: File (fetches binary from storage), Parser (dispatches to `deepdoc.parser` based on file type), TokenChunker/TitleChunker (splits into chunks), Tokenizer (computes full-text tokens + embedding vectors), Extractor (LLM-based extraction). Data flows via Pydantic `*FromUpstream` schemas.
- **Document parsing**: `deepdoc/` — PDF parsing (vision-based OCR, layout analysis, table structure recognition) and format-specific parsers (DOCX, XLSX, PPT, Markdown, HTML, images). All parsers normalize to a common structure (list of bbox dicts for PDFs, `{text, doc_type_kwd}` for others).
- **DeepDoc HTTP API service** (`deepdoc/server/`): OSS ONNX models (DLA, OCR, TSR) wrapped with LitServe as a standalone HTTP API on port 8124. The Go parser (`internal/parser/`) calls this service via `DeepDocClient`. Endpoints: `GET /health`, `GET /model`, `POST /predict/dla`, `POST /predict/tsr`, `POST /predict/ocr` (with `operator=det` or `operator=rec` form field). Docker image: `deepdoc_oss:latest`. See `deepdoc/server/README.md` for the full API reference.
- **LLM Integration**: `rag/llm/` — factory pattern with runtime class discovery. `chat_model.py` (30+ providers via OpenAI SDK and LiteLLM wrappers), `embedding_model.py`, `rerank_model.py`, `cv_model.py` (image-to-text), `sequence2txt_model.py` (ASR), `tts_model.py`. Use `LLMBundle` (from `api.db.services.llm_service`) as the unified interface.
- **Graph RAG**: `rag/graphrag/` — multi-phase pipeline: per-document subgraph extraction (LLM or spaCy NER), Leiden community detection, entity resolution, community summarization. Entities/relations/reports are indexed as chunks alongside regular text chunks, differentiated by `knowledge_graph_kwd`.
- **Search**: `rag/nlp/search.py` — `Dealer` class combines vector similarity + BM25 + re-ranking. `KGSearch` extends it for graph-aware retrieval (entity resolution, n-hop enrichment).

### Agent System (`/agent/`)

- **Execution engine**: `agent/canvas.py` — `Canvas` (extends `Graph`) executes the DAG. Components are run in topological order via `_run_batch`, each receiving upstream outputs as kwargs. Control-flow components (`Categorize`, `Switch`, `Iteration`, `Loop`) dynamically modify the execution path.
- **Component base**: `agent/component/base.py` — `ComponentBase` with `invoke(**kwargs)` / `invoke_async(**kwargs)` lifecycle. Variable references (`{component_id@output_var}` or `{sys.query}`) are resolved from the canvas graph at runtime.
- **Components**: Modular workflow components in `agent/component/` — Begin, LLM, Agent (tool-calling LLM), Categorize, Switch, Iteration, Loop, Message, Invoke (HTTP), and data manipulation nodes. Auto-discovered by `__init__.py`.
- **Templates**: Pre-built agent workflows as JSON DSL files in `agent/templates/`. Each contains a complete `components` DAG, `path`, and `globals`.
- **Tools**: `agent/tools/` — Retrieval, web search (DuckDuckGo, Google, Tavily, SearXNG), academic search (ArXiv, PubMed, Google Scholar, Wikipedia), code execution, SQL execution, email, GitHub, finance data, translation, weather. Tools implement `ToolBase` (extends `ComponentBase`) and produce OpenAI-compatible function descriptors.
- **Plugins**: `agent/plugin/` — plugin system using `pluginlib` for loading external LLM tool plugins from `embedded_plugins/`.

### Frontend (`/web/`)

- React/TypeScript with vitejs framework
- shadcn/ui components (Radix UI primitives + Tailwind CSS)
- `@tanstack/react-query` for server state (cache keys, mutations, invalidation)
- Zustand for local state (primarily agent canvas graph store)
- `react-router` v7 with lazy-loaded pages
- `react-i18next` for i18n (17 languages)
- Axios for HTTP with a layered pattern: endpoint definitions (`utils/api.ts`) → HTTP client (`utils/next-request.ts`) → service layer (`services/`) → query hooks (`hooks/use-*-request.ts`) → components
- `@xyflow/react` for the agent workflow canvas
- `react-hook-form` + `zod` for form validation
- Two API proxy prefixes: `webAPI = '/v1'` (legacy) and `restAPIv1 = '/api/v1'` (RESTful)

## Common Development Commands

### Backend Development

```bash
# Install Python dependencies
uv sync --python 3.13 --all-extras
uv run python3 ragflow_deps/download_deps.py
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
# Full stack with Docker (includes deepdoc vision service)
cd docker
docker compose -f docker-compose.yml up -d

# Check server status
docker logs -f ragflow-server

# Build the OSS deepdoc vision service standalone
docker build -f docker/Dockerfile_deepdoc_oss -t deepdoc_oss:latest .
docker run -p 8124:8124 deepdoc_oss:latest

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

## Account Password Handling (Critical for Login Flow)

### Password Encryption Pipeline (Browser → Backend → DB Hash)

The login password verification chain is counterintuitive. Understanding this is essential when generating or verifying password hashes.

**Complete flow:**

```
Browser input: "demo"
  → Base64("demo") = "ZGVtbw=="
  → RSA encrypt with conf/public.pem
  → POST to /api/v1/auth/login

Backend DecryptPassword():
  → RSA decrypt with conf/private.pem (passphrase: "Welcome")
  → Returns "ZGVtbw=="  (NOT "demo"!)

VerifyPassword("ZGVtbw==", storedHash)  ← hash is of Base64(password), not raw password
```

**Consequences:**
- The string verified against the hash is **Base64(original password)**, never the raw password
- `DecryptPassword()` handles both RSA-encrypted (browser) and plaintext (curl/API key) inputs: if base64 decode fails, the input is returned as-is for backward compatibility
- Python backend has the same design: `api/utils/crypt.py:decrypt()` RSA-decrypts and returns the Base64-encoded string directly, no further decode

### How to Generate a Valid Password Hash

```bash
# For password "demo" (user input in browser):
# The actual verified string = Base64("demo") = "ZGVtbw=="
# Generate hash with: common.GenerateWerkzeugPasswordHash("ZGVtbw==")
# or use the scrypt template:
# scrypt:32768:8:1$<random-b64-salt>$<hex-hash-of-ZGVtbw==>
```

**To update a user's password in the running database:**
```bash
docker exec docker-mysql-1 mysql -u root -pinfini_rag_flow rag_flow \
  -e "UPDATE user SET password='<hash>' WHERE email='<email>';"
```

### RSA Keys
- `conf/public.pem` — frontend uses this to encrypt Base64(password) before sending
- `conf/private.pem` — backend uses this to decrypt, passphrase `"Welcome"`
- Both referenced in `internal/common/password.go:DecryptPassword()`

### Obtaining an API Token for a Tenant

When testing APIs manually (curl, Go scripts, etc.), you need a valid auth token. The login endpoint returns **two different tokens**:

| Field | Format | Purpose |
|-------|--------|---------|
| `response.body.data.access_token` | Raw UUID | Stored in DB, NOT used for API auth |
| `response.Header["Authorization"]` | itsdangerous-signed token | Used as `Bearer <token>` for all subsequent API requests |

**How to obtain the correct token:**

```bash
# Step 1: Construct the encrypted password
# Raw password → Base64 → RSA encrypt with conf/public.pem
PASSWORD="demo"
PASSWORD_B64=$(echo -n "$PASSWORD" | base64)

# Step 2: POST to login (use RSA encryption — easiest via a Go/Python script)
# Response header contains: Authorization: <itsdangerous-signed-token>

# Step 3: Use the Authorization header value for all API requests
curl -H "Authorization: <itsdangerous-signed-token>" \
  http://127.0.0.1:9222/api/v1/agents
```

**Go snippet (complete login + token extraction):**

```go
// Login
passwordB64 := base64.StdEncoding.EncodeToString([]byte(password))
pubData, _ := os.ReadFile("conf/public.pem")
block, _ := pem.Decode(pubData)
pubKey, _ := x509.ParsePKIXPublicKey(block.Bytes)
ciphertext, _ := rsa.EncryptPKCS1v15(rand.Reader, pubKey.(*rsa.PublicKey), []byte(passwordB64))
encryptedB64 := base64.StdEncoding.EncodeToString(ciphertext)

body, _ := json.Marshal(map[string]string{"email": email, "password": encryptedB64})
resp, _ := http.Post(baseURL+"/api/v1/auth/login", "application/json", bytes.NewReader(body))

// KEY: use the Authorization header, NOT body.access_token
authToken := resp.Header.Get("Authorization")

// Use for API calls
req, _ := http.NewRequest("GET", baseURL+"/api/v1/agents", nil)
req.Header.Set("Authorization", authToken)
```

**The raw `access_token` (UUID) in the response body** is the internal DB token used only by the `itsdangerous` middleware to verify the signed token — it is never passed directly in API Authorization headers.

---

## Agent Run E2E Tests

### Running the Tests

```bash
# Run all agent run e2e tests (in-memory SQLite + miniredis, no Docker needed)
cd /home/zhichyu/github.com/infiniflow/ragflow
go test -count=1 -v -run 'TestRunAgent_RealCanvas|TestRunAgent_RunTracker' ./internal/service/
```

### Test Architecture

All e2e tests live in `internal/service/agent_run_e2e_test.go`. They exercise the full production chain:

```
loadCanvasForUser → versionDAO.GetLatest → decodeCanvasFromDSL →
canvas.Compile → cc.Workflow.Invoke → answer extraction
```

**Test isolation**: Each test stands up its own in-memory SQLite DB (pushed as `dao.DB`), seeds User/Tenant/UserCanvas/UserCanvasVersion rows, and tears down in `t.Cleanup`. Tests use **miniredis** for Redis-backed CheckPointStore + RunTracker — no external services needed.

**Key test helpers:**
- `makeCanvasWithDSL(t, canvasID, userID, tenantID, versionID, dsl)` — seeds all required DB rows
- `drainAgentEvents(t, events)` — drains the `<-chan canvas.RunEvent` channel, buckets results into `messages`, `waiting`, `errors_`, `done`
- `newRunTrackerForTest(t, ttl)` — wires a `canvas.RunTracker` against in-memory miniredis

**Existing e2e tests:**

| Test | What it covers |
|------|---------------|
| `TestRunAgent_RealCanvas_BeginMessage` | Happy path: Begin→Message, verifies `"{{sys.query}}"` resolution |
| `TestRunAgent_RealCanvas_WaitForUserResume` | Resume path: Begin→Message→UserFillUp, two-run cycle |
| `TestRunAgent_RealCanvas_CompileFails` | Error path: unknown component name → sanitized error |
| `TestRunAgent_RealCanvas_InvokeFails` | Error path: unresolvable template ref |
| `TestRunAgent_RunTracker_AttachCheckpoint_CallSequence` | Production boot: Start→AttachCheckpoint→MarkSucceeded with Redis/miniredis |

**Test DSL data files** are in `internal/agent/dsl/testdata/`:
- `agent_msg.json` — Agent+Message with Begin, LLM-powered agent component
- `all.json` — Complex: Begin→UserFillUp→Switch→Loop→Message
- `switch.json`, `resume.json`, `browser.json`, `subagent.json`, etc.

**Handler-level SSE streaming tests** in `internal/handler/agent_test.go` use a `stubChatRunner` that emits pre-configured `canvas.RunEvent` values without a real DB or eino runner, verifying:
- SSE `Content-Type: text/event-stream`
- `data: {...}\n\n` framing
- Trailing `data: [DONE]\n\n` terminator
- OpenAI-compatible non-stream `choices` response shape

**Important**: `_ "ragflow/internal/agent/component"` (blank import in test) is required — it triggers `init()` to register all component factories. Without it, `canvas.Compile` fails to resolve any component type.

---

## Development Environment Requirements

- Python 3.10-3.13
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

### Model storage: dual-write (`tenant_llm` + `TenantModelProvider/Instance/Model`)

Upstream 2026-06-02 introduced a 3-level hierarchy for model storage: `tenant_model_provider` → `tenant_model_instance` → `tenant_model` (+ `tenant_model_group`, `tenant_model_group_mapping`). We **migrated** to it on 2026-06-03 by running `tools/scripts/mysql_migration.py --stages tenant_model_provider,tenant_model_instance,tenant_model,model_id_config --execute`. The migration is idempotent (`INSERT IGNORE` semantics) — safe to re-run.

**Both schemas are now kept in sync** by the admin panel:
- `tenant_llm` (legacy) — STILL written by `management/server/routers/models.py`. Required because `TenantLLMService.get_api_key()` is still used at inference time (token usage tracking, `LLMBundle` lookups, etc.).
- `tenant_model_provider/instance/model` (new) — written via `management/server/services/sync_tenant_model_tables.py::sync_tenant_llm_to_new_tables(tenant_id, llm_factory)` after every mutation. Required because upstream's `/v1/models`, `/v1/models/default`, and `get_model_config_from_provider_instance` read from these tables exclusively.

**`tenant.llm_id` / `embd_id` / etc. fields** store the 3-part format `name@instance@provider` post-migration (e.g. `nomic-embed-text@default@Ollama`). Upstream's `_get_model_info` accepts both 2- and 3-part — we standardize on 3-part for new writes.

**On upstream merges that touch model handling:**
1. **Do NOT** re-introduce a "fallback to `tenant_llm` when new tables are empty" pattern in `api/apps/services/models_api_service.py` — the new tables are authoritative now.
2. **Do NOT** drop the `legacy_id` lookup in `api/db/joint_services/tenant_model_service.py::get_model_config_from_provider_instance` — `LLMBundle.encode()` / `chat()` etc. still need `model_config["id"]` for `TenantLLMService.increase_usage_by_id` (token tracking). Upstream forgot to rewire this when they refactored the function.
3. **Verify after merge**: `management/server/services/sync_tenant_model_tables.py` is still imported in the 5 mutation routes of `management/server/routers/models.py` (add / update / toggle-status / delete provider, set defaults).
4. **When upstream rewires usage tracking to the new tables** (`tenant_model.used_tokens` or similar), THIS is the trigger to drop the dual-write: stop writing `tenant_llm`, remove the `legacy_id` injection, write only to the new tables.

The custom helper is the entire mirror logic in ~150 lines and survives upstream model-layer churn without per-merge edits — much better than the previous fallback approach.

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
| `web/src/hooks/use-warn-empty-model.tsx` + `web/src/routes.tsx` + `web/src/hooks/logic-hooks/navigate-hooks.ts` | Modal "modèle manquant" → message "contactez votre administrateur" (clé i18n `modelProvidersWarnAdmin`, en+fr) au lieu du lien vers Settings > Model providers. Route `/user-setting/model` volontairement NON enregistrée + `navigateToModelSetting` supprimé (un merge upstream qui le rappelle doit casser le build). Grep `CUSTOM B2B SaaS — model config is admin-panel-only` |
| `rag/llm/chat_model.py` | `_extract_reasoning()` helper + 6 call sites — vLLM 0.23 renommé `reasoning_content` → `reasoning`, tombe dans `model_extra` du SDK openai. Grep `CUSTOM B2B SaaS — vLLM 0.23 reasoning` |
| `rag/llm/embedding_model.py` — `OpenAI_APIEmbed` + `rag/llm/rerank_model.py` — `CoHereRerank._compute_rank` | Troncature conservatrice sur le chemin vLLM : la troncature amont compte en tiktoken (cl100k) mais bge-m3/reranker comptent en sentencepiece → un texte à 8191 tiktoken dépasse 8192 côté modèle → vLLM 400 "value=8193", docs FAIL à l'embed ET reranking cassé (2026-08-27, benchmark CRAG). Override `encode`/`encode_queries` (cap 7000, env `VLLM_EMBED_TRUNCATE_TOKENS`) + troncature docs rerank (cap 6000, env `VLLM_RERANK_TRUNCATE_TOKENS`). Cause profonde = chunks > 8192 tokens créés à l'ingestion (adaptive_chunk ne cape pas au tokenizer du modèle) ; la troncature est le garde-fou aval. Grep `CUSTOM B2B SaaS — vLLM embed truncate margin` et `vLLM rerank truncate margin` |
| `api/utils/api_utils.py` | Lazy import de `common.mcp_tool_call_conn` (dans `get_mcp_tools`) au lieu du top-level upstream — sinon cascade dans le mgmt-backend qui n'a pas `mcp` dans son Dockerfile. Grep `CUSTOM B2B SaaS — lazy MCP import` |
| `api/apps/restful_apis/dataset_api.py` — `check_dataset_embedding` | Route `POST /datasets/<id>/embedding/check` ajoutée : le front upstream l'appelle mais upstream ne l'a implémentée que côté serveur Go (non déployé chez nous) → 404 visible au changement d'embedding d'un dataset (2026-09-01). Le service Python `dataset_api_service.check_embedding` est upstream ; seule la route est à nous. Si un merge upstream ajoute sa propre route Python équivalente, prendre la leur et re-brancher permission + offload. Grep `CUSTOM B2B SaaS — route absente de l'API Python` |
| `rag/app/naive.py` — branche `.xml` + `_xml_to_flat_lines` | Support XML dans le chunker General : l'upload accepte .xml (filename_type → DOC) mais upstream n'a AUCUNE branche de parsing → NotImplementedError au parse. Aplati « chemin/balise@attr: texte », découpe TxtParser, invalide → texte brut. Si upstream ajoute sa propre branche xml, prendre la leur. Pin `test/multitenant/test_xml_support.py`. Grep `CUSTOM B2B SaaS — support XML` |
| `rag/app/picture.py` | OCR paresseux (`_get_ocr()` au lieu de `ocr = OCR()` module-level) — l'import au boot des pods api (chaîne agent_api → rag.flow.pipeline → figure_parser → picture) chargeait ~150-300 Mo de sessions ONNX det+rec jamais utilisées par l'api. Pin AST `test_no_module_level_model_instantiation_in_rag_app` (interdit OCR/LayoutRecognizer/TSR module-level sous rag/app). Grep `CUSTOM B2B SaaS — OCR paresseux` |
| `api/apps/__init__.py` — hooks request | Slow-request log : WARNING `SLOW <id> <méthode> <path> -> <status> in Nms` pour toute requête > `SLOW_REQUEST_LOG_MS` (défaut 500, 0 = off) — boussole d'optimisation latence. Grep `CUSTOM B2B SaaS — slow-request log`. Le cache compagnon (résolution X-Workspace-Id → tenant_id, TTL `WORKSPACE_RESOLVE_CACHE_TTL_S` défaut 30 s, positifs seulement, refus jamais cachés) vit dans `api/utils/tenant_context.py` (fichier custom), pin `test/multitenant/test_ws_resolve_cache.py` |
| `api/db/services/task_service.py` | Lazy import de `deepdoc.parser.PdfParser` + `RAGFlowExcelParser` (dans `queue_tasks`) — même raison que api_utils, mgmt-backend n'a pas deepdoc dans son image slim. Grep `CUSTOM B2B SaaS — lazy deepdoc import` |
| `api/db/services/file_service.py` | Lazy imports de `api.utils.file_utils` (pdfplumber) + `rag.llm.cv_model.GptV4` (openai). Utilisés uniquement dans upload_document/parse/upload_info, jamais appelées depuis mgmt. Grep `CUSTOM B2B SaaS — lazy imports pour découpler` |
| `common/metadata_utils.py` | Lazy import de `json_repair` (dans `update_metadata_to`) — atteint par mgmt via doc_metadata_service → document_service → file_service. Grep `CUSTOM B2B SaaS — json_repair lazy-imported` |
| `rag/app/paper_fast.py` + `FACTORY["paper_fast"]` dans task_executor.py | Nouveau mode chunk_method "paper_fast" (variante paper avec skip auto-rotate tables). Coexiste avec "paper" upstream (inchangé). Sélectionnable par KB dans admin panel. ~50% du temps TSR économisé. Duplication de `paper.chunk()` — resync si upstream change. |
| `api/apps/sdk/doc.py:184` + `api/utils/validation_utils.py:468` | `"paper_fast"` ajouté au set `valid_chunk_method` pour que le SDK/validator accepte ce nouveau mode. |
| `web/src/hooks/use-user-setting-request.tsx:118` + `web/src/components/chunk-method-dialog/hooks.ts:12` | `"paper_fast"` ajouté à la liste des chunk methods sélectionnables dans l'UI (KB config). |
| `api/db/services/document_service.py:clear_chunk_num_when_rerun` | Reset AUSSI `doc.token_num`/`chunk_num` (upstream décrémente juste le KB, pas le doc → compteur doublé au re-parse). Bug upstream 2026-07-07. |
| `rag/nlp/search.py` — `Dealer._consolidate_children_to_moms` + hook dans `retrieval()` + `retrieval_by_children` (max) | **Fix parent-child retrieval** : consolidation enfants→PARENTS AVANT le scoring — l'upstream rerankait/sélectionnait sur les fragments enfants puis résolvait les parents trop tard (−7,6 pts appariés CRAG 2026-08-28). Matching = enfants (précision), ranking/reranking = textes de PARENTS, top-k = parents distincts, agrégation = MAX (le mean diluait). Scopé reranker-ou-Infinity (ES/OB sans reranker gardent le flux legacy + filet caller-side). Repli enfants si parent introuvable. Grep `CUSTOM B2B SaaS — parent-child`, pin `test/multitenant/test_parent_child_consolidation.py`. Candidat contribution upstream. |
| `rag/nlp/__init__.py` — `split_with_pattern` | **Parent-child v2 : plancher de taille des enfants.** Sans plancher, chaque fragment devenait un enfant (5 caractères sur split par ligne) → unités de retrieval inutiles + postings dégénérés qui crashent Infinity (#3418). Fusion des fragments jusqu'à `CHILD_MIN_CHARS` (env, défaut 120, 0 = legacy), queue absorbée par le dernier enfant. Grep `CUSTOM B2B SaaS — parent-child v2`, pin `test/multitenant/test_child_min_chars.py` |
| `api/db/services/document_service.py:get_unfinished_docs` | Deux conditions ajoutées au filtre du thread `update_progress` — les états hybrides `progress>=1, run=RUNNING` et `progress=-1, run=RUNNING` (nés sous stress 1226/restarts executors) étaient invisibles À JAMAIS → docs figés RUNNING alors que leurs tâches sont finies (incident 2026-08-28, 144+7 docs ; nudge SQL de secours en mémoire `project_update_progress_blindspots`). Auto-cicatrisant : `_sync_progress` recalcule et rebascule. Grep `CUSTOM B2B SaaS — update_progress blind spots`, pin `test/multitenant/test_update_progress_blindspots.py` |
| `web/src/constants/knowledge.ts:102` + `web/src/pages/dataset/dataset-setting/chunk-method-form.tsx:28` + `.../utils.ts:13` | Enum `DocumentParserType.PaperFast` + mapping vers `PaperConfiguration` (réutilise UI) + `ImageMap.paper_fast` (illustrations). |
| `management/server/services/provisioning.py:29` (`_DEFAULT_PARSER_IDS`) + `common/settings.py:257` (`PARSERS` fallback) | `paper_fast:Paper (fast)` ajouté au default. Le hook UI `useSelectParserList` filtre par `tenant.parser_ids` — sans ça, invisible dans le dropdown même après build. **Migration SQL requise pour tenants existants** (voir memory `mgmt-paper-fast-migration`). |
| `api/apps/restful_apis/{document_api,file_api}.py` + `api/apps/sdk/doc.py` + `rag/utils/minio_conn.py` (`get_stream`) | Téléchargements STREAMÉS via `api/utils/blob_stream.py` (chunks 8 Mo offloadés en thread) — l'ancien pattern upstream (`STORAGE_IMPL.get` sync + `BytesIO` + `send_file`) gelait l'event-loop du pod api pendant tout le download d'un gros fichier et doublait sa RAM. Si un merge upstream réintroduit `file_stream = settings.STORAGE_IMPL.get(...)` dans une route de download, re-brancher le helper. Grep `CUSTOM B2B SaaS — streamed download` |
| `rag/utils/minio_conn.py` (`get_presigned_url` — `endpoint_override`) + `agent/tools/get_file.py` | `agent/tools/get_file.py` (tool `get_file`, code_exec data hand-off) needs to presign URLs reachable from a sandbox-facing MinIO endpoint distinct from the API pod's. `endpoint_override` builds a temporary `Minio` client pointed at that host but stays INSIDE `RAGFlowMinio.get_presigned_url`, after `@use_default_bucket`/`@use_prefix_path` remap `bucket`/`fnm` — so single-bucket/prefix-path deployments still sign the correct physical address. Do not let a merge move the override logic outside those decorators or back into `get_file.py` as a standalone `Minio()` client. Grep `CUSTOM B2B SaaS — get_file presign endpoint override` |
| `api/apps/restful_apis/document_api.py` — `_upload_local_documents` | Check `check_storage_quota` (plafond `max_storage_gb` org) avant sauvegarde des fichiers. Grep `CUSTOM B2B SaaS — storage quota enforcement` |
| `api/apps/restful_apis/openai_api.py` | Ownership du chat + validation LLM scopées `active_tenant_id()` au lieu de `current_user.id` (upstream) — sinon tout chat de workspace renvoie "You don't own the chat" sur `/api/v1/openai/<chat_id>/chat/completions`. Grep `CUSTOM B2B SaaS — scope to the active workspace tenant` |
| `api/apps/restful_apis/chat_api.py` — `_build_default_completion_dialog` | Dialog synthétique (chat direct sans `chat_id`) scoped `active_tenant_id()` au lieu de `current_user.id` — sinon lookup modèles sur le tenant personnel (vide) → "No default chat model for tenant". Même marker grep que openai_api.py. Le reste du fichier utilise déjà `active_tenant_id()` partout. |
| `api/db/db_models.py` (Code product tables) | `CodeEntitlement`/`CodeTeam`/`CodeTeamMember`/`CodeKey` — control plane for the LiteLLM data plane, marked `CUSTOM B2B SaaS — Code product tables`. `management/server/{routers/code.py,services/litellm_client.py,services/code_provisioning.py,services/code_reconcile.py}` are fully custom too but live under `management/` which never conflicts on upstream merge. |
| `agent/sandbox/executor_manager/core/container.py` | Podman-backed hosts reject the `uid=`/`gid=` tmpfs sub-options Docker Engine accepts on `--tmpfs /workspace` and `/tmp` (`"unknown mount option \"uid=65534\": invalid mount option"`), which left the whole container pool at 0/N. Retries once with a `mode=1777` fallback tmpfs (world-writable + sticky bit, both engines accept it) only when that specific Podman error is detected; Docker Engine keeps the nominal `uid=`/`gid=` path. Grep `CUSTOM B2B SaaS — Podman tmpfs compat (POC FAMAT)` |
| `agent/sandbox/sandbox_base_image/python/Dockerfile` | `COPY famat_recipes.py /usr/local/lib/python3.11/site-packages/famat_recipes.py` — bakes the FAMAT POC analytics recipes into the sandbox Python image so `code_exec` can `import famat_recipes`. Grep `CUSTOM B2B SaaS — recettes FAMAT` |
| `agent/sandbox/sandbox_base_image/python/requirements.txt` | `duckdb` + `pymysql` added for the FAMAT POC recipes (in-sandbox CSV/SQL analysis). Plain list, no code marker — see the `# CUSTOM B2B SaaS — duckdb+pymysql pour recettes FAMAT (POC)` comment line above the two packages in the file itself. |
| `agent/sandbox/sandbox_base_image/python/famat_recipes.py` | GENERATED copy of `poc/famat/famat_recipes.py`, baked into the sandbox image via the Dockerfile above. Resync procedure documented in `poc/famat/README.md` — keep both files byte-identical modulo the `# GÉNÉRÉ` header on line 1. |
| `agent/sandbox/client.py` | Provider `k8s` enregistré dans `provider_classes` + import, et dans le set `{"local", "ssh", "k8s"}` qui déclenche `SandboxProviderConfigError` sur un `initialize()` KO (sinon message trompeur "No sandbox provider configured"). Grep `CUSTOM B2B SaaS — provider sandbox k8s` |
| `common/doc_store/infinity_conn_base.py` | Retry borné (backoff+jitter) sur conflit de transaction dans `delete()` + verrous stripés par table + **sémaphore d'écriture distribué par table** (`_table_write_slot`, N slots Redis/valkey, défaut 2, fail-open sans Redis, envs `INFINITY_TABLE_WRITE_SLOTS`/`_TTL_S`). Incidents prod 2026-08-19 : livelock de DELETEs concurrents, boucle de compaction (même ts rejoué), TOO_MANY_CONNECTIONS — Infinity (v0.7.0-dev5, non corrigé jusqu'à v0.7.3 vérifié commit par commit) ne supporte pas N écrivains concurrents sur une table. Contrat épinglé par `test/multitenant/test_infinity_retry.py`. Grep `CUSTOM B2B SaaS — Infinity delete conflict retry` et `per-table distributed write semaphore` |
| `rag/utils/infinity_conn.py` — `insert()` | La paire upsert (DELETE by id + INSERT) passe sous `_table_write_slot` + `_retry_on_txn_conflict` — c'est CE chemin qui générait les tempêtes Delete-vs-Delete même sans re-parse. Grep `CUSTOM B2B SaaS — per-table write semaphore` |
| `common/doc_store/infinity_conn_pool.py` | DEUX patchs classe sur le SDK thrift : (1) timeout socket sur `_reconnect` (2026-08-10) ; (2) **no-replay des mutations** — `insert`/`delete` tentés UNE fois, un timeout propage au lieu de rejouer (le rejeu SDK combattait son propre fantôme serveur : conflits "Delete N vs N" identiques, doublons potentiels sur insert ; le 5003 "Try N times" est le code fourre-tout du retry_wrapper, PAS la limite serveur). Kill-switch `INFINITY_MUTATION_REPLAY=1`. Contrat épinglé par `test/multitenant/test_infinity_no_replay.py`. À revérifier à chaque bump du SDK infinity. Grep `CUSTOM B2B SaaS — no replay of MUTATIONS` |
| `agent/sandbox/providers/k8s.py` + `agent/sandbox/security_shared.py` + `agent/sandbox/tests/` | Fichiers entièrement custom (provider Jobs K8s durcis + AST partagé + tests) — pas de conflit attendu, listés pour visibilité |
| `rag/utils/redis_conn.py` — `xautoclaim_all` + `rag/svr/task_executor.py` — garde `_is_task_permanently_gone` + `api/db/services/task_service.py` — `requeue_vanished_tasks` + hook dans `document_service.update_progress` | **Fix v0.9.16 « messages disparus »** (3 incidents, ~4% des tâches sous burst) : (1) pagination XAUTOCLAIM — seul le curseur "0-0" termine le scan (l'ancien `not claimed` laissait les orphelins derrière une page de baux frais invisibles à jamais) ; (2) collect() n'ACK plus un message « unknown » sur simple contention row-lock (perte définitive si le pair mourait) — ack réservé aux écartements permanents ; (3) filet : tâche jamais livrée (retry=0) + file VIDE (lag+pending=0) + >10 min → re-queue sans doublon possible, throttlé 60s. Dossier : docs/known-issues/queue-vanished-tasks-evidence.md. Grep `v0.9.16 queue-vanished fix`, pin `test/multitenant/test_queue_vanished_fixes.py` |
| `api/utils/validation_utils.py` — `ParserConfig` | Clés parent-child `enable_children`/`children_delimiter` acceptées par les APIs dataset (sinon "Extra inputs are not permitted" alors que l'UI/DB les gèrent — constaté au bench PC 2026-08-29). |
| `api/apps/**` (chunk_api, document_api, dataset_api, agent_api, sdk/doc, dataset_api_service) | **Offload doc-store obligatoire dans les routes async** : 32 appels `settings.docStoreConn.*` passés sous `await thread_pool_exec(...)` — un appel synchrone dans une fonction async gèle la boucle du pod api (probes comprises) jusqu'à 600 s (prod 2026-08-31 : 3 pods sur 4 NotReady). Verrou AST `test/multitenant/test_no_sync_docstore_in_async.py` — un merge upstream qui réintroduit un appel direct fait échouer le test. |
| `common/doc_store/es_conn_base.py` — `create_idx` | **ES 9.x** : `index.mapping.exclude_source_vectors: false` forcé au create (les dense_vector sont exclus du `_source` par défaut en 9.x → `rerank()` sans reranker + `insert_citations` lisent le fallback `zero_vector` silencieux de search.py — upstream ragflow#13272, confirmé contre ES 9.5.2 le 2026-08-28). Retry sans le setting si le serveur (≤8.x) ne le connaît pas. Grep `CUSTOM B2B SaaS — ES 9.x exclude_source_vectors` |
| `admin/server/services.py` — `SandboxMgr` | Provider `k8s` enregistré dans les 4 registres (`PROVIDER_REGISTRY`, schemas de `get_provider_config_schema`, `provider_classes` de `set_config` et de `test_connection`) — sinon invisible/rejeté côté admin panel même si `agent/sandbox/client.py` le supporte. Grep `CUSTOM B2B SaaS — provider sandbox k8s` |

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

4. **Detect when upstream DELETES a Python equivalent**: once upstream removes a
   `api/apps/restful_apis/<name>_api.py`, our Python implementation becomes the
   only one in the codebase — but we've intentionally kept it as the "fallback"
   while letting Go upstream catch up. When upstream deletes the Python:
   ```bash
   # After every merge, list Python files upstream has deleted vs us:
   git diff <last_merge_base>..upstream/main --diff-filter=D --name-only -- api/apps/restful_apis/
   ```
   If anything appears: either (a) port the Go upstream impl to our fork and
   wire it through our `rbacPerm` + `GetTenantID(c)` pattern, or (b) keep our
   Python and document the divergence. As of 2026-06-25 merge:
   - `api/apps/restful_apis/api_key_api.py`: deleted upstream. Our fork KEEPS
     it (per-user API keys is our custom B2B SaaS feature, scope ≠ upstream's
     tenant-scoped Go `api_token.go`). Routes `/api/v1/api_keys` are stable.
   - `api/apps/restful_apis/chunk_api.py`: still present upstream (774 lines,
     receives fix commits). Our Python is the fallback; the Go chunk routes
     (`StopParsing`, `AddChunk`, `ListChunks`) added upstream are NOT ramené
     in our fork yet. Watch for upstream deletion to trigger the port.

5. **MINE DORMANTE — binaires stagehand jamais empaquetés** : le composant
   `Browser` de l'agent Go (`internal/agent/component/browser`) exécute les
   binaires `stagehand-server-v3-linux-<arch>` que `ragflow_deps/download_deps.py`
   télécharge — mais **aucun Dockerfile ne les copie dans une image** (audit
   2026-08-26). Sans impact tant que le runtime agent Go n'est pas déployé
   (prod = api Python). Le jour où un déploiement inclut le serveur Go avec
   ses composants d'agent : ajouter la copie des binaires dans l'image + un
   `test -x` de présence au build (même discipline que les modèles DeepDoc,
   cf. le garde-fou `test -f` du Dockerfile principal).

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

> **DANGER — `--theirs` on RBAC files** (`api/apps/restful_apis/*.py`, `api/apps/sdk/*.py`): even when the visible conflict is small (1-2 hunks), upstream often auto-merged other parts of the same file that silently dropped our `@require_permission` / `@token_required` / IDOR-guard / audit-log code. **Default to `--ours` on these files**, then read the upstream diff (`git diff dev upstream/main -- <file>`) and re-apply the notable upstream changes manually. The 2026-05-15 merge hit this trap twice (agent_api.py + chunk_api.py); caught by `test_session_idor`, `test_rbac_coverage`, `test_webhook_security` — but the test failures are non-obvious without this rule, costing 15-30 min of debug each time.

| File / pattern | Rule |
|---|---|
| `internal/handler/*.go` — `user.ID` vs `GetTenantID(c)` | **Always keep `GetTenantID(c)`** (except `Accessible()` checks and `memory.go` comparison) |
| `internal/handler/kb.go` — `ListKbs`, `DeleteKB` | **Keep ours** — custom workspace-scoped routes |
| `internal/service/tenant.go` — `GetModels`/`SetModels` | **Exception: use `user.ID`** — `ListTenantDefaultModels` calls `GetInfoByUserID` which needs a real user ID |
| `internal/service/tenant.go` — model type names | Take upstream renames (`"llm"→"chat"`, `"image2text"→"vision"`, new `"ocr"`) |
| `rag/svr/task_executor.py` | **Keep ours** — `_embed_insert_pipelined`, `set_progress` throttle, tenant limiter release. Plus a 5-line K8s injection (search `CUSTOM B2B SaaS — K8s` to find both sites): an `import` line and the `touch_heartbeat` / `should_recycle` calls inside `report_status`. The actual logic lives in `rag/svr/_k8s_runtime.py` (custom file, no upstream conflicts). Plus the adaptive chunking two-pass block in `build_chunks` (CIA-10, grep `CUSTOM B2B SaaS — adaptive chunking`) — the pure helper it calls lives in `rag/svr/adaptive_chunk.py` (custom file, no upstream conflicts). Plus le réarmement périodique de `UNACKED_ITERATOR` dans `collect()` + garde anti-doublon `CURRENT_TASKS` (inscrit AVANT tout await) + **renouvellement de bail** par tâche (`RedisMsg.renew_lease` dans `rag/utils/redis_conn.py`, XCLAIM vers soi toutes les 60s) — sans le bail, toute tâche >5 min serait volée par le reclaim d'un pair (double traitement + abandon à 3 strikes) ; sans le réarmement, les tâches orphelinées par un worker mort restent RUNNING pour toujours. Grep `CUSTOM B2B SaaS — periodic XAUTOCLAIM`, contrat épinglé par `test/multitenant/test_task_lease.py` |
| `rag/llm/embedding_model.py` | **Keep ours** — `np.vstack(batches)` is O(n); upstream's `np.concatenate` loop is O(n²) |
| `api/utils/api_utils.py` | **Keep ours** — workspace tenant resolution in `token_required` |
| `api/apps/system_app.py`, `api/apps/api_app.py` | **Keep ours** — token routes scoped to `active_tenant_id()` |
| `api/apps/document_app.py`, `api/apps/sdk/doc.py` | **Keep ours** — workspace permission checks on routes |
| `api/apps/restful_apis/agent_api.py` | **Keep ours** — all `@require_permission` decorators + IDOR session-by-canvas check in `get_agent_session` + `WEBHOOK_REJECTED_NO_SECURITY` 403 path + `AuditService.record` on webhook invoke. NEVER `--theirs` even if conflict markers look small: upstream regularly auto-merges other parts of this file that strip our custom decorators silently. Caught by `test_session_idor`, `test_webhook_security`, `test_rbac_coverage`. |
| `api/apps/restful_apis/chunk_api.py` | **Keep ours** — 6× `@require_permission(...)` decorators + workspace-strict `KnowledgebaseService.query(tenant_id=, id=)` (vs upstream's `accessible()` which is now safe too, but the rename also dropped our RBAC). Same `--theirs` trap as `agent_api.py`. Caught by `test_rbac_coverage`. |
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
| `web/src/services/knowledge-service.ts` — `uploadDocument` / `webCrawlDocument` | **Keep ours** — uses `axios.post()` directly (not `request.post`) to inject `X-Workspace-Id` header. **DANGER trap**: when resolving conflicts here, the imports block and the function bodies can drift apart. If upstream removes `axios` from imports because they no longer use it, but we keep our custom function body that does use it, the result is a silent runtime `ReferenceError` swallowed by the `try/catch → console.warn` in `useUploadDocument`. Symptom: Save button does nothing, no visible error, no network call. **Verify after merge**: `grep -E "^import.*(axios\|Authorization\|getAuthorization)" web/src/services/knowledge-service.ts` must show all three present. |
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

**DANGER — never `git stash` while a merge is in progress.** `git stash` resets to HEAD, which silently drops `.git/MERGE_HEAD`. `git stash pop` restores the working tree but NOT MERGE_HEAD, so the next `git commit` creates a normal commit with **one parent instead of two** — git no longer knows that upstream was merged. Symptoms: `git log upstream/main ^dev` keeps reporting all "merged" commits as missing, and the next merge re-conflicts on the same 200+ files.

If it happens (caught early — before pushing to others):
```bash
# Rebuild the merge commit with the correct two parents (tree stays identical):
NEW_SHA=$(git log -1 --format=%B <bad_merge_sha> \
  | git commit-tree $(git rev-parse <bad_merge_sha>^{tree}) \
      -p <pre-merge-dev-tip> -p <upstream/main-tip-at-merge-time>)
git update-ref refs/heads/merge/upstream-$(date +%Y-%m-%d) "$NEW_SHA"
git checkout dev && git reset --hard <pre-merge-dev-tip>
git merge merge/upstream-$(date +%Y-%m-%d) --no-ff -m "<original merge message>"
git push --force-with-lease  # only if you'd already pushed the broken history
```

Safe alternative if you need to inspect the dev pre-merge state mid-merge: use `git worktree add ../ragflow-dev dev` instead of stashing — leaves MERGE_HEAD untouched on the original checkout.

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
export HOST_ADDRESS=http://127.0.0.1:9380 ZHIPU_AI_API_KEY=dummy SILICONFLOW_API_KEY=dummy PYTHONPATH=.
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
- Bumping Infinity image (`docker/docker-compose-base.yml` + `pyproject.toml`) — nightly format breaks the local WAL silently. Our Helm chart pins **`v0.7.3-x64-v3`** (alterai) / `v0.7.3-x64-v2` (défaut) explicitly, upgraded 2026-08-26 from dev5 after local validation (migration WAL in-place OK, stress 15 min propre — voir issue infiniflow/infinity#3418). **Do not** propagate upstream bumps to `helm/ragflow/values.yaml` without redoing that validation. CRITICAL : le protocole SDK↔serveur est couplé version à version (rejets croisés) — tout bump d'`infinity-sdk` dans pyproject.toml doit partir dans le MÊME déploiement que l'image serveur, et inversement. Les images arm64 ≥0.7.1 sont cassées upstream (glibc) — le compose local utilise x64 + `platform: linux/amd64` (Rosetta sur Mac).

**Watch for these upstream breaking changes (require full audit):**
- `rag/svr/task_executor.py` CLI changes — upstream 2026-06-02 switched from a positional worker name to `argparse` flags `-i <index> -t <type>`. **All custom launchers** must pass `-i` explicitly: `scripts/dev_up.sh`, `scripts/dev_simple.sh`, `scripts/dev_scaled.sh`, and the Helm template at `helm/ragflow/charts/ragflow-task-executor/templates/deployment.yaml`. CONSUMER_NAME is now derived as `task_executor_<type>_<index>`. Symptom: `error: unrecognized arguments: <name>` in `/tmp/ragflow_task_executor.log` → no worker → `test_e2e_smoke` and any parse/embed flow times out.
- Any change to `user_id == tenant_id` invariant in `api/db/init_data.py`
- New top-level route group added outside `authorized` in `router.go` (won't get workspace middleware)
- Rename of `GetInfoByUserID` in `internal/dao/tenant.go` (breaks `ListTenantDefaultModels`)
- Any new Langfuse-related route or service that uses `user_id` instead of `active_tenant_id()` — would leak workspace traces into a wrong project. See [docs/roadmap-multitenant-saas.md](docs/roadmap-multitenant-saas.md) Tier 3 prerequisites.
- **Service-layer `get_joined_tenants_by_user_id(tenant_id)` anti-pattern** — upstream refactors regularly introduce a service function that takes `tenant_id` (which in our fork is the **workspace tenant_id**, not a user_id) and feeds it to `TenantService.get_joined_tenants_by_user_id(...)`. The lookup returns empty for workspace tenants → service silently returns 0 rows. Caught twice in the 2026-05-15 merge: `dataset_api_service.list_datasets` (broke MCP + browser dataset list) and `knowledgebase_service.accessible` (broke a unit test for team-dataset access). The `test/multitenant/test_perf_invariants.py::TestUpstreamJoinedTenantsMisuse` AST test flags new occurrences automatically — if it fails after a merge, the fix is either (a) workspace-strict scoping `tenant_ids = [tenant_id]` (see `dataset_api_service.list_datasets`) or (b) adopt the upstream impl as-is when the parameter is actually named `user_id` and both meanings work (see `knowledgebase_service.accessible`). Its sibling `TestUserIdAsTenantIdMisuse` (added 2026-07-16 after the openai_api "You don't own the chat" bug) bans `tenant_id=current_user.id` kwargs anywhere under `api/apps/` — fix with `active_tenant_id()`, or whitelist in `ALLOWED_CALLERS` when the resource is user-owned by design (memories).
