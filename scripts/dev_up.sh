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

# Wrapper: spawn in a subshell that ignores SIGINT, then exec nohup so the
# child survives Ctrl+C of the wait loop below. dev_down.sh still kills via
# SIGTERM (default pkill), which is NOT trapped — clean shutdown still works.
spawn_detached() {
  local logfile="$1"; shift
  ( trap '' INT; exec nohup "$@" > "$logfile" 2>&1 ) &
}

echo "[dev_up] starting task_executor (1 worker)"
# upstream 2026-06-02 switched task_executor CLI from a positional worker
# name to argparse flags `-i <index> -t <type>`; passing the old positional
# `dev_worker_1` is now a hard error.
spawn_detached /tmp/ragflow_task_executor.log uv run python rag/svr/task_executor.py -i 0

echo "[dev_up] starting ragflow_server (single process, hot-reload)"
spawn_detached /tmp/ragflow_server.log uv run python api/ragflow_server.py

echo "[dev_up] starting ragflow frontend (Vite :9222)"
# Kill stale vite for the main web/ folder (not management/web/).
for pid in $(pgrep -f "vite$" 2>/dev/null); do
  cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
  if echo "$cmd" | grep -q "ragflow/web " || echo "$cmd" | grep -q "ragflow/web$"; then
    kill "$pid" 2>/dev/null || true
  fi
done
( trap '' INT; cd "$REPO/web" && exec nohup npm run dev > /tmp/ragflow_web.log 2>&1 ) &

echo -n "[dev_up] waiting for ragflow_server :9380 + frontend :9222… "
until curl -fsS http://localhost:9380/api/v1/system/version > /dev/null 2>&1 \
   && curl -fsS http://localhost:9222/ > /dev/null 2>&1; do sleep 2; done
echo "OK"

# ---------------------------------------------------------------------------
# 4. (--full only) Admin panel + MCP server
# ---------------------------------------------------------------------------
if [ "$FULL" -eq 1 ]; then
  echo "[dev_up] killing stale admin / mcp / vite processes"
  pkill -f "management.server.main"      2>/dev/null || true
  pkill -f "mcp/server/server.py"        2>/dev/null || true
  pkill -f "management/web.*vite"        2>/dev/null || true
  # vite spawns esbuild children; kill by working dir is the reliable way.
  for pid in $(pgrep -f "vite$" 2>/dev/null); do
    if ps -o command= -p "$pid" 2>/dev/null | grep -q "management/web"; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  sleep 1

  echo "[dev_up] starting admin backend (:9381)"
  spawn_detached /tmp/ragflow_admin.log uv run uvicorn management.server.main:app --host 0.0.0.0 --port 9381

  echo "[dev_up] starting MCP server (:9382, mode=host)"
  spawn_detached /tmp/ragflow_mcp.log uv run python mcp/server/server.py \
    --mode=host --host=127.0.0.1 --port=9382 \
    --base-url=http://127.0.0.1:9380

  echo "[dev_up] starting admin frontend (Vite :5173)"
  ( trap '' INT; cd "$REPO/management/web" && exec nohup npm run dev > /tmp/ragflow_admin_web.log 2>&1 ) &

  echo -n "[dev_up] waiting for admin backend :9381 + mcp :9382 + admin frontend :5173… "
  # MCP returns 401 on unauthenticated GET — that's success (auth-required, alive).
  # We just check that the port is bound and the process answers with any HTTP code.
  until curl -fsS http://localhost:9381/api/admin/docs > /dev/null 2>&1 \
     && [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:9382/mcp/)" != "000" ] \
     && curl -fsS http://localhost:5173/ > /dev/null 2>&1; do
    sleep 2
  done
  echo "OK"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "[dev_up] ✓ stack ready"
echo "  ragflow API     :9380   tail -f /tmp/ragflow_server.log"
echo "  ragflow UI      :9222   tail -f /tmp/ragflow_web.log         → http://localhost:9222/"
echo "  task_executor           tail -f /tmp/ragflow_task_executor.log"
if [ "$FULL" -eq 1 ]; then
  echo "  admin backend   :9381   tail -f /tmp/ragflow_admin.log"
  echo "  admin UI        :5173   tail -f /tmp/ragflow_admin_web.log  → http://localhost:5173/admin"
  echo "  mcp server      :9382   tail -f /tmp/ragflow_mcp.log"
fi
echo ""
echo "  stop all:   bash scripts/dev_down.sh"
echo "  run tests:  see memory project_test_suite.md (canonical command)"
