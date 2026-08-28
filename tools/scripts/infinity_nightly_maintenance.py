#!/usr/bin/env python3
"""Maintenance nocturne Infinity : optimize + compact de toutes les tables.

CUSTOM B2B SaaS — fenêtre de maintenance planifiée (CronJob K8s, cf.
helm/ragflow/templates/infinity-maintenance-cronjob.yaml). La maintenance
périodique d'Infinity (optimize/compact) entre en conflit OCC avec les
écritures — incidents 2026-08-19/20 (issue infiniflow/infinity#3418). En
mode « fenêtre » : les intervalles périodiques sont montés à ~24h dans la
config serveur, et ce script fait le ménage la nuit, table par table, quand
personne n'écrit. Idempotent, best-effort : une table qui échoue n'empêche
pas les suivantes ; exit 0 si tout passe, 1 sinon.

Usage :
  INFINITY_HOST=rag-new2-infinity.rag-new2.svc.cluster.local \
  INFINITY_THRIFT_PORT=23817 \
  python tools/scripts/infinity_nightly_maintenance.py
"""

import logging
import os
import sys
import time

import infinity
from infinity.common import NetworkAddress

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("infinity-maintenance")


def main() -> int:
    host = os.environ.get("INFINITY_HOST", "localhost")
    port = int(os.environ.get("INFINITY_THRIFT_PORT", "23817"))
    db_name = os.environ.get("INFINITY_DB_NAME", "default_db")

    conn = infinity.connect(NetworkAddress(host, port))
    try:
        db = conn.get_database(db_name)
        table_names = list(db.list_tables().table_names)
        log.info("%d tables dans %s", len(table_names), db_name)

        failures = 0
        for name in sorted(table_names):
            t0 = time.time()
            try:
                table = db.get_table(name)
                # Ordre compact -> optimize (audit robustesse 2026-08-28) :
                # compact ne fait que MARQUER (le cleanup détruit plus tard),
                # optimize mute les octets d'index EN PLACE — le faire en
                # dernier minimise la fenêtre où un scan concurrent lirait des
                # octets en cours de mutation. Le restart post-maintenance
                # (cronjob) purge ensuite les caches périmés — non négociable.
                table.compact()
                table.optimize()
                log.info("%s: compact+optimize OK (%.1fs)", name, time.time() - t0)
            except Exception as exc:
                failures += 1
                log.error("%s: échec maintenance: %s", name, exc)

        log.info("terminé: %d/%d tables OK", len(table_names) - failures, len(table_names))
        return 1 if failures else 0
    finally:
        conn.disconnect()


if __name__ == "__main__":
    sys.exit(main())
