#!/usr/bin/env bash
# Shell-level parallelism for the workspace HTTP-API bridge.
#
# pytest-xdist (`-n N`) DOES NOT WORK here: every worker shares a single
# session-scoped `_ws_credentials` fixture (driven by CI_WORKSPACE_NAME=
# `Général` by default), so all xdist workers slam the same workspace and
# trip dataset/chat name collisions, "Duplicated chat name", and embedding-
# model races on Ollama.
#
# Pattern that works (mirrors the prod K8s shape — N independent clients,
# N hypercorn workers, no shared writable state):
#
#   1. Bring up a scaled backend ahead of time:
#        scripts/dev_scaled.sh        # 4 hypercorn + 4 task_executor
#
#   2. Provision N test workspaces once:
#        uv run python test/multitenant_http_api/provision_workers.py 4
#
#   3. Run this script — it spawns 4 pytest invocations in parallel,
#      each pinned to its own CI_WORKSPACE_NAME (ci-test-w0..ci-test-w3).
#
# Output is interleaved by line; per-worker logs land in
# /tmp/httpapi_parallel_w{0..N-1}.log for post-mortem.
set -e

WORKERS="${WORKERS:-4}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

export PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH
export RAGFLOW_TEST_LOCAL_AUTH=1
export ZHIPU_AI_API_KEY="${ZHIPU_AI_API_KEY:-stub}"

# Reap any stale pytest workers from a previous (interrupted/killed) run.
# Multiple aborted parallel.sh invocations have been observed leaving 4-N
# orphan pytest processes still hammering the backend, which makes a fresh
# run hang on Ollama serialization or 502 the live workers — kill them all
# before we spawn ours.
stale=$(pgrep -f 'pytest test/multitenant_http_api' 2>/dev/null || true)
if [ -n "$stale" ]; then
  echo "[parallel] reaping $(echo "$stale" | wc -l | tr -d ' ') stale pytest worker(s) from prior runs: $stale"
  echo "$stale" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

# Sync venv ONCE before forking. We use `uv run --no-sync` in the workers so
# they reuse the synced venv without each one re-syncing in parallel (which
# would race on package install/uninstall and silently kill workers — observed
# during dev: 4 simultaneous `uv run` calls produced empty test logs because
# uv was uninstalling/reinstalling `hypothesis` on each invocation).
#
# Ensure `hypothesis` (test-only dep) is present — pyproject's `test` group
# additionally pulls tensorflow-cpu which has no macOS ARM64 wheels, so we
# can't simply do `uv sync --group test`.
uv run --no-sync python -c "import hypothesis" 2>/dev/null || \
  uv pip install --quiet hypothesis

# Cleanup hook: kill our spawned workers on EXIT / INT / TERM so a Ctrl-C
# (or a parent kill) never leaves orphan pytest processes for the next run
# to trip over.
declare -a pids
cleanup() {
  local signal="${1:-EXIT}"
  for pid in "${pids[@]}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
      echo "[parallel] $signal: killed worker pid=$pid"
    fi
  done
}
trap 'cleanup INT;  exit 130' INT
trap 'cleanup TERM; exit 143' TERM
trap 'cleanup EXIT' EXIT

# Split test files across workers (round-robin alphabetical) so each worker
# runs a DIFFERENT subset of the suite — wall-clock = max(per-worker time),
# not Nx the serial time.
#
# Why round-robin and not bin-pack-by-duration? Tested both: a greedy LPT
# bin-pack on serial-measured durations produced WORSE wall-clock because
# it groups Ollama-bound files into the same bin, and Ollama serialises
# embeddings — 4 workers all queueing on Ollama from the same bin stalls
# that worker for 5+ minutes. Round-robin alphabetical ends up mixing
# embedding-heavy files with light ones in each bin, which keeps the
# Ollama queue more evenly fed across workers.
#
# `mapfile` is bash 4+; macOS ships bash 3.2 — read into an array manually.
declare -a ALL_TEST_FILES=()
while IFS= read -r line; do ALL_TEST_FILES+=("$line"); done < <(ls "$REPO"/test/multitenant_http_api/test_*.py | sort)
declare -a WORKER_FILES
for i in $(seq 0 $((${#ALL_TEST_FILES[@]} - 1))); do
  worker_idx=$((i % WORKERS))
  WORKER_FILES[$worker_idx]+=" ${ALL_TEST_FILES[$i]}"
done

START=$(date +%s)
for i in $(seq 0 $((WORKERS-1))); do
  log="/tmp/httpapi_parallel_w${i}.log"
  files="${WORKER_FILES[$i]}"
  echo "[parallel] worker $i files: $(echo $files | wc -w | tr -d ' ') file(s)"
  # shellcheck disable=SC2086  # word-splitting on $files is intentional
  CI_WORKSPACE_NAME="ci-test-w${i}" \
    uv run --no-sync python -m pytest $files \
      -p no:cacheprovider \
      --no-header --no-summary -q \
      > "$log" 2>&1 &
  pid=$!
  pids[$i]=$pid
  echo "[parallel] spawned worker $i pid=$pid → $log"
done

# Wait for all, capture exit codes
fail=0
for i in $(seq 0 $((WORKERS-1))); do
  pid=${pids[$i]}
  if ! wait "$pid"; then
    fail=$((fail+1))
    echo "[parallel] worker $i (pid=$pid) FAILED"
  fi
  # Worker is now reaped — clear from the array so the EXIT trap does not
  # try to kill an already-finished PID (cosmetic, but keeps logs clean).
  pids[$i]=""
done
END=$(date +%s)

echo ""
echo "=== Per-worker tail ==="
for i in $(seq 0 $((WORKERS-1))); do
  log="/tmp/httpapi_parallel_w${i}.log"
  echo "--- w$i ---"
  grep -E "passed|failed|error" "$log" | tail -1
done

echo ""
echo "DURATION=$((END-START))s WORKERS=$WORKERS exit_failures=$fail"
exit "$fail"
