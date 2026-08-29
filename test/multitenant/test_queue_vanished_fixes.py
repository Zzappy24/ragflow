"""Pins du fix v0.9.16 « messages disparus » (queue-vanished).

Trois incidents (2026-08-19/28/29) : des tâches mises en file n'étaient
jamais livrées — docs figés RUNNING à ~0.008, retry_count=0. Dossier :
docs/known-issues/queue-vanished-tasks-evidence.md. Trois protections :

1. Pagination XAUTOCLAIM (rag/utils/redis_conn.py::xautoclaim_all) — une
   page sans message éligible ne termine PAS le scan ; seul le curseur
   "0-0" le fait.
2. ACK non destructeur (rag/svr/task_executor.py) — collect() ne consomme
   un message « unknown » que si l'écartement est PERMANENT (row absente,
   retries épuisés, annulée) ; la contention row-lock laisse le message.
3. Filet de re-mise en file (api/db/services/task_service.py::
   should_requeue_vanished) — tâche jamais livrée + file VIDE = message
   matériellement disparu → re-queue sans risque de doublon.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from common import settings  # noqa: F401,E402 — casse le cycle d'import redis_conn ↔ settings
from rag.utils.redis_conn import xautoclaim_all  # noqa: E402


def _xautoclaim_all():
    return xautoclaim_all


class FakeRedisPaged:
    """XAUTOCLAIM scripté : pages (next_cursor, claimed, deleted)."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def xautoclaim(self, queue, group, consumer, min_idle_time, start_id, count):
        self.calls.append(start_id)
        return self.pages.pop(0)


class TestXautoclaimPagination:
    def test_empty_page_does_not_stop_the_scan(self):
        """LE bug : page 1 sans éligible (baux frais), les orphelins derrière.

        L'ancien code s'arrêtait sur `not claimed` → 0 réclamé. Le scan doit
        continuer jusqu'au curseur "0-0" et réclamer les 2 orphelins.
        """
        fake = FakeRedisPaged([
            ("5-5", [], []),                       # page de baux frais : rien d'éligible
            ("9-9", [("m1", {}), ("m2", {})], []), # les orphelins, derrière
            ("0-0", [], []),
        ])
        assert _xautoclaim_all()(fake, "q", "g", "c") == 2
        assert fake.calls == ["0-0", "5-5", "9-9"]

    def test_stops_on_terminal_cursor(self):
        fake = FakeRedisPaged([("0-0", [("m1", {})], [])])
        assert _xautoclaim_all()(fake, "q", "g", "c") == 1
        assert fake.calls == ["0-0"]

    def test_bytes_cursor_supported(self):
        fake = FakeRedisPaged([(b"3-3", [], []), (b"0-0", [("m1", {})], [])])
        assert _xautoclaim_all()(fake, "q", "g", "c") == 1

    def test_max_pages_backstop(self):
        pages = [(f"{i}-0", [], []) for i in range(1, 50)]
        fake = FakeRedisPaged(pages)
        assert _xautoclaim_all()(fake, "q", "g", "c", max_pages=10) == 0
        assert len(fake.calls) == 10


class TestShouldRequeueVanished:
    def setup_method(self):
        from api.db.services.task_service import should_requeue_vanished
        self.fn = should_requeue_vanished

    def test_old_task_empty_queue_requeues(self):
        assert self.fn(age_s=700, queue_backlog=0, min_age_s=600) is True

    def test_backlog_means_wait(self):
        """File non vide = backlog légitime possible → jamais de doublon."""
        assert self.fn(age_s=7000, queue_backlog=1, min_age_s=600) is False

    def test_young_task_waits(self):
        assert self.fn(age_s=599, queue_backlog=0, min_age_s=600) is False


class TestCollectAckIsNotDestructive:
    """Pin AST : le chemin « unknown » de collect() ne doit plus ACK
    inconditionnellement — l'ack doit être gardé par le check permanent."""

    def test_ack_guarded_by_permanence_check(self):
        with open(os.path.join(REPO, "rag/svr/task_executor.py")) as f:
            src = f.read()
        assert "_is_task_permanently_gone" in src, "le garde de permanence a disparu"
        tree = ast.parse(src)
        guarded = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                cond = ast.dump(node.test)
                if "_is_task_permanently_gone" in cond and "canceled" in cond:
                    body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                    if "ack" in body:
                        guarded = True
        assert guarded, "redis_msg.ack() du chemin unknown doit être sous `canceled or _is_task_permanently_gone(...)`"

    def test_db_failure_keeps_message(self):
        """En cas d'échec DB, _is_task_permanently_gone doit répondre False
        (garder le message) — jamais True (perte définitive)."""
        with open(os.path.join(REPO, "rag/svr/task_executor.py")) as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_is_task_permanently_gone":
                for handler in [n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)]:
                    returns = [r for r in ast.walk(ast.Module(body=handler.body, type_ignores=[])) if isinstance(r, ast.Return)]
                    assert returns, "le handler d'exception doit retourner explicitement"
                    for r in returns:
                        assert isinstance(r.value, ast.Constant) and r.value.value is False
                return
        raise AssertionError("_is_task_permanently_gone introuvable")


class TestRequeueHookWired:
    """Le filet doit rester branché dans le cycle update_progress."""

    def test_update_progress_calls_requeue(self):
        with open(os.path.join(REPO, "api/db/services/document_service.py")) as f:
            src = f.read()
        assert "requeue_vanished_tasks" in src, "filet débranché de update_progress (v0.9.16)"
