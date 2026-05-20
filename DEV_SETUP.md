# Dev Setup — RAGFlow Fork (Multi-Tenant B2B SaaS)

Local development workflow for this fork. Until a full Docker Compose stack
is wired up for our custom apps, services must be started manually.

## 0. Prerequisites

- Python 3.12 with [`uv`](https://github.com/astral-sh/uv) at `~/.local/bin/uv`
- Node.js ≥ 18.20 (Homebrew: `/opt/homebrew/bin/node`)
- Docker Desktop running

The project venv is managed by `uv` (no `pip` inside `.venv`). To install
extra deps not in `pyproject.toml`:

```bash
PATH=/Users/zappy/.local/bin:$PATH uv pip install <package>
```

> **Why this matters**: `uv run` ignores any `VIRTUAL_ENV` pointing to a
> different env (e.g. miniconda) and always uses the project's `.venv/`.

## 1. Infra services (Docker)

The four dependent services (MySQL, Redis, MinIO, Infinity) run in Docker:

```bash
cd /Users/zappy/ragflow
docker compose -f docker/docker-compose-base.yml up -d
```

Wait ~30s for all containers to be healthy:

```bash
docker compose -f docker/docker-compose-base.yml ps
```

Useful endpoints:

| Service  | URL / Port              | Credentials                |
| -------- | ----------------------- | -------------------------- |
| MySQL    | `localhost:3306`        | `root` / `infini_rag_flow` |
| Redis    | `localhost:6379`        | password `infini_rag_flow` |
| MinIO    | `http://localhost:9001` | `rag_flow` / `infini_rag_flow` |
| Infinity | `localhost:23817`       | (no auth)                  |

To reset everything (drops all data!):

```bash
docker compose -f docker/docker-compose-base.yml down -v
```

## 2. Apps (run manually, in separate terminals or background)

The four custom apps run as local Python/Node processes against the Docker
infra above.

### 2.1 RAGFlow backend (Quart, port 9380)

```bash
cd /Users/zappy/ragflow

PYTHONPATH=$(pwd) \
  DOC_ENGINE=infinity \
  ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026 \
  RSA_PASSPHRASE=Welcome \
  PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \
  uv run python api/ragflow_server.py >> /tmp/ragflow.log 2>&1 &
```

- Logs: `tail -f /tmp/ragflow.log`
- Status: `ps aux | grep ragflow_server`
- Stop: `pkill -f ragflow_server.py`

### 2.2 Task executor (ingestion worker)

Required for document parsing/vectorization. Not embedded in the backend.

```bash
cd /Users/zappy/ragflow

PYTHONPATH=$(pwd) \
  DOC_ENGINE=infinity \
  ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026 \
  RSA_PASSPHRASE=Welcome \
  PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \
  uv run python rag/svr/task_executor.py dev_worker_1 >> /tmp/task_executor.log 2>&1 &
```

- Logs: `tail -f /tmp/task_executor.log`
- Stop: `pkill -f task_executor.py`

### 2.3 Admin panel backend (FastAPI/uvicorn, port 9381)

```bash
cd /Users/zappy/ragflow

PYTHONPATH=$(pwd) \
  ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026 \
  RSA_PASSPHRASE=Welcome \
  PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \
  uv run uvicorn management.server.main:app \
    --host 0.0.0.0 --port 9381 --reload \
    >> /tmp/admin.log 2>&1 &
```

- `--reload` picks up code changes automatically
- Swagger docs: http://localhost:9381/api/admin/docs
- Stop: `pkill -f "management.server.main"` or `lsof -ti:9381 | xargs kill -9`
- Port already in use: `lsof -ti:9381 | xargs kill -9`

### 2.4 RAGFlow web frontend (Vite, port 5173)

```bash
cd /Users/zappy/ragflow/web
PATH=/opt/homebrew/bin:$PATH npm run dev
```

### 2.5 Admin panel frontend (Vite, port 5174)

```bash
cd /Users/zappy/ragflow/management/web
PATH=/opt/homebrew/bin:$PATH npm run dev
```

## 3. Stop everything

```bash
# Apps
pkill -f ragflow_server.py
pkill -f task_executor.py
pkill -f "management.server.main"
pkill -f "vite"

# Infra (keep data)
docker compose -f docker/docker-compose-base.yml stop

# Infra (wipe data)
docker compose -f docker/docker-compose-base.yml down -v
```

## Environment variables reference

The required env vars for the four apps:

| Variable            | Used by                | Default (dev)                                | Notes |
| ------------------- | ---------------------- | -------------------------------------------- | ----- |
| `DOC_ENGINE`        | ragflow + executor     | `infinity`                                   | Vector DB choice |
| `ADMIN_JWT_SECRET`  | ragflow + admin        | `dev-only-change-before-prod-ragflow-2026`   | JWT signing for bridge login |
| `RSA_PASSPHRASE`    | ragflow + executor + admin | `Welcome`                                | Required since security hardening; matches `conf/private.pem` dev key |
| `PYTHONPATH`        | all Python apps        | `$(pwd)` from repo root                      | Required by `uv run` |

For prod, see [scripts/init_rsa_keys.sh](scripts/init_rsa_keys.sh) to
generate a new RSA keypair and rotate `RSA_PASSPHRASE`.

## Test suite

The 110-test multi-tenant suite validates RBAC + workspace isolation:

```bash
PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \
  PYTHONPATH=$(pwd) \
  RAGFLOW_TEST_LOCAL_AUTH=1 \
  VIEWER_EMAIL=viewer.internal@cyllene.com \
  EDITOR_EMAIL=editor.internal@cyllene.com \
  uv run python -m pytest test/multitenant/ -v
```

Backend services (1) + (2) must be running. Tests use the seeded
`ci.internal@cyllene.com`, `viewer.internal@cyllene.com` and
`editor.internal@cyllene.com` accounts.
