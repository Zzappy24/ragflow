"""K8s runtime helpers for the task_executor — heartbeat + recycle.

This module exists to keep the customisation in `task_executor.py` to two
lines so upstream merges stay clean. See CLAUDE.md "Custom files to watch
on upstream merges" — task_executor.py imports `touch_heartbeat` and
`should_recycle`; everything else lives here.

CUSTOM B2B SaaS — not part of upstream RAGFlow.
"""
from __future__ import annotations

import logging
import os

# 0 = disabled. K8s deployments set this via env (see helm chart).
# In dev / non-K8s deployments the variable is absent → recycling is off
# and task_executor behaves exactly like upstream.
RECYCLE_AFTER_TASKS = int(os.environ.get("WORKER_RECYCLE_AFTER_TASKS", "0"))

# Heartbeat file path — kubelet's exec liveness probe stats this file's
# mtime. Default works on Linux containers where /tmp is writable; can be
# overridden if the container's tmp layout differs.
HEARTBEAT_FILE = os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/k8s_heartbeat")


def touch_heartbeat(timestamp: float) -> None:
    """Best-effort write of `timestamp` to the heartbeat file.

    Called once per `report_status` iteration (~30s). Failures are logged
    but never raised — a worker that can't write its heartbeat will be
    killed by the liveness probe instead, which is the desired outcome.
    """
    try:
        with open(HEARTBEAT_FILE, "w") as fh:
            fh.write(str(timestamp))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logging.warning("Failed to touch heartbeat file %s: %s", HEARTBEAT_FILE, exc)


def should_recycle(done: int, failed: int) -> bool:
    """Return True when the worker has crossed the recycle threshold.

    Caller is expected to set the global `stop_event` (or equivalent) so
    the main loop exits cleanly. Deployment respawns a fresh pod.
    """
    if RECYCLE_AFTER_TASKS <= 0:
        return False
    total = done + failed
    if total >= RECYCLE_AFTER_TASKS:
        logging.info(
            "Recycling worker after %d tasks (threshold=%d); requesting stop.",
            total,
            RECYCLE_AFTER_TASKS,
        )
        return True
    return False
