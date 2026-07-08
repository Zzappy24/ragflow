"""Integration against a REAL LiteLLM container (docker/litellm-test compose).

Start:  docker compose -f docker/litellm-test/docker-compose.yml up -d
Gate:   LITELLM_TEST_URL=http://localhost:4000 (skipped otherwise)
"""
import os
import time
import uuid
import httpx
import pytest

pytestmark = [
    pytest.mark.p2,
    pytest.mark.skipif(not os.environ.get("LITELLM_TEST_URL"),
                       reason="LITELLM_TEST_URL not set — integration stack not running"),
]

BASE = os.environ.get("LITELLM_TEST_URL", "http://localhost:4000")


@pytest.fixture()
def client():
    from management.server.services.litellm_client import LiteLLMClient
    c = LiteLLMClient(base_url=BASE, master_key="sk-master-test-only")
    yield c
    c.close()


def test_team_create_is_idempotent_by_alias(client):
    alias = f"org:it:team:{uuid.uuid4().hex[:8]}"
    t1 = client.create_team(alias=alias, max_budget=50.0, budget_duration="1mo", models=[])
    t2 = client.find_team_by_alias(alias)
    assert t2 == t1  # retry path finds, not duplicates


def test_key_lifecycle_generate_call_block(client):
    alias = f"org:it:team:{uuid.uuid4().hex[:8]}"
    team_id = client.create_team(alias=alias, max_budget=50.0, budget_duration="1mo", models=[])
    out = client.generate_key(team_id=team_id, alias=f"org:it:key:{uuid.uuid4().hex[:8]}")
    assert out["token"], "generate must return the hashed token used for /key/block"

    # the virtual key reaches the mock model through /v1
    resp = httpx.post(f"{BASE}/v1/chat/completions",
                      headers={"Authorization": f"Bearer {out['plain_key']}"},
                      json={"model": "code-mock",
                            "messages": [{"role": "user", "content": "hi"}]},
                      timeout=30.0)
    assert resp.status_code == 200
    assert "mock" in resp.json()["choices"][0]["message"]["content"].lower()

    # block by hashed token -> key stops working
    client.block_key(out["token"])
    resp = httpx.post(f"{BASE}/v1/chat/completions",
                      headers={"Authorization": f"Bearer {out['plain_key']}"},
                      json={"model": "code-mock",
                            "messages": [{"role": "user", "content": "hi"}]},
                      timeout=30.0)
    assert resp.status_code in (400, 401, 403)


def test_team_budget_blocks_in_real_time(client):
    alias = f"org:it:team:{uuid.uuid4().hex[:8]}"
    team_id = client.create_team(alias=alias, max_budget=0.01,  # busted by ONE call
                                 budget_duration="1mo", models=[])
    out = client.generate_key(team_id=team_id, alias=f"org:it:key:{uuid.uuid4().hex[:8]}")

    def call():
        return httpx.post(f"{BASE}/v1/chat/completions",
                          headers={"Authorization": f"Bearer {out['plain_key']}"},
                          json={"model": "code-mock",
                                "messages": [{"role": "user", "content": "hi " * 50}]},
                          timeout=30.0)

    first = call()
    assert first.status_code == 200  # burns > 0.01 EUR at 0.5/token

    # LiteLLM spend updates can have a short in-memory propagation delay —
    # poll/retry briefly before asserting the block.
    deadline = time.monotonic() + 10.0
    second = call()
    while second.status_code == 200 and time.monotonic() < deadline:
        time.sleep(0.5)
        second = call()
    assert second.status_code == 400
    assert "budget" in second.text.lower()
