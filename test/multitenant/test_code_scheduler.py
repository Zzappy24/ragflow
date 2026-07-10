"""Scheduler housekeeping — lifespan réellement exercé (leçon asgi.py 2026-06-30)."""
import logging
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.p1


def test_lifespan_disabled_via_env_guard(monkeypatch, caplog):
    monkeypatch.setenv("ADMIN_CODE_SCHEDULER", "0")
    from management.server.main import app
    with caplog.at_level(logging.INFO):
        with TestClient(app):
            pass
    assert any("DISABLED" in r.message for r in caplog.records)


def test_lifespan_starts_scheduler_sleep_first(monkeypatch, caplog):
    """Guard=1, intervalle 3600 : la task démarre (log) mais ne run PAS au boot."""
    monkeypatch.setenv("ADMIN_CODE_SCHEDULER", "1")
    monkeypatch.setenv("ADMIN_CODE_SCHEDULER_INTERVAL_S", "3600")
    from management.server.main import app
    with caplog.at_level(logging.INFO):
        with TestClient(app):
            pass
    assert any("code housekeeping scheduler started (interval=3600s)" in r.message
               for r in caplog.records)
    # sleep-first: aucun run au boot → aucun log de run
    assert not any("code housekeeping run" in r.message for r in caplog.records)


def test_scheduler_loop_runs_and_survives_failures(monkeypatch, caplog):
    """Intervalle court + housekeeping mocké : 1er run échoue (la boucle survit), 2e run OK."""
    calls = {"n": 0}

    def fake_housekeeping():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"teams_snapshotted": 0, "teams_synced": 0, "keys_synced": 0,
                "errors": 0, "ran_at": "x"}

    import management.server.services.code_housekeeping as hk
    monkeypatch.setattr(hk, "housekeeping", fake_housekeeping)
    monkeypatch.setenv("ADMIN_CODE_SCHEDULER", "1")
    monkeypatch.setenv("ADMIN_CODE_SCHEDULER_INTERVAL_S", "1")
    from management.server.main import app
    import time
    with caplog.at_level(logging.INFO):
        with TestClient(app):
            time.sleep(2.5)  # laisse passer ≥2 ticks
    assert calls["n"] >= 2  # le 1er échec n'a pas tué la boucle
    assert any("failed" in r.message.lower() for r in caplog.records)
