#!/usr/bin/env bash
# Local dev backend — single process, hot-reload.
#
# Use this for daily dev: 1× ragflow_server (Quart's app.run, picks up code
# changes) + 1× task_executor. Lower memory, faster iteration.
#
# For full test-suite runs use `dev_scaled.sh` instead — it's ~2x faster on
# the bridge but eats ~6-10 GB more RAM and disables hot-reload.
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

export PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH
export PYTHONPATH="$REPO"
export DOC_ENGINE=infinity
export ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026
export RSA_PASSPHRASE=Welcome

# Source local secrets (gitignored)
if [ -f "$REPO/.env.local" ]; then
  set -a; source "$REPO/.env.local"; set +a
fi

echo "[dev_simple] killing any existing ragflow_server / task_executor / hypercorn"
pkill -f "ragflow_server.py" 2>/dev/null || true
pkill -f "task_executor.py"  2>/dev/null || true
pkill -f "hypercorn.*api.asgi" 2>/dev/null || true
sleep 2

echo "[dev_simple] starting task_executor (1 worker)"
nohup uv run python rag/svr/task_executor.py dev_worker_1 \
  > /tmp/ragflow_task_executor.log 2>&1 &

echo "[dev_simple] starting ragflow_server (single process, hot-reload)"
nohup uv run python api/ragflow_server.py \
  > /tmp/ragflow_server.log 2>&1 &

echo "[dev_simple] backend up. Tail logs:"
echo "  tail -f /tmp/ragflow_server.log /tmp/ragflow_task_executor.log"
