"""Intégration EMPIRIQUE du reclaim périodique + bail, contre un vrai Redis.

Exécute le VRAI code de prod (RedisDB.get_unacked_iterator avec son XAUTOCLAIM
min_idle=300s, RedisMsg.renew_lease) contre le Redis/Valkey de la stack dev
(localhost:6379, `scripts/dev_up.sh`). Les messages sont vieillis
artificiellement via XCLAIM idle=<ms> — sémantique serveur identique au temps
réel — pour ne pas attendre 5 minutes.

Skippé si aucun Redis local ne répond (CI sans stack, poste sans dev_up).

Contrats épinglés (grep `CUSTOM B2B SaaS — periodic XAUTOCLAIM`) :
  A. Worker mort → son message orphelin est réclamé par un pair (fix fantômes).
  B. Tâche vivante renouvelée → PAS volée par le scan d'un pair (bail).
  C. Renouvellements stoppés (mort) → volée à nouveau (récupération ≤7 min).
  D. renew_lease (justid) ne gonfle PAS times_delivered.
"""

import json
import uuid

import pytest
import valkey as redis_lib

from common import settings  # noqa: F401 — casse le cycle redis_conn ↔ settings
from rag.utils import redis_conn as rc

GROUP = "rag_flow_svr_task_broker_leasetest"

R = redis_lib.Valkey(host="localhost", port=6379, decode_responses=True)
try:
    R.ping()
except Exception:
    pytest.skip("Redis/Valkey local (dev stack) indisponible sur 6379", allow_module_level=True)


def _make_db():
    # RedisDB est @singleton (classe → fonction) : on récupère la vraie classe
    # via l'instance module, puis on instancie sans __init__ (qui lirait la
    # conf prod) et on injecte notre client — les méthodes testées n'utilisent
    # que self.REDIS.
    cls = rc.REDIS_CONN.__class__
    db = cls.__new__(cls)
    db.REDIS = R
    setattr(db, "_RedisDB__open__", lambda: None)
    return db


@pytest.fixture()
def stream():
    name = f"te.leasetest.{uuid.uuid4().hex[:8]}"
    R.xgroup_create(name, GROUP, id="0", mkstream=True)
    yield name
    R.delete(name)


def _add_and_deliver(stream, task_id, consumer):
    R.xadd(stream, {"message": json.dumps({"id": task_id})})
    out = R.xreadgroup(GROUP, consumer, {stream: ">"}, count=1)
    assert out, "delivery failed"
    return out[0][1][0][0]


def _age(stream, msg_id, consumer, ms):
    """Vieillit artificiellement le message (même effet serveur que le temps)."""
    R.xclaim(stream, GROUP, consumer, min_idle_time=0, message_ids=[msg_id], idle=ms)


def _scan_as(db, stream, consumer):
    """Un pair lance le scan de prod et retourne les ids récupérés."""
    return [p.get_message().get("id") for p in db.get_unacked_iterator([stream], GROUP, consumer)]


def _delivery_count(stream, msg_id):
    for e in R.xpending_range(stream, GROUP, "-", "+", 100):
        if e["message_id"] == msg_id:
            return e["times_delivered"]
    return None


def test_dead_worker_orphan_is_reclaimed(stream):
    db = _make_db()
    mid = _add_and_deliver(stream, "task-A", "dead_worker")
    _age(stream, mid, "dead_worker", 400_000)  # idle 400s > plancher 300s
    assert _scan_as(db, stream, "live_worker") == ["task-A"]


def test_renewed_live_task_is_never_stolen_then_recovered_after_death(stream):
    db = _make_db()
    mid = _add_and_deliver(stream, "task-B", "busy_worker")
    _age(stream, mid, "busy_worker", 400_000)  # 400s écoulées SANS renouvellement
    msg = rc.RedisMsg(R, stream, GROUP, mid, {"message": json.dumps({"id": "task-B"})})
    assert msg.renew_lease("busy_worker") is True
    # B: le renouvellement a remis idle≈0 → le scan d'un pair ne vole rien
    assert _scan_as(db, stream, "thief_worker") == []
    # C: plus de renouvellement (mort) → l'idle regrimpe → récupérée
    _age(stream, mid, "busy_worker", 400_000)
    assert _scan_as(db, stream, "thief_worker") == ["task-B"]


def test_renew_lease_preserves_delivery_counter(stream):
    mid = _add_and_deliver(stream, "task-D", "worker_d")
    before = _delivery_count(stream, mid)
    msg = rc.RedisMsg(R, stream, GROUP, mid, {"message": json.dumps({"id": "task-D"})})
    for _ in range(5):  # 5 renouvellements = 5 min simulées de tâche longue
        assert msg.renew_lease("worker_d") is True
    assert _delivery_count(stream, mid) == before
