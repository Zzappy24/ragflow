"""ASGI entry point for multi-worker servers (hypercorn/uvicorn).

`api/ragflow_server.py` exists for `python api/ragflow_server.py` and runs
all of the boot wiring inside `if __name__ == '__main__':`. That is fine for
single-process dev mode but unusable for `hypercorn --workers N` because the
entry point in that case is a *module import* — `__main__` never fires.

This module replicates the same wiring so that each hypercorn worker boots a
fully-initialised app. It is the entry point used by `scripts/dev_scaled.sh`
and is intended to be the prod entry point too.

Run with:

    hypercorn api.asgi:app -b 127.0.0.1:9380 --workers 4

Each worker is its own OS process; init runs N times (DB init is idempotent,
plugin load is light). update_progress / flush_token_usage threads are
guarded by a Redis lock so only one worker actually does the work.
"""
from common.log_utils import init_root_logger
init_root_logger("ragflow_asgi")

from common import settings
settings.init_settings()
# Sanity check — STORAGE_IMPL DOIT être set après init_settings().
# Si ce assert échoue, le worker boot crash en CrashLoopBackOff, plutôt
# que de servir des requêtes en mode dégradé (où settings.STORAGE_IMPL=None
# fait planter tous les uploads/parses en NoneType.get() — observé
# 2026-06-30 sur 1 des 2 pods API, race condition mystérieuse à l'init).
assert settings.STORAGE_IMPL is not None, (
    "STORAGE_IMPL is None after init_settings() — env vars (STORAGE_IMPL_TYPE) "
    "or storage backend config (MinIO/S3/Azure/...) broken. Inspect logs above."
)

from api.apps import app
from api.db.db_models import init_database_tables as init_web_db
from api.db.init_data import init_web_data

init_web_db()
init_web_data()

from api.apps.extensions.rbac_retriever import install_rbac_proxy
install_rbac_proxy()


@app.before_request
async def _rbac_resolve_tenant():
    from quart import g, request
    g._ws_header = request.headers.get("X-Workspace-Id") or None
    g._tenant_resolved = False
    g.active_tenant_id = None
    g.rbac_user_id = None


from api.db.runtime_config import RuntimeConfig
RuntimeConfig.init_env()
RuntimeConfig.init_config(JOB_SERVER_HOST=settings.HOST_IP, HTTP_PORT=settings.HOST_PORT)

from agent.plugin import GlobalPluginManager
GlobalPluginManager.load_plugins()


# CUSTOM B2B SaaS — K8s liveness/readiness endpoints.
# /healthz — liveness: 200 if the worker is alive. No auth, no DB/Redis
#            hit, returns instantly. Safe target for kubelet livenessProbe.
# /readyz  — readiness: 200 only if Redis + MariaDB are both reachable.
#            Returns 503 + JSON `{ok:false, reason:...}` otherwise so we
#            can read `kubectl describe pod` and know which dep is down.
#
# Mounted directly on the Quart `app`, NOT inside a Blueprint, so the
# `@app.before_request` RBAC hook above runs but does not block — it only
# sets `g.*`, no auth check (that's `@login_required` which we don't use
# on these routes).
@app.route("/healthz", methods=["GET"])
async def _healthz():
    return {"ok": True}, 200


@app.route("/readyz", methods=["GET"])
async def _readyz():
    from rag.utils.redis_conn import REDIS_CONN
    from api.db.db_models import DB
    try:
        if not REDIS_CONN.health():
            return {"ok": False, "reason": "redis"}, 503
    except Exception as exc:  # noqa: BLE001 — best-effort probe
        return {"ok": False, "reason": f"redis:{exc}"}, 503
    try:
        with DB.connection_context():
            DB.execute_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"mysql:{exc}"}, 503
    return {"ok": True}, 200


# ─────────────────────────────────────────────────────────────────────────────
# Background threads — CRITICAL for live UI updates.
#
# `api/ragflow_server.py` starts two threads inside `if __name__ == '__main__':`:
#   - update_progress: every 6s, aggregates Task.progress → Document.progress
#     so the list_documents endpoint returns live values during parsing.
#   - flush_token_usage: every 30s, flushes Redis token counters to MySQL.
#
# In hypercorn mode (this entry point), `__main__` never runs and the comment
# at the top of this file mentioned the threads but never actually started
# them. Result: Document.progress stays frozen at the initial value (e.g.
# 0.0022) during the entire parse and only updates when the task-executor
# writes the final 1.0 — the UI shows "queued..." then suddenly "DONE",
# never the intermediate steps. Observed 2026-06-30.
#
# Each hypercorn worker spawns these threads, but a Redis distributed lock
# (`update_progress` / `flush_token_usage`) ensures only ONE worker across
# the whole pod fleet actually does the work. Idle workers acquire the lock,
# fail, and sleep. Safe.
import threading

def _start_update_progress_thread():
    # Lazy import — éviter d'exécuter le top-level de api.ragflow_server au
    # chargement de ce module (qui pourrait interférer avec settings/imports
    # déjà résolus, soupçon de cause au bug 2026-06-30 STORAGE_IMPL=None).
    import logging
    from api.ragflow_server import update_progress
    logging.info("hypercorn: starting update_progress thread")
    threading.Thread(target=update_progress, daemon=True, name="update_progress").start()

def _start_flush_token_usage_thread():
    import logging
    from api.ragflow_server import flush_token_usage
    logging.info("hypercorn: starting flush_token_usage thread")
    threading.Thread(target=flush_token_usage, daemon=True, name="flush_token_usage").start()

# Démarrer maintenant — à ce stade tout l'init asgi.py est fini (settings,
# DB, RBAC, blueprints, runtime config, plugins). Plus besoin de Timer
# (qui ajoutait 1-2s de latence non-déterministe au boot).
_start_update_progress_thread()
_start_flush_token_usage_thread()
