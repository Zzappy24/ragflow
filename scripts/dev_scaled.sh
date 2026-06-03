#!/usr/bin/env bash
# Scaled local backend — N hypercorn workers + N task_executor workers.
#
# Use this when you want to run the full HTTP-API bridge or any heavy test
# suite. On the slow batch (4 files, 240 tests) we measured:
#
#   simple (1+1)  shell-parallel-4 = 272 s
#   scaled (4+4)  shell-parallel-4 = 154 s   (~1.8x speedup)
#
# Cost: ~6-10 GB extra RAM, no hot-reload (changes need a script restart).
# Mirrors the prod K8s shape (N replicas of server + N replicas of
# task_executor) so parallel correctness here approximates prod.
#
# Usage:
#   scripts/dev_scaled.sh         # WORKERS=4 (default)
#   WORKERS=8 scripts/dev_scaled.sh
set -e

WORKERS="${WORKERS:-4}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

export PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH
export PYTHONPATH="$REPO"
export DOC_ENGINE=infinity
export ADMIN_JWT_SECRET=dev-only-change-before-prod-ragflow-2026
export RSA_PASSPHRASE=Welcome

if [ -f "$REPO/.env.local" ]; then
  set -a; source "$REPO/.env.local"; set +a
fi

echo "[dev_scaled] WORKERS=$WORKERS"
echo "[dev_scaled] killing any existing ragflow_server / task_executor / hypercorn"
pkill -f "ragflow_server.py" 2>/dev/null || true
pkill -f "task_executor.py"  2>/dev/null || true
pkill -f "hypercorn.*api.asgi" 2>/dev/null || true
sleep 2

echo "[dev_scaled] starting $WORKERS task_executor workers"
# upstream 2026-06-02 switched from positional worker name to argparse flags
# `-i <index> -t <type>`. The index suffix below becomes part of CONSUMER_NAME
# (`task_executor_common_<i>`), so workers stay unique in Redis.
for i in $(seq 0 $((WORKERS - 1))); do
  nohup uv run python rag/svr/task_executor.py -i "$i" \
    > "/tmp/ragflow_te_$i.log" 2>&1 &
done

echo "[dev_scaled] starting hypercorn with $WORKERS workers on :9380"
nohup uv run hypercorn api.asgi:app \
  -b 127.0.0.1:9380 \
  --workers "$WORKERS" \
  > /tmp/ragflow_hypercorn.log 2>&1 &

echo "[dev_scaled] backend launching. Watch readiness:"
echo "  tail -f /tmp/ragflow_hypercorn.log"
echo "  curl http://127.0.0.1:9380/   # should return shortly"
