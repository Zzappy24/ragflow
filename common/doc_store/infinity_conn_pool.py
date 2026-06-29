#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
import logging
import os
import time

import infinity
from infinity.connection_pool import ConnectionPool
from infinity.errors import ErrorCode

from common import settings
from common.decorator import singleton


@singleton
class InfinityConnectionPool:

    def __init__(self):
        if hasattr(settings, "INFINITY"):
            self.INFINITY_CONFIG = settings.INFINITY
        else:
            self.INFINITY_CONFIG = settings.get_base_config("infinity", {
                "uri": "infinity:23817",
                "postgres_port": 5432,
                "db_name": "default_db"
            })

        # CUSTOM PERF: default tracks WORKER_MAX_TASKS so the pool is never
        # smaller than the number of concurrent ingestion workers on this pod.
        # Formula: INFINITY_POOL_MAX_SIZE >= WORKER_MAX_TASKS (per pod).
        # On K8s, total Infinity connections = nb_pods × INFINITY_POOL_MAX_SIZE —
        # ensure Infinity server max_connections exceeds that product.
        _worker_max_tasks = int(os.environ.get("WORKER_MAX_TASKS", "16"))
        raw_pool_max_size = os.environ.get("INFINITY_POOL_MAX_SIZE", str(_worker_max_tasks))
        try:
            self.pool_max_size = int(raw_pool_max_size)
        except ValueError as e:
            raise ValueError("INFINITY_POOL_MAX_SIZE must be a positive integer") from e
        if self.pool_max_size < 1:
            raise ValueError("INFINITY_POOL_MAX_SIZE must be >= 1")

        infinity_uri = self.INFINITY_CONFIG["uri"]
        if ":" in infinity_uri:
            host, port = infinity_uri.split(":")
            self.infinity_uri = infinity.common.NetworkAddress(host, int(port))

        # CUSTOM B2B SaaS — Wrap the health probe in a thread+timeout so a
        # degraded Infinity (TCP up, gRPC unresponsive — observed on
        # 2026-06-29: Infinity stuck in compact/optimize loop) doesn't
        # block the boot indefinitely. Without this, `show_current_node()`
        # has no timeout in the upstream lib, the API worker hangs forever,
        # K8s startup probe fails but never logs a clear cause.
        import concurrent.futures
        self.conn_pool = None
        for _ in range(24):
            conn_pool = None
            inf_conn = None
            try:
                conn_pool = ConnectionPool(self.infinity_uri, max_size=self.pool_max_size)
                inf_conn = conn_pool.get_conn()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(inf_conn.show_current_node)
                    res = fut.result(timeout=5)
                if res.error_code == ErrorCode.OK and res.server_status in ["started", "alive"]:
                    self.conn_pool = conn_pool
                    break
                logging.warning(f"Infinity status: {res.server_status}. Waiting Infinity {infinity_uri} to be healthy.")
                time.sleep(5)
            except concurrent.futures.TimeoutError:
                logging.warning(f"Infinity {infinity_uri} gRPC unresponsive after 5s (TCP may be up). Retrying...")
                time.sleep(5)
            except Exception as e:
                logging.warning(f"{str(e)}. Waiting Infinity {infinity_uri} to be healthy.")
                time.sleep(5)
            finally:
                if inf_conn is not None and conn_pool is not None:
                    try:
                        conn_pool.release_conn(inf_conn)
                    except Exception:
                        pass
                if conn_pool is not None and conn_pool is not self.conn_pool:
                    try:
                        conn_pool.destroy()
                    except Exception:
                        pass

        if self.conn_pool is None:
            msg = f"Infinity {infinity_uri} is unhealthy in 120s."
            logging.error(msg)
            raise Exception(msg)

        logging.info(f"Infinity {infinity_uri} is healthy. Connection pool max_size={self.pool_max_size}")

    def get_conn_pool(self):
        return self.conn_pool

    def get_conn_uri(self):
        """
        Get connection URI for PostgreSQL protocol.
        """
        infinity_uri = self.INFINITY_CONFIG["uri"]
        postgres_port = self.INFINITY_CONFIG["postgres_port"]
        db_name = self.INFINITY_CONFIG["db_name"]

        if ":" in infinity_uri:
            host, _ = infinity_uri.split(":")
            return f"host={host} port={postgres_port} dbname={db_name}"
        return f"host=localhost port={postgres_port} dbname={db_name}"

    def refresh_conn_pool(self):
        try:
            inf_conn = self.conn_pool.get_conn()
            res = inf_conn.show_current_node()
            if res.error_code == ErrorCode.OK and res.server_status in ["started", "alive"]:
                return self.conn_pool
            else:
                raise Exception(f"{res.error_code}: {res.server_status}")

        except Exception as e:
            logging.error(str(e))
            if hasattr(self, "conn_pool") and self.conn_pool:
                self.conn_pool.destroy()
                self.conn_pool = ConnectionPool(self.infinity_uri, max_size=self.pool_max_size)
                return self.conn_pool

    def __del__(self):
        if hasattr(self, "conn_pool") and self.conn_pool:
            self.conn_pool.destroy()


INFINITY_CONN = InfinityConnectionPool()
