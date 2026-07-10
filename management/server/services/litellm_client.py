"""LiteLLM management-API client — the Code product's headless data plane.

The panel is the source of truth; LiteLLM only holds Teams + virtual Keys.
All calls are server-to-server with the MASTER_KEY (never exposed to the front).
Deterministic aliases make create operations idempotent (spec §5).
"""
import datetime

import httpx

from management.server.config import settings


class LiteLLMError(Exception):
    """LiteLLM unreachable or returned an error. Callers keep sync_status=pending."""


class LiteLLMClient:
    def __init__(self, base_url: str | None = None, master_key: str | None = None,
                 timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self.base_url = (base_url or settings.LITELLM_BASE_URL).rstrip("/")
        master = master_key if master_key is not None else settings.LITELLM_MASTER_KEY
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {master}"},
            transport=transport,
        )

    # ---- deterministic aliases (idempotency, spec §5) ----
    @staticmethod
    def team_alias(org_id: str, code_team_id: str) -> str:
        return f"org:{org_id}:team:{code_team_id}"

    @staticmethod
    def key_alias(org_id: str, code_key_id: str) -> str:
        return f"org:{org_id}:key:{code_key_id}"

    # ---- low-level ----
    def _request(self, method: str, path: str, **kwargs):
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise LiteLLMError(f"LiteLLM unreachable ({method} {path}): {e}") from e
        if resp.status_code >= 400:
            raise LiteLLMError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}

    # ---- teams ----
    def list_teams(self) -> list[dict]:
        """Raw /team/list — each item carries team_id, team_alias and accrued spend."""
        return self._request("GET", "/team/list")

    def find_team_by_alias(self, alias: str) -> str | None:
        for t in self.list_teams():
            if t.get("team_alias") == alias:
                return t.get("team_id")
        return None

    def create_team(self, *, alias: str, max_budget: float,
                    budget_duration: str, models: list[str]) -> str:
        payload = {"team_alias": alias, "max_budget": max_budget,
                   "budget_duration": budget_duration}
        if models:
            payload["models"] = models
        return self._request("POST", "/team/new", json=payload)["team_id"]

    def update_team(self, *, team_id: str, max_budget: float | None = None,
                    models: list[str] | None = None) -> None:
        payload: dict = {"team_id": team_id}
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if models is not None:
            payload["models"] = models
        self._request("POST", "/team/update", json=payload)

    def team_info(self, team_id: str) -> dict:
        return self._request("GET", "/team/info", params={"team_id": team_id})

    # ---- keys ----
    def generate_key(self, *, team_id: str, alias: str) -> dict:
        data = self._request("POST", "/key/generate",
                             json={"team_id": team_id, "key_alias": alias})
        plain = data["key"]
        # hashed identifier usable with /key/block — integration test (Task 7)
        # validates this against the real container.
        token = data.get("token") or data.get("token_id") or ""
        if not token:
            # An empty litellm_key_id would make revoke_code_key() silently
            # skip block_key() while still marking sync_status='synced' —
            # a silent security hole (a "revoked" key stays live upstream).
            raise LiteLLMError(
                "key/generate returned no token identifier — cannot manage this key")
        return {
            "plain_key": plain,
            "token": token,
            "masked": f"{plain[:6]}...{plain[-4:]}",
        }

    def block_key(self, token: str) -> None:
        self._request("POST", "/key/block", json={"key": token})

    def unblock_key(self, token: str) -> None:
        self._request("POST", "/key/unblock", json={"key": token})

    def list_keys(self, team_id: str) -> list[dict]:
        data = self._request("GET", "/key/list", params={"team_id": team_id})
        return data.get("keys", data) if isinstance(data, dict) else data

    # ---- usage ----
    def daily_usage(self, day: datetime.date) -> dict[str, dict]:
        """Tokens + errors per team for the UTC day `day`.

        Validated against ghcr.io/berriai/litellm main-v1.74.0-stable AND v1.91.1
        (only drift between the two: budget-exceeded on /v1 is 400 -> 429):
        GET /spend/logs?start_date=<day>&end_date=<day+1>&summarize=false
        returns a flat JSON array of raw per-request log rows — NOT
        aggregated. `summarize=true` (the default) instead groups rows by
        (api_key, user, model, startTime), which is useless for per-team
        totals, so summarize=false is required.

        start_date/end_date are parsed server-side as UTC midnight
        (`datetime.strptime(x, "%Y-%m-%d")`) and the filter is
        `startTime BETWEEN start_date AND end_date` — start_date == end_date
        == day matches nothing (a zero-width instant at midnight), so
        end_date must be day + 1 to cover the whole day.

        Each row carries `team_id`, `status` ("success"/"failure") and
        `total_tokens` (already prompt+completion) at the top level.
        Raises LiteLLMError when the gateway is unreachable or rejects the
        request (e.g. bad auth) — callers map that to None/NULL.

        NOTE: the response is UNBOUNDED (no pagination param on this endpoint
        in the pinned version) — fine for code-product daily volumes (<~5K
        req/day); past that, add a TTL cache or serve from snapshots instead.
        """
        params = {
            "start_date": day.isoformat(),
            "end_date": (day + datetime.timedelta(days=1)).isoformat(),
            "summarize": "false",
        }
        rows = self._request("GET", "/spend/logs", params=params)
        out: dict[str, dict] = {}
        for r in rows:
            tid = r.get("team_id")
            if not tid:
                continue
            agg = out.setdefault(tid, {"tokens": 0, "errors": 0})
            agg["tokens"] += int(r.get("total_tokens") or 0)
            if (r.get("status") or "success") != "success":
                agg["errors"] += 1
        return out

    # ---- lifecycle ----
    def close(self) -> None:
        """Release the underlying HTTP connection pool (real sockets in integration use)."""
        self._client.close()
