#!/usr/bin/env bash
# Start the full local dev stack.
#
# Usage:
#   bash scripts/dev_up.sh           # backend + task_executor (daily dev)
#   bash scripts/dev_up.sh --full    # + admin panel (:9381) + MCP server (:9382)
#                                    # required for full test/multitenant/ coverage
#
# Stop with: bash scripts/dev_down.sh
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

FULL=0
if [ "${1:-}" = "--full" ]; then
  FULL=1
fi

export PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH
export PYTHONPATH="$REPO"
export DOC_ENGINE=infinity
export ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026
export RSA_PASSPHRASE=Welcome

if [ -f "$REPO/.env.local" ]; then
  set -a; source "$REPO/.env.local"; set +a
fi

# ---------------------------------------------------------------------------
# 1. Docker base services (mysql, redis, infinity, minio, es01)
# ---------------------------------------------------------------------------
echo "[dev_up] starting docker base services (mysql, redis, infinity, minio, es01)"
docker compose -f docker/docker-compose-base.yml --profile infinity up -d > /dev/null

echo -n "[dev_up] waiting for minio… "
until curl -fsS http://localhost:9000/minio/health/live > /dev/null 2>&1; do sleep 2; done
echo "OK"

# ---------------------------------------------------------------------------
# 2. Ollama health check (background app, models at ~/.ollama/models)
# ---------------------------------------------------------------------------
if ! curl -fsS http://localhost:11434/api/version > /dev/null 2>&1; then
  echo "[dev_up] WARNING: Ollama daemon not reachable at :11434 (open Ollama.app)"
fi

# ---------------------------------------------------------------------------
# 3. RAGFlow backend + task_executor (kill stale, then start)
# ---------------------------------------------------------------------------
echo "[dev_up] killing stale ragflow_server / task_executor / hypercorn"
pkill -f "ragflow_server.py"   2>/dev/null || true
pkill -f "task_executor.py"    2>/dev/null || true
pkill -f "hypercorn.*api.asgi" 2>/dev/null || true
sleep 2

echo "[dev_up] starting task_executor (1 worker)"
nohup uv run python rag/svr/task_executor.py dev_worker_1 \
  > /tmp/ragflow_task_executor.log 2>&1 &

echo "[dev_up] starting ragflow_server (single process, hot-reload)"
nohup uv run python api/ragflow_server.py \
  > /tmp/ragflow_server.log 2>&1 &

echo -n "[dev_up] waiting for ragflow_server :9380… "
until curl -fsS http://localhost:9380/api/v1/system/version > /dev/null 2>&1; do sleep 2; done
echo "OK"

# ---------------------------------------------------------------------------
# 4. (--full only) Admin panel + MCP server
# ---------------------------------------------------------------------------
if [ "$FULL" -eq 1 ]; then
  echo "[dev_up] killing stale admin / mcp processes"
  pkill -f "management.server.main" 2>/dev/null || true
  pkill -f "mcp/server/server.py"   2>/dev/null || true
  sleep 1

  echo "[dev_up] starting admin panel (:9381)"
  nohup uv run uvicorn management.server.main:app --host 0.0.0.0 --port 9381 \
    > /tmp/ragflow_admin.log 2>&1 &

  echo "[dev_up] starting MCP server (:9382, mode=host)"
  nohup uv run python mcp/server/server.py \
    --mode=host --host=127.0.0.1 --port=9382 \
    --base-url=http://127.0.0.1:9380 \
    > /tmp/ragflow_mcp.log 2>&1 &

  echo -n "[dev_up] waiting for admin :9381 + mcp :9382… "
  until curl -fsS http://localhost:9381/api/admin/docs > /dev/null 2>&1 \
     && curl -fs  http://localhost:9382/mcp/ > /dev/null 2>&1; do sleep 2; done
  echo "OK"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "[dev_up] ✓ stack ready"
echo "  ragflow API   :9380   tail -f /tmp/ragflow_server.log"
echo "  task_executor         tail -f /tmp/ragflow_task_executor.log"
if [ "$FULL" -eq 1 ]; then
  echo "  admin panel   :9381   tail -f /tmp/ragflow_admin.log"
  echo "  mcp server    :9382   tail -f /tmp/ragflow_mcp.log"
fi
echo ""
echo "  stop all:   bash scripts/dev_down.sh"
echo "  run tests:  see memory project_test_suite.md (canonical command)"
