# Modified Files — RBAC Multi-Tenant Layer

Files modified or created by the RBAC layer. Used during upstream merges to identify potential conflicts.

## New Files (no conflict risk)

### RBAC Core (`api/apps/extensions/`)
- `api/apps/extensions/__init__.py`
- `api/apps/extensions/rbac.py` — Permission enum, role matrix, decorators
- `api/apps/extensions/rbac_retriever.py` — Proxy on settings.retriever for dataset filtering
- `api/apps/extensions/audit.py` — Audit log decorator
- `api/apps/extensions/quotas.py` — Quota checking

### Services (`api/db/services/`)
- `api/db/services/org_service.py`
- `api/db/services/workspace_service.py`
- `api/db/services/group_service.py`
- `api/db/services/audit_service.py`

### Migration
- `api/db/migrate/rbac_tables.py`

### Admin Panel (`management/`)
- Entire `management/` directory (separate FastAPI + Vue app)

## Modified Files (check on upstream merge)

### Database Models
- `api/db/db_models.py` — +9 Peewee model classes appended at end of file

### Route Files (decorator additions: 1-5 lines each)
- `api/apps/restful_apis/chat_api.py`
- `api/apps/restful_apis/dataset_api.py`
- `api/apps/restful_apis/file_api.py`
- `api/apps/restful_apis/search_api.py`
- `api/apps/sdk/doc.py`
- `api/apps/sdk/session.py`
- `api/apps/sdk/agents.py`
- `api/apps/kb_app.py`
- `api/apps/document_app.py`
- `api/apps/canvas_app.py`
- `api/apps/chunk_app.py`

### Security Fixes
- `api/apps/document_app.py` — Re-enable @login_required on get_image
- `api/apps/sdk/agents.py` — Add auth to webhook routes
- `api/apps/canvas_app.py` — Add auth to upload/trace routes
- `api/apps/mcp_server_app.py` — Add auth to test_mcp

### Auth & API
- `api/utils/api_utils.py` — Enrich @token_required with api_key_scope check
- `api/apps/api_app.py` — Block native token creation for non-superusers
- `api/apps/user_app.py` — Signup toggle via env var

### Server Startup
- `api/ragflow_server.py` — 1 line: call install_rbac_proxy()
