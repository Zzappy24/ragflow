#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

print("Start RAGFlow server...")

import time
start_ts = time.time()

import logging
import os
import signal
import sys
import threading
import uuid
import faulthandler

from api.apps import app, current_user
from api.db.runtime_config import RuntimeConfig
from api.db.services.document_service import DocumentService
from common.file_utils import get_project_base_directory
from common import settings
from api.db.db_models import init_database_tables as init_web_db
from api.db.init_data import init_web_data, init_superuser
from common.versions import get_ragflow_version
from common.config_utils import show_configs
from common.mcp_tool_call_conn import shutdown_all_mcp_sessions
from common.log_utils import init_root_logger
from agent.plugin import GlobalPluginManager
from rag.utils.redis_conn import RedisDistributedLock

stop_event = threading.Event()


def flush_token_usage():
    """Flush daily token usage counters from Redis to MySQL every 5 minutes."""
    from datetime import date
    from rag.utils.redis_conn import REDIS_CONN
    from api.db.db_models import TokenUsageDaily, DB

    lock_value = str(uuid.uuid4())
    redis_lock = RedisDistributedLock("flush_token_usage", lock_value=lock_value, timeout=120)

    while not stop_event.is_set():
        try:
            if redis_lock.acquire():
                try:
                    pattern = "token_usage:*"
                    cursor = 0
                    keys = []
                    while True:
                        cursor, batch = REDIS_CONN.REDIS.scan(cursor, match=pattern, count=100)
                        keys.extend(batch)
                        if cursor == 0:
                            break

                    if keys:
                        # CUSTOM PERF: one pipeline for all GET+DELETE instead of N pipelines.
                        # key format: token_usage:{date}:{tenant_id}:{llm_factory}:{model_type}:{llm_name}
                        pipe = REDIS_CONN.REDIS.pipeline()
                        for key in keys:
                            pipe.get(key)
                            pipe.delete(key)
                        results = pipe.execute()  # [get0, del0, get1, del1, ...]

                        with DB.connection_context():
                            for i, key in enumerate(keys):
                                parts = key.split(":", 6)
                                if len(parts) != 6:
                                    continue
                                _, day, tenant_id, llm_factory, model_type, llm_name = parts
                                tokens = int(results[i * 2] or 0)
                                if tokens <= 0:
                                    continue
                                (TokenUsageDaily
                                 .insert(
                                     tenant_id=tenant_id,
                                     llm_factory=llm_factory,
                                     model_type=model_type,
                                     llm_name=llm_name,
                                     date=day,
                                     tokens=tokens,
                                 )
                                 .on_conflict(
                                     update={TokenUsageDaily.tokens: TokenUsageDaily.tokens + tokens},
                                 )
                                 .execute())
                finally:
                    redis_lock.release()
        except Exception:
            logging.exception("flush_token_usage exception")
        stop_event.wait(30)  # flush every 30 seconds

RAGFLOW_DEBUGPY_LISTEN = int(os.environ.get('RAGFLOW_DEBUGPY_LISTEN', "0"))

def update_progress():
    lock_value = str(uuid.uuid4())
    redis_lock = RedisDistributedLock("update_progress", lock_value=lock_value, timeout=60)
    logging.info(f"update_progress lock_value: {lock_value}")
    while not stop_event.is_set():
        try:
            if redis_lock.acquire():
                DocumentService.update_progress()
                redis_lock.release()
        except Exception:
            logging.exception("update_progress exception")
        finally:
            try:
                redis_lock.release()
            except Exception:
                logging.exception("update_progress exception")
            stop_event.wait(6)

def signal_handler(sig, frame):
    logging.info("Received interrupt signal, shutting down...")
    shutdown_all_mcp_sessions()
    stop_event.set()
    stop_event.wait(1)
    sys.exit(0)

