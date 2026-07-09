"""LiteLLMClient unit tests — httpx.MockTransport, zero network."""
import json
import httpx
import pytest

pytestmark = pytest.mark.p1


def make_client(handler):
    from management.server.services.litellm_client import LiteLLMClient
    return LiteLLMClient(
        base_url="http://litellm.test",
        master_key="sk-master-test",
        transport=httpx.MockTransport(handler),
    )


def test_aliases_are_deterministic():
    from management.server.services.litellm_client import LiteLLMClient
    assert LiteLLMClient.team_alias("org1", "teamA") == "org:org1:team:teamA"
    assert LiteLLMClient.key_alias("org1", "keyB") == "org:org1:key:keyB"


def test_create_team_sends_auth_and_returns_team_id():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"team_id": "llm-team-123"})

    client = make_client(handler)
    team_id = client.create_team(alias="org:o:team:t", max_budget=100.0,
                                 budget_duration="1mo", models=["code-model"])
    assert team_id == "llm-team-123"
    assert seen["auth"] == "Bearer sk-master-test"
    assert seen["path"] == "/team/new"
    assert seen["body"]["team_alias"] == "org:o:team:t"
    assert seen["body"]["max_budget"] == 100.0
    assert seen["body"]["budget_duration"] == "1mo"
    assert seen["body"]["models"] == ["code-model"]


def test_generate_key_never_returns_plaintext_in_token_fields():
    def handler(request):
        return httpx.Response(200, json={"key": "sk-abcdef1234567890", "token": "hashed-token-xyz"})

    client = make_client(handler)
    out = client.generate_key(team_id="llm-team-123", alias="org:o:key:k")
    assert out["plain_key"] == "sk-abcdef1234567890"
    assert out["token"] == "hashed-token-xyz"
    assert out["masked"] == "sk-abc...7890"
    assert out["plain_key"] not in out["masked"]


def test_http_error_raises_litellm_error():
    from management.server.services.litellm_client import LiteLLMError

    def handler(request):
        return httpx.Response(500, text="boom")

    client = make_client(handler)
    with pytest.raises(LiteLLMError):
        client.block_key("hashed-token-xyz")


def test_network_error_raises_litellm_error():
    from management.server.services.litellm_client import LiteLLMError

    def handler(request):
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LiteLLMError):
        client.team_info("llm-team-123")


def test_generate_key_without_token_raises_litellm_error():
    """A missing/empty token identifier must be a hard failure — silently
    returning "" would let revoke_code_key() skip block_key() while still
    marking sync_status='synced' (silent security hole)."""
    from management.server.services.litellm_client import LiteLLMError

    def handler(request):
        return httpx.Response(200, json={"key": "sk-abcdef1234567890"})

    client = make_client(handler)
    with pytest.raises(LiteLLMError):
        client.generate_key(team_id="llm-team-123", alias="org:o:key:k")


def test_find_team_by_alias_filters_client_side():
    def handler(request):
        assert request.url.path == "/team/list"
        return httpx.Response(200, json=[
            {"team_id": "t1", "team_alias": "org:o:team:a"},
            {"team_id": "t2", "team_alias": "org:o:team:b"},
        ])

    client = make_client(handler)
    assert client.find_team_by_alias("org:o:team:b") == "t2"
    assert client.find_team_by_alias("org:o:team:zzz") is None


def test_list_teams_returns_raw_list():
    def handler(request):
        assert request.url.path == "/team/list"
        return httpx.Response(200, json=[{"team_id": "t1", "team_alias": "a", "spend": 3.25}])

    client = make_client(handler)
    teams = client.list_teams()
    assert teams == [{"team_id": "t1", "team_alias": "a", "spend": 3.25}]


def test_daily_usage_aggregates_tokens_and_errors_per_team():
    """Mock shape reflects reality observed against a live
    ghcr.io/berriai/litellm:main-v1.74.0-stable container (see
    daily_usage docstring): flat per-request rows with top-level
    team_id/status/total_tokens, summarize=false, end_date = day + 1."""
    import datetime

    def handler(request):
        assert request.url.path == "/spend/logs"
        params = dict(request.url.params)
        assert params["start_date"] == "2026-07-09"
        assert params["end_date"] == "2026-07-10"
        assert params["summarize"] == "false"
        return httpx.Response(200, json=[
            {"team_id": "t1", "total_tokens": 30, "status": "success"},
            {"team_id": "t1", "total_tokens": 0, "status": "failure"},
            {"team_id": "t2", "total_tokens": 5, "status": "success"},
        ])

    client = make_client(handler)
    usage = client.daily_usage(datetime.date(2026, 7, 9))
    assert usage["t1"] == {"tokens": 30, "errors": 1}
    assert usage["t2"] == {"tokens": 5, "errors": 0}


def test_daily_usage_no_traffic_returns_empty_dict():
    import datetime

    def handler(request):
        return httpx.Response(200, json=[])

    client = make_client(handler)
    assert client.daily_usage(datetime.date(2026, 7, 9)) == {}


def test_daily_usage_rows_without_team_id_are_skipped():
    import datetime

    def handler(request):
        return httpx.Response(200, json=[
            {"team_id": "", "total_tokens": 100, "status": "success"},
            {"team_id": "t1", "total_tokens": 10, "status": "success"},
        ])

    client = make_client(handler)
    usage = client.daily_usage(datetime.date(2026, 7, 9))
    assert usage == {"t1": {"tokens": 10, "errors": 0}}


def test_daily_usage_gateway_down_raises():
    from management.server.services.litellm_client import LiteLLMError
    import datetime

    def handler(request):
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LiteLLMError):
        client.daily_usage(datetime.date(2026, 7, 9))
