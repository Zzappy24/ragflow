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
    # v1.74 renvoyait 400 ; v1.91+ renvoie 429 (sémantiquement plus juste).
    # Les deux signifient « budget dépassé, requête refusée » côté client.
    assert second.status_code in (400, 429)
    assert "budget" in second.text.lower()


def test_daily_usage_counts_real_traffic(client):
    """Validates daily_usage() against a live container: GET /spend/logs
    with summarize=false, per-request rows aggregated by team_id (see
    LiteLLMClient.daily_usage docstring for the fully observed shape)."""
    import datetime

    alias = f"org:it:team:{uuid.uuid4().hex[:8]}"
    team_id = client.create_team(alias=alias, max_budget=50.0, budget_duration="1mo", models=[])
    out = client.generate_key(team_id=team_id, alias=f"org:it:key:{uuid.uuid4().hex[:8]}")
    for _ in range(2):
        resp = httpx.post(f"{BASE}/v1/chat/completions",
                          headers={"Authorization": f"Bearer {out['plain_key']}"},
                          json={"model": "code-mock",
                                "messages": [{"role": "user", "content": "hi"}]},
                          timeout=30.0)
        assert resp.status_code == 200

    today = datetime.datetime.now(datetime.timezone.utc).date()
    deadline = time.monotonic() + 15.0
    usage = client.daily_usage(today)
    while team_id not in usage and time.monotonic() < deadline:
        time.sleep(1.0)
        usage = client.daily_usage(today)

    assert team_id in usage, "spend-logs never surfaced our team's traffic"
    assert usage[team_id]["tokens"] > 0
    assert usage[team_id]["errors"] == 0


def test_key_list_returns_full_objects_with_spend(client):
    """list_keys() doit renvoyer des OBJETS (token/key_alias/spend), pas des
    hashes nus : /key/list sans return_full_object=true renvoie des strings
    (v1.91.1) et le per-key spend du panel devient silencieusement '—'."""
    alias = f"org:it:team:{uuid.uuid4().hex[:8]}"
    team_id = client.create_team(alias=alias, max_budget=50.0, budget_duration="1mo", models=[])
    key_alias = f"org:it:key:{uuid.uuid4().hex[:8]}"
    out = client.generate_key(team_id=team_id, alias=key_alias)

    keys = client.list_keys(team_id)
    assert keys and all(isinstance(k, dict) for k in keys), \
        f"expected full objects, got: {keys[:2]}"
    match = [k for k in keys if k.get("token") == out["token"]]
    assert match, "generated key's hashed token must appear in list_keys"
    assert match[0].get("key_alias") == key_alias
    assert "spend" in match[0] and float(match[0]["spend"] or 0.0) >= 0.0


def test_seat_budget_blocks_while_team_budget_remains(client):
    """Budget par siège : la clé est bloquée par SA limite même si la team a
    encore du budget — c'est l'enforcement temps réel qui protège la team
    d'un agent qui boucle sur un seul siège."""
    alias = f"org:it:team:{uuid.uuid4().hex[:8]}"
    team_id = client.create_team(alias=alias, max_budget=50.0, budget_duration="1mo", models=[])
    out = client.generate_key(team_id=team_id, alias=f"org:it:key:{uuid.uuid4().hex[:8]}",
                              max_budget=0.01, budget_duration="1mo", rpm_limit=1000)

    # la limite est bien posée côté LiteLLM (objet complet du /key/list)
    keys = client.list_keys(team_id)
    me = [k for k in keys if k.get("token") == out["token"]][0]
    assert float(me["max_budget"]) == 0.01
    assert int(me["rpm_limit"]) == 1000

    def call():
        return httpx.post(f"{BASE}/v1/chat/completions",
                          headers={"Authorization": f"Bearer {out['plain_key']}"},
                          json={"model": "code-mock",
                                "messages": [{"role": "user", "content": "hi " * 50}]},
                          timeout=30.0)

    first = call()
    assert first.status_code == 200  # crame > 0.01 EUR au tarif mock

    deadline = time.monotonic() + 10.0
    second = call()
    while second.status_code == 200 and time.monotonic() < deadline:
        time.sleep(0.5)
        second = call()
    assert second.status_code in (400, 429)
    assert "budget" in second.text.lower()

    # une AUTRE clé de la même team (sans limite siège) passe toujours :
    # c'est bien le siège qui est bloqué, pas la team.
    other = client.generate_key(team_id=team_id, alias=f"org:it:key:{uuid.uuid4().hex[:8]}")
    resp = httpx.post(f"{BASE}/v1/chat/completions",
                      headers={"Authorization": f"Bearer {other['plain_key']}"},
                      json={"model": "code-mock",
                            "messages": [{"role": "user", "content": "hi"}]},
                      timeout=30.0)
    assert resp.status_code == 200


def test_list_models_returns_public_names(client):
    """Contrat /v1/models : la section « bien démarrer » du claim en dépend."""
    models = client.list_models()
    assert "code-mock" in models
