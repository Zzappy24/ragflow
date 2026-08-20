"""Contrat du patch no-replay des mutations Infinity (CUSTOM B2B SaaS).

Incident prod 2026-08-20 : le retry_wrapper du SDK rejoue insert/delete
après un timeout transport pendant que la 1re tentative tourne encore côté
serveur (session zombie) → conflits "Delete N vs. Delete N" (mêmes lignes),
aborts en cascade, commits gelés. Un INSERT rejoué produirait des chunks
dupliqués. Le patch (common/doc_store/infinity_conn_pool.py) remplace
insert/delete du client thrift : UNE tentative, reconnexion, propagation.

On charge la tête du module (avant le singleton de pool, qui exige un
serveur) et on vérifie : application du patch sur le SDK installé + la
sémantique une-tentative. Si un bump du SDK casse @wraps/__wrapped__ ou les
attributs utilisés, ces tests le détectent avant la prod.

Run:
    RAGFLOW_TEST_LOCAL_AUTH=1 uv run python -m pytest \
        test/multitenant/test_infinity_no_replay.py -v
"""
from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

infinity_sdk = pytest.importorskip("infinity.remote_thrift.client")
from infinity.common import InfinityException  # noqa: E402
from thrift.transport.TTransport import TTransportException  # noqa: E402

_SRC = (REPO / "common" / "doc_store" / "infinity_conn_pool.py").read_text()
# La tête du module contient les patchs classe ; le singleton de pool (plus
# bas) exige un serveur joignable — on s'arrête avant.
_HEAD = _SRC[: _SRC.index("@singleton")]
_NS: dict = {}
exec(compile(_HEAD, "infinity_conn_pool_head", "exec"), _NS)


class _Lock:
    def gen_rlock(self):
        return nullcontext()

    def gen_wlock(self):
        return nullcontext()


class _Client:
    lock = _Lock()

    def __init__(self):
        self.session_i = 0
        self.calls = 0
        self.reconnects = 0

    def _reconnect(self):
        self.reconnects += 1


def test_sdk_mutations_are_patched():
    cls = infinity_sdk.ThriftInfinityClient
    assert cls.insert.__code__.co_filename == "infinity_conn_pool_head"
    assert cls.delete.__code__.co_filename == "infinity_conn_pool_head"


def test_timeout_is_not_replayed_and_reconnects():
    c = _Client()

    def failing(self, *a, **k):
        self.calls += 1
        raise TTransportException(message="read timeout")

    with pytest.raises(InfinityException) as ei:
        _NS["_no_replay_mutation"](failing, "insert")(c)
    assert c.calls == 1, "la mutation ne doit JAMAIS être rejouée sur timeout"
    assert c.reconnects == 1, "la socket doit être resaine pour l'appel suivant"
    assert "NOT replayed" in str(getattr(ei.value, "error_message", ei.value))


def test_success_passthrough():
    c = _Client()

    def ok(self, *a, **k):
        self.calls += 1
        return "res"

    assert _NS["_no_replay_mutation"](ok, "delete")(c) == "res"
    assert c.calls == 1


def test_non_transport_error_propagates_untouched():
    c = _Client()

    def boom(self, *a, **k):
        raise ValueError("bug applicatif")

    with pytest.raises(ValueError):
        _NS["_no_replay_mutation"](boom, "insert")(c)
    assert c.reconnects == 0, "pas de reconnexion sur une erreur non-transport"
