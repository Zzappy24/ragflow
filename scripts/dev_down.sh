#!/usr/bin/env bash
# Stop the full local dev stack and free CPU/RAM.
#
# Usage:
#   bash scripts/dev_down.sh           # stops backend + executor + admin + mcp,
#                                      # leaves docker base services running
#                                      # (so next `dev_up` is fast)
#   bash scripts/dev_down.sh --all     # also stops docker base services
#                                      # (use when laptop heats / you're done for the day)
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

ALL=0
if [ "${1:-}" = "--all" ]; then
  ALL=1
fi

# ---------------------------------------------------------------------------
# 1. Python processes (ragflow + task_executor + admin + mcp)
# ---------------------------------------------------------------------------
echo "[dev_down] stopping python processes"
pkill -f "ragflow_server.py"      2>/dev/null || true
pkill -f "task_executor.py"       2>/dev/null || true
pkill -f "hypercorn.*api.asgi"    2>/dev/null || true
pkill -f "management.server.main" 2>/dev/null || true
pkill -f "mcp/server/server.py"   2>/dev/null || true
sleep 2

# Force-kill anything still listening on the dev ports.
for port in 9380 9381 9382; do
  pid=$(lsof -ti :$port 2>/dev/null || true)
  if [ -n "$pid" ]; then
    echo "[dev_down] force-killing leftover on :$port (pid=$pid)"
    kill -9 $pid 2>/dev/null || true
  fi
done

# ---------------------------------------------------------------------------
# 2. (--all only) Docker base services
# ---------------------------------------------------------------------------
if [ "$ALL" -eq 1 ]; then
  echo "[dev_down] stopping docker base services (volumes preserved)"
  # All optional profiles included so legacy containers (e.g. es01 from a prior
  # DOC_ENGINE=elasticsearch run) also stop. Volumes are preserved either way.
  docker compose -f docker/docker-compose-base.yml \
    --profile elasticsearch --profile opensearch --profile infinity \
    --profile oceanbase --profile seekdb --profile sandbox \
    stop > /dev/null
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "[dev_down] ✓ stopped"
if [ "$ALL" -eq 1 ]; then
  echo "  data preserved in docker volumes (mysql, infinity, minio, redis, es01)."
  echo "  restart with: bash scripts/dev_up.sh"
else
  echo "  docker base still running (keep for fast restart)."
  echo "  add --all to also stop docker."
fi
