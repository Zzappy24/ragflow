"""Contrat du renouvellement de bail des tâches (periodic XAUTOCLAIM).

Épingle le fix `CUSTOM B2B SaaS — periodic XAUTOCLAIM` :
- `RedisMsg.renew_lease` fait un XCLAIM vers soi-même avec justid=True et
  min_idle_time=0 — c'est ce qui remet le compteur idle à zéro SANS
  incrémenter le delivery counter, donc une tâche vivante n'est jamais
  volée par le XAUTOCLAIM périodique (plancher 5 min) d'un pair.
- Un échec Redis ne lève pas : renvoie False (le renouvellement raté
  n'interrompt jamais le traitement de la tâche).
"""

import json

from common import settings  # noqa: F401 — casse le cycle d'import redis_conn ↔ settings
from rag.utils.redis_conn import RedisMsg


class _FakeConsumer:
    def __init__(self, fail=False):
        self.fail = fail
        self.xclaim_calls = []
        self.xack_calls = []

    def xclaim(self, queue, group, consumer, min_idle_time, message_ids, justid):
        if self.fail:
            raise ConnectionError("redis down")
        self.xclaim_calls.append(
            {
                "queue": queue,
                "group": group,
                "consumer": consumer,
                "min_idle_time": min_idle_time,
                "message_ids": message_ids,
                "justid": justid,
            }
        )
        return message_ids

    def xack(self, queue, group, msg_id):
        self.xack_calls.append(msg_id)


def _make_msg(consumer):
    return RedisMsg(consumer, "te.1.common", "rag_flow_svr_task_broker", "123-0", {"message": json.dumps({"id": "t1"})})


class TestRenewLease:
    def test_xclaim_to_self_with_justid(self):
        consumer = _FakeConsumer()
        msg = _make_msg(consumer)
        assert msg.renew_lease("task_executor_0") is True
        assert len(consumer.xclaim_calls) == 1
        call = consumer.xclaim_calls[0]
        assert call["queue"] == "te.1.common"
        assert call["group"] == "rag_flow_svr_task_broker"
        assert call["consumer"] == "task_executor_0"
        # min_idle_time=0 → claim inconditionnel (reset idle) ; justid=True →
        # ne touche PAS le delivery counter (sinon chaque renouvellement
        # rapprocherait la tâche du seuil d'abandon).
        assert call["min_idle_time"] == 0
        assert call["justid"] is True
        assert call["message_ids"] == ["123-0"]

    def test_redis_failure_returns_false_never_raises(self):
        msg = _make_msg(_FakeConsumer(fail=True))
        assert msg.renew_lease("task_executor_0") is False