if __name__ == '__main__':
    faulthandler.enable()
    init_root_logger("ragflow_server")
    logging.info(r"""
        ____   ___    ______ ______ __
       / __ \ /   |  / ____// ____// /____  _      __
      / /_/ // /| | / / __ / /_   / // __ \| | /| / /
     / _, _// ___ |/ /_/ // __/  / // /_/ /| |/ |/ /
    /_/ |_|/_/  |_|\____//_/    /_/ \____/ |__/|__/

    """)
    logging.info(
        f'RAGFlow version: {get_ragflow_version()}'
    )
    logging.info(
        f'project base: {get_project_base_directory()}'
    )
    show_configs()
    settings.init_settings()
    settings.print_rag_settings()

    if RAGFLOW_DEBUGPY_LISTEN > 0:
        logging.info(f"debugpy listen on {RAGFLOW_DEBUGPY_LISTEN}")
        import debugpy
        debugpy.listen(("0.0.0.0", RAGFLOW_DEBUGPY_LISTEN))

    # init db
    init_web_db()
    init_web_data()

    # RBAC: install retriever proxy + inject user_id into request context
    from api.apps.extensions.rbac_retriever import install_rbac_proxy
    install_rbac_proxy()

    @app.before_request
    async def _rbac_resolve_tenant():
        """
        Resolve the active tenant for this request.

        ``current_user`` is a Flask-Login lazy proxy that may not be
        authenticated yet at ``before_request`` time (the ``@login_required``
        decorator on each route does the actual auth check later). So we
        stash the raw X-Workspace-Id header now, and defer the full
        resolution to ``active_tenant_id()`` when it is first called inside
        the route handler — at which point ``current_user`` is guaranteed to
        be available.
        """
        from quart import g, request
        # Stash the raw header — resolution happens lazily in
        # ``tenant_context.active_tenant_id()`` via ``_resolve_tenant()``.
        g._ws_header = request.headers.get("X-Workspace-Id") or None
        g._tenant_resolved = False
        g.active_tenant_id = None
        g.rbac_user_id = None
    # init runtime config
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version", default=False, help="RAGFlow version", action="store_true"
    )
    parser.add_argument(
        "--debug", default=False, help="debug mode", action="store_true"
    )
    parser.add_argument(
        "--init-superuser", default=False, help="init superuser", action="store_true"
    )
    args = parser.parse_args()
    if args.version:
        print(get_ragflow_version())
        sys.exit(0)

    if args.init_superuser:
        init_superuser()
    RuntimeConfig.DEBUG = args.debug
    if RuntimeConfig.DEBUG:
        logging.info("run on debug mode")

    RuntimeConfig.init_env()
    RuntimeConfig.init_config(JOB_SERVER_HOST=settings.HOST_IP, HTTP_PORT=settings.HOST_PORT)

    GlobalPluginManager.load_plugins()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    def delayed_start_update_progress():
        logging.info("Starting update_progress thread (delayed)")
        t = threading.Thread(target=update_progress, daemon=True)
        t.start()

    def delayed_start_flush_token_usage():
        logging.info("Starting flush_token_usage thread (delayed)")
        t = threading.Thread(target=flush_token_usage, daemon=True)
        t.start()

    if RuntimeConfig.DEBUG:
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            threading.Timer(1.0, delayed_start_update_progress).start()
            threading.Timer(2.0, delayed_start_flush_token_usage).start()
    else:
        threading.Timer(1.0, delayed_start_update_progress).start()
        threading.Timer(2.0, delayed_start_flush_token_usage).start()

    # start http server
    try:
        logging.info(f"RAGFlow server is ready after {time.time() - start_ts}s initialization.")
        app.run(host=settings.HOST_IP, port=settings.HOST_PORT, use_reloader=RuntimeConfig.DEBUG, debug=False)
    except Exception as e:
        logging.exception(f"Unhandled exception: {e}")
        stop_event.set()
        stop_event.wait(1)
        os.kill(os.getpid(), signal.SIGKILL)
