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
