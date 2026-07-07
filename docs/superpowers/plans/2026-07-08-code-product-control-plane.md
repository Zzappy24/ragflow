# Code Product Control Plane — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gérer le produit « Code » (entitlements, code-teams, sièges/keys, budgets) dans le panel management existant, avec LiteLLM OSS headless comme data plane provisionné par API.

**Architecture:** Le panel (FastAPI `management/server` + React `management/web`) est la source de vérité (MariaDB : `code_entitlement`, `code_team`, `code_team_member`, `code_key`). Chaque mutation écrit l'intention en DB (**desired-state-first** : `status` = désiré, `sync_status` = pending/synced/error) puis appelle l'API LiteLLM en synchrone ; un réconciliateur converge les `pending`. Budget org = **invariant d'allocation** (`Σ team budgets ≤ org budget`), enforcement runtime natif LiteLLM par team.

**Tech Stack:** FastAPI, Peewee (modèles dans `api/db/db_models.py`), httpx (+ `httpx.MockTransport` pour les tests), React + antd + axios, LiteLLM OSS pinnée + Postgres en docker compose pour l'intégration.

**Spec:** `docs/superpowers/specs/2026-07-08-code-product-control-plane-design.md`

## Global Constraints

- **Jamais de plaintext de key en DB** — on stocke `litellm_key_id` (token hashé) + `key_masked` ; le plaintext est retourné **une seule fois** par la route de création.
- **Idempotence** : alias déterministes `org:{org_id}:team:{code_team_id}` et `org:{org_id}:key:{code_key_id}`.
- **Cycles alignés** : `budget_duration` des teams = `code_entitlement.budget_period` (imposé, jamais choisi par team).
- **Image mgmt slim** : lazy imports Peewee dans les fonctions (pattern existant), pas de nouveau package lourd. httpx est déjà dans `management/server/requirements.txt`.
- **Tests locaux sans GPU** : unit = LiteLLM mocké (`httpx.MockTransport` / fake client injecté) ; intégration = compose LiteLLM+Postgres avec `mock_response` ; jamais de vLLM local.
- **`MASTER_KEY`** : lue depuis l'env (`LITELLM_MASTER_KEY`), jamais loggée, jamais renvoyée par une route.
- **Déploiement K8s/Helm : hors périmètre de ce plan** (plan séparé — cf. « Follow-ups »).
- Env de test DB : mêmes prérequis que `test/multitenant` (stack `scripts/dev_up.sh`, env vars du README de test).

---

### Task 1: Modèles Peewee (4 tables)

**Files:**
- Modify: `api/db/db_models.py` (fin de la section « RBAC Multi-Tenant Models », après `WsGroupDataset`)
- Test: `test/multitenant/test_code_models.py`

**Interfaces:**
- Produces: classes Peewee `CodeEntitlement`, `CodeTeam`, `CodeTeamMember`, `CodeKey` (importables par les tasks 3-5). Tables auto-créées par `init_database_tables()` (introspection du module — aucun enregistrement manuel).

**Note spec :** la table `code_team_member` est un addendum au §3 du spec (le rôle « code-team admin » du §4 a besoin d'un stockage). Mettre à jour le spec dans cette task.

- [ ] **Step 1: Write the failing test**

```python
# test/multitenant/test_code_models.py
"""Schema smoke test for the Code product tables."""
import pytest

pytestmark = pytest.mark.p1


def test_code_tables_exist_with_expected_columns():
    from api.db.db_models import DB, CodeEntitlement, CodeTeam, CodeTeamMember, CodeKey

    with DB.connection_context():
        cols_ent = {c.name for c in DB.get_columns("code_entitlement")}
        cols_team = {c.name for c in DB.get_columns("code_team")}
        cols_member = {c.name for c in DB.get_columns("code_team_member")}
        cols_key = {c.name for c in DB.get_columns("code_key")}

    assert {"id", "org_id", "status", "org_code_budget", "budget_period", "created_by"} <= cols_ent
    assert {"id", "org_id", "name", "litellm_team_id", "max_budget",
            "model_access", "status", "sync_status", "sync_error", "created_by"} <= cols_team
    assert {"id", "code_team_id", "user_id", "role"} <= cols_member
    assert {"id", "code_team_id", "label", "litellm_key_id", "key_masked",
            "owner_user_id", "status", "sync_status", "sync_error", "created_by"} <= cols_key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'CodeEntitlement'`

- [ ] **Step 3: Add the models**

Dans `api/db/db_models.py`, après la classe `WsGroupDataset` (fin de la section RBAC). `FloatField` et `JSONField` sont déjà utilisés dans ce module.

```python
class CodeEntitlement(DataBaseModel):
    """Code product entitlement — one row per org. Cyllene-controlled (the 'how much')."""
    id = CharField(max_length=32, primary_key=True)
    org_id = CharField(max_length=32, null=False, unique=True, index=True)
    status = CharField(max_length=16, null=False, default="active", index=True)  # active | suspended
    org_code_budget = FloatField(null=False, default=0.0)  # EUR per budget_period
    budget_period = CharField(max_length=8, null=False, default="1mo")  # LiteLLM budget_duration format
    created_by = CharField(max_length=32, null=False)

    class Meta:
        db_table = "code_entitlement"


class CodeTeam(DataBaseModel):
    """A client squad = one LiteLLM Team. status = DESIRED state; sync_status tells if LiteLLM matches."""
    id = CharField(max_length=32, primary_key=True)
    org_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=False)
    litellm_team_id = CharField(max_length=64, null=True, index=True)
    max_budget = FloatField(null=False, default=0.0)  # EUR per entitlement.budget_period (cycle imposed)
    model_access = JSONField(null=True, default=[])  # [] = all models exposed by the proxy
    status = CharField(max_length=16, null=False, default="active", index=True)  # active | deleted
    sync_status = CharField(max_length=16, null=False, default="pending", index=True)  # pending | synced | error
    sync_error = TextField(null=True)
    created_by = CharField(max_length=32, null=False)

    class Meta:
        db_table = "code_team"


class CodeTeamMember(DataBaseModel):
    """Delegated code-team admins (spec §4 'Modèle 1'). role kept for future non-admin roles."""
    id = CharField(max_length=32, primary_key=True)
    code_team_id = CharField(max_length=32, null=False, index=True)
    user_id = CharField(max_length=32, null=False, index=True)
    role = CharField(max_length=16, null=False, default="admin")

    class Meta:
        db_table = "code_team_member"


class CodeKey(DataBaseModel):
    """A seat/dev = one LiteLLM virtual key. NEVER stores the plaintext key.

    litellm_key_id = hashed token returned by /key/generate (usable for /key/block).
    status is the DESIRED state: active | revoked (seat-level) | blocked (org suspension fan-out).
    """
    id = CharField(max_length=32, primary_key=True)
    code_team_id = CharField(max_length=32, null=False, index=True)
    label = CharField(max_length=255, null=False)
    litellm_key_id = CharField(max_length=128, null=True, index=True)
    key_masked = CharField(max_length=32, null=True)
    owner_user_id = CharField(max_length=32, null=True, index=True)
    status = CharField(max_length=16, null=False, default="active", index=True)  # active | revoked | blocked
    sync_status = CharField(max_length=16, null=False, default="pending", index=True)  # pending | synced | error
    sync_error = TextField(null=True)
    created_by = CharField(max_length=32, null=False)

    class Meta:
        db_table = "code_key"
```

- [ ] **Step 4: Create tables and re-run the test**

Run: `PYTHONPATH=. uv run python -c "from api.db.db_models import init_database_tables; init_database_tables()"`
Then: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_models.py -v`
Expected: PASS

- [ ] **Step 5: Update the spec addendum + commit**

Dans le spec §3, ajouter la ligne au tableau :
`| code_team_member | id, code_team_id (FK), user_id (FK), role | délégation « code-team admin » (§4) |`

```bash
git add api/db/db_models.py test/multitenant/test_code_models.py docs/superpowers/specs/2026-07-08-code-product-control-plane-design.md
git commit -m "feat(code): tables code_entitlement/code_team/code_team_member/code_key"
```

---

### Task 2: Client LiteLLM (`litellm_client.py`) + config

**Files:**
- Modify: `management/server/config.py` (2 settings)
- Create: `management/server/services/litellm_client.py`
- Test: `test/multitenant/test_code_litellm_client.py`

**Interfaces:**
- Consumes: `settings.LITELLM_BASE_URL`, `settings.LITELLM_MASTER_KEY`
- Produces:
  - `LiteLLMError(Exception)`
  - `LiteLLMClient(base_url=None, master_key=None, timeout=15.0, transport=None)` avec :
    - `team_alias(org_id: str, code_team_id: str) -> str` (static)
    - `key_alias(org_id: str, code_key_id: str) -> str` (static)
    - `find_team_by_alias(alias: str) -> str | None` (→ `team_id` ou None)
    - `create_team(*, alias: str, max_budget: float, budget_duration: str, models: list[str]) -> str` (→ `team_id`)
    - `update_team(*, team_id: str, max_budget: float | None = None, models: list[str] | None = None) -> None`
    - `generate_key(*, team_id: str, alias: str) -> dict` (→ `{"plain_key", "token", "masked"}`)
    - `block_key(token: str) -> None` / `unblock_key(token: str) -> None`
    - `team_info(team_id: str) -> dict` (contient `spend`)
    - `list_keys(team_id: str) -> list[dict]`

- [ ] **Step 1: Add settings**

Dans `management/server/config.py`, ajouter aux settings existants (même pattern que `RAGFLOW_API_URL`) :

```python
    LITELLM_BASE_URL: str = "http://localhost:4000"
    LITELLM_MASTER_KEY: str = ""  # K8s Secret in prod; never logged, never returned by any route
```

- [ ] **Step 2: Write the failing tests (httpx.MockTransport — no network)**

```python
# test/multitenant/test_code_litellm_client.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_litellm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: management.server.services.litellm_client`

- [ ] **Step 4: Implement the client**

```python
# management/server/services/litellm_client.py
"""LiteLLM management-API client — the Code product's headless data plane.

The panel is the source of truth; LiteLLM only holds Teams + virtual Keys.
All calls are server-to-server with the MASTER_KEY (never exposed to the front).
Deterministic aliases make create operations idempotent (spec §5).
"""
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
    def find_team_by_alias(self, alias: str) -> str | None:
        teams = self._request("GET", "/team/list")
        for t in teams:
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
        return {
            "plain_key": plain,
            # hashed identifier usable with /key/block — integration test (Task 7)
            # validates this against the real container.
            "token": data.get("token") or data.get("token_id") or "",
            "masked": f"{plain[:6]}...{plain[-4:]}",
        }

    def block_key(self, token: str) -> None:
        self._request("POST", "/key/block", json={"key": token})

    def unblock_key(self, token: str) -> None:
        self._request("POST", "/key/unblock", json={"key": token})

    def list_keys(self, team_id: str) -> list[dict]:
        data = self._request("GET", "/key/list", params={"team_id": team_id})
        return data.get("keys", data) if isinstance(data, dict) else data
```

- [ ] **Step 5: Run tests to verify they pass, then commit**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_litellm_client.py -v`
Expected: 6 PASS

```bash
git add management/server/config.py management/server/services/litellm_client.py test/multitenant/test_code_litellm_client.py
git commit -m "feat(code): client LiteLLM headless (aliases idempotents, MockTransport tests)"
```

---

### Task 3: Service de provisioning (`code_provisioning.py`) — invariant + desired-state-first

**Files:**
- Create: `management/server/services/code_provisioning.py`
- Test: `test/multitenant/test_code_provisioning.py`

**Interfaces:**
- Consumes: modèles Task 1, `LiteLLMClient`/`LiteLLMError` Task 2
- Produces (toutes acceptent `client: LiteLLMClient | None = None` pour l'injection de fake en test) :
  - `get_entitlement(org_id: str)` → row ou None
  - `upsert_entitlement(*, org_id, status, org_code_budget, budget_period, actor_id, client=None)` → row. Passage à `suspended` = fan-out `blocked` sur toutes les keys ; retour à `active` = unblock.
  - `allocated_budget(org_id: str, exclude_team_id: str | None = None) -> float`
  - `create_code_team(*, org_id, name, max_budget, model_access, created_by, client=None)` → row (raises `ValueError` si invariant violé ou entitlement absent/suspendu)
  - `update_code_team_budget(*, code_team_id, new_budget, client=None)` → row (re-valide l'invariant)
  - `create_code_key(*, code_team_id, label, owner_user_id, created_by, client=None)` → `(row, plain_key | None)` — plain_key None si LiteLLM down (row reste pending)
  - `revoke_code_key(*, code_key_id, client=None)` → row

- [ ] **Step 1: Write the failing tests**

```python
# test/multitenant/test_code_provisioning.py
"""Unit tests for the code provisioning service — fake LiteLLM client, real dev DB."""
import uuid
import pytest

pytestmark = pytest.mark.p1


class FakeLiteLLM:
    """In-memory stand-in for LiteLLMClient. Records calls; can simulate downtime."""
    def __init__(self, down=False):
        self.down = down
        self.teams: dict[str, dict] = {}
        self.blocked: set[str] = set()
        self.calls: list[tuple] = []

    @staticmethod
    def team_alias(org_id, code_team_id):
        return f"org:{org_id}:team:{code_team_id}"

    @staticmethod
    def key_alias(org_id, code_key_id):
        return f"org:{org_id}:key:{code_key_id}"

    def _maybe_down(self):
        if self.down:
            from management.server.services.litellm_client import LiteLLMError
            raise LiteLLMError("simulated: LiteLLM down")

    def find_team_by_alias(self, alias):
        self._maybe_down()
        for tid, t in self.teams.items():
            if t["alias"] == alias:
                return tid
        return None

    def create_team(self, *, alias, max_budget, budget_duration, models):
        self._maybe_down()
        tid = f"llm-{alias}"
        self.teams[tid] = {"alias": alias, "max_budget": max_budget}
        self.calls.append(("create_team", alias))
        return tid

    def update_team(self, *, team_id, max_budget=None, models=None):
        self._maybe_down()
        self.teams[team_id]["max_budget"] = max_budget
        self.calls.append(("update_team", team_id, max_budget))

    def generate_key(self, *, team_id, alias):
        self._maybe_down()
        self.calls.append(("generate_key", alias))
        return {"plain_key": f"sk-{alias}-secret", "token": f"hash-{alias}", "masked": "sk-...cret"}

    def block_key(self, token):
        self._maybe_down()
        self.blocked.add(token)
        self.calls.append(("block_key", token))

    def unblock_key(self, token):
        self._maybe_down()
        self.blocked.discard(token)
        self.calls.append(("unblock_key", token))


@pytest.fixture()
def org_with_entitlement():
    """Minimal org + active entitlement (100 EUR / 1mo). Cleaned up after."""
    from api.db.db_models import DB, Organisation, CodeEntitlement, CodeTeam, CodeTeamMember, CodeKey
    from common.misc_utils import get_uuid
    org_id = get_uuid()
    with DB.connection_context():
        Organisation.create(id=org_id, name=f"code-test-{org_id[:6]}",
                            slug=f"code-test-{org_id[:6]}", created_by="tester")
        CodeEntitlement.create(id=get_uuid(), org_id=org_id, status="active",
                               org_code_budget=100.0, budget_period="1mo", created_by="tester")
    yield org_id
    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_id)]
        if team_ids:
            CodeKey.delete().where(CodeKey.code_team_id.in_(team_ids)).execute()
            CodeTeamMember.delete().where(CodeTeamMember.code_team_id.in_(team_ids)).execute()
        CodeTeam.delete().where(CodeTeam.org_id == org_id).execute()
        CodeEntitlement.delete().where(CodeEntitlement.org_id == org_id).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


def test_create_team_within_budget_syncs_to_litellm(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="squad-front",
                               max_budget=60.0, model_access=[], created_by="tester", client=fake)
    assert team.sync_status == "synced"
    assert team.litellm_team_id == f"llm-org:{org_with_entitlement}:team:{team.id}"


def test_allocation_invariant_rejects_overcommit(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    cp.create_code_team(org_id=org_with_entitlement, name="a", max_budget=60.0,
                        model_access=[], created_by="tester", client=fake)
    with pytest.raises(ValueError, match="allocation"):
        cp.create_code_team(org_id=org_with_entitlement, name="b", max_budget=50.0,
                            model_access=[], created_by="tester", client=fake)  # 60+50 > 100


def test_create_team_litellm_down_stays_pending(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM(down=True)
    team = cp.create_code_team(org_id=org_with_entitlement, name="squad-back",
                               max_budget=10.0, model_access=[], created_by="tester", client=fake)
    assert team.sync_status == "pending"
    assert team.litellm_team_id is None
    assert team.status == "active"  # desired state persisted first


def test_create_key_returns_plaintext_once_and_stores_only_hash(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    key_row, plain = cp.create_code_key(code_team_id=team.id, label="dev-alice",
                                        owner_user_id=None, created_by="tester", client=fake)
    assert plain and plain.startswith("sk-")
    assert key_row.litellm_key_id.startswith("hash-")
    assert plain not in (key_row.key_masked or "")
    assert key_row.sync_status == "synced"


def test_revoke_key_blocks_in_litellm(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    key_row, _ = cp.create_code_key(code_team_id=team.id, label="dev",
                                    owner_user_id=None, created_by="tester", client=fake)
    revoked = cp.revoke_code_key(code_key_id=key_row.id, client=fake)
    assert revoked.status == "revoked"
    assert revoked.sync_status == "synced"
    assert key_row.litellm_key_id in fake.blocked


def test_suspend_entitlement_fans_out_blocks(org_with_entitlement):
    from management.server.services import code_provisioning as cp
    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    k1, _ = cp.create_code_key(code_team_id=team.id, label="d1", owner_user_id=None,
                               created_by="tester", client=fake)
    k2, _ = cp.create_code_key(code_team_id=team.id, label="d2", owner_user_id=None,
                               created_by="tester", client=fake)
    cp.upsert_entitlement(org_id=org_with_entitlement, status="suspended",
                          org_code_budget=100.0, budget_period="1mo",
                          actor_id="tester", client=fake)
    assert {k1.litellm_key_id, k2.litellm_key_id} <= fake.blocked
    # re-activate → unblock
    cp.upsert_entitlement(org_id=org_with_entitlement, status="active",
                          org_code_budget=100.0, budget_period="1mo",
                          actor_id="tester", client=fake)
    assert not fake.blocked
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_provisioning.py -v`
Expected: FAIL — `ModuleNotFoundError: code_provisioning`

- [ ] **Step 3: Implement the service**

```python
# management/server/services/code_provisioning.py
"""Code product provisioning — panel is the source of truth, LiteLLM the enforcer.

Desired-state-first (spec §5): every mutation persists the DESIRED state in
MariaDB inside a transaction (status + sync_status='pending'), then tries the
LiteLLM call synchronously. Success -> sync_status='synced'. LiteLLM down ->
row stays 'pending' and the reconciler (code_reconcile.py) converges later.

Allocation invariant (spec §3): sum(active team budgets) <= org_code_budget.
Team budget_duration is ALWAYS entitlement.budget_period (cycle alignment).
"""
import logging

from common.misc_utils import get_uuid
from management.server.services.litellm_client import LiteLLMClient, LiteLLMError

logger = logging.getLogger(__name__)


def _client(client=None) -> LiteLLMClient:
    return client if client is not None else LiteLLMClient()


def get_entitlement(org_id: str):
    from api.db.db_models import DB, CodeEntitlement
    with DB.connection_context():
        return CodeEntitlement.get_or_none(CodeEntitlement.org_id == org_id)


def allocated_budget(org_id: str, exclude_team_id: str | None = None) -> float:
    from api.db.db_models import DB, CodeTeam
    from peewee import fn
    with DB.connection_context():
        q = CodeTeam.select(fn.COALESCE(fn.SUM(CodeTeam.max_budget), 0.0)).where(
            (CodeTeam.org_id == org_id) & (CodeTeam.status == "active"))
        if exclude_team_id:
            q = q.where(CodeTeam.id != exclude_team_id)
        return float(q.scalar() or 0.0)


def upsert_entitlement(*, org_id: str, status: str, org_code_budget: float,
                       budget_period: str, actor_id: str, client=None):
    """Create/update the org's entitlement. Suspension fans out key blocks."""
    from api.db.db_models import DB, CodeEntitlement, CodeTeam, CodeKey
    if status not in ("active", "suspended"):
        raise ValueError(f"invalid status: {status}")
    if org_code_budget < allocated_budget(org_id):
        raise ValueError("org_code_budget below current allocation — reduce team budgets first")

    with DB.connection_context():
        row = CodeEntitlement.get_or_none(CodeEntitlement.org_id == org_id)
        was_active = row.status == "active" if row else True
        if row is None:
            row = CodeEntitlement.create(id=get_uuid(), org_id=org_id, status=status,
                                         org_code_budget=org_code_budget,
                                         budget_period=budget_period, created_by=actor_id)
        else:
            CodeEntitlement.update(status=status, org_code_budget=org_code_budget,
                                   budget_period=budget_period).where(
                CodeEntitlement.id == row.id).execute()
            row = CodeEntitlement.get_by_id(row.id)

        # Fan-out on transition (desired state first, then best-effort sync)
        desired_key_status = None
        if was_active and status == "suspended":
            desired_key_status = "blocked"
        elif not was_active and status == "active":
            desired_key_status = "active"
        if desired_key_status is not None:
            team_ids = [t.id for t in CodeTeam.select(CodeTeam.id).where(
                (CodeTeam.org_id == org_id) & (CodeTeam.status == "active"))]
            if team_ids:
                # only touch keys not individually revoked
                CodeKey.update(status=desired_key_status, sync_status="pending").where(
                    (CodeKey.code_team_id.in_(team_ids)) & (CodeKey.status != "revoked")).execute()

    if desired_key_status is not None:
        _sync_pending_keys_for_org(org_id, client=client)
    return get_entitlement(org_id)


def _sync_pending_keys_for_org(org_id: str, client=None) -> None:
    """Best-effort immediate convergence of pending key blocks/unblocks."""
    from api.db.db_models import DB, CodeTeam, CodeKey
    cl = _client(client)
    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select(CodeTeam.id).where(CodeTeam.org_id == org_id)]
        pending = list(CodeKey.select().where(
            (CodeKey.code_team_id.in_(team_ids)) & (CodeKey.sync_status == "pending") &
            (CodeKey.litellm_key_id.is_null(False)))) if team_ids else []
    for key in pending:
        try:
            if key.status in ("blocked", "revoked"):
                cl.block_key(key.litellm_key_id)
            else:
                cl.unblock_key(key.litellm_key_id)
            _mark(key.__class__, key.id, sync_status="synced", sync_error=None)
        except LiteLLMError as e:
            logger.warning("key %s sync deferred: %s", key.id, e)
            _mark(key.__class__, key.id, sync_error=str(e)[:1000])


def _mark(model, row_id: str, **fields) -> None:
    from api.db.db_models import DB
    with DB.connection_context():
        model.update(**fields).where(model.id == row_id).execute()


def create_code_team(*, org_id: str, name: str, max_budget: float,
                     model_access: list[str], created_by: str, client=None):
    from api.db.db_models import DB, CodeTeam
    ent = get_entitlement(org_id)
    if ent is None or ent.status != "active":
        raise ValueError("code entitlement is not active for this org")
    if max_budget <= 0:
        raise ValueError("max_budget must be > 0")
    if allocated_budget(org_id) + max_budget > ent.org_code_budget:
        raise ValueError(
            f"allocation exceeded: {allocated_budget(org_id)} + {max_budget} "
            f"> org budget {ent.org_code_budget}")

    team_id = get_uuid()
    with DB.connection_context():  # desired state FIRST
        CodeTeam.create(id=team_id, org_id=org_id, name=name, max_budget=max_budget,
                        model_access=model_access or [], status="active",
                        sync_status="pending", created_by=created_by)

    cl = _client(client)
    alias = cl.team_alias(org_id, team_id)
    try:
        llm_team_id = cl.find_team_by_alias(alias) or cl.create_team(
            alias=alias, max_budget=max_budget,
            budget_duration=ent.budget_period, models=model_access or [])
        _mark(CodeTeam, team_id, litellm_team_id=llm_team_id, sync_status="synced", sync_error=None)
    except LiteLLMError as e:
        logger.warning("code_team %s creation deferred to reconciler: %s", team_id, e)
        _mark(CodeTeam, team_id, sync_error=str(e)[:1000])

    with DB.connection_context():
        return CodeTeam.get_by_id(team_id)


def update_code_team_budget(*, code_team_id: str, new_budget: float, client=None):
    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == code_team_id)
    if team is None or team.status != "active":
        raise ValueError("code team not found")
    ent = get_entitlement(team.org_id)
    if ent is None:
        raise ValueError("no entitlement for org")
    if new_budget <= 0:
        raise ValueError("max_budget must be > 0")
    if allocated_budget(team.org_id, exclude_team_id=code_team_id) + new_budget > ent.org_code_budget:
        raise ValueError("allocation exceeded")

    _mark(CodeTeam, code_team_id, max_budget=new_budget, sync_status="pending")
    cl = _client(client)
    try:
        if team.litellm_team_id:
            cl.update_team(team_id=team.litellm_team_id, max_budget=new_budget)
            _mark(CodeTeam, code_team_id, sync_status="synced", sync_error=None)
    except LiteLLMError as e:
        _mark(CodeTeam, code_team_id, sync_error=str(e)[:1000])

    with DB.connection_context():
        return CodeTeam.get_by_id(code_team_id)


def create_code_key(*, code_team_id: str, label: str, owner_user_id: str | None,
                    created_by: str, client=None):
    """Returns (row, plain_key). plain_key is None when LiteLLM is down —
    the UI must tell the admin to retry (a pending_create key can NOT be
    completed by the reconciler: the plaintext only exists in the generate
    response, so the reconciler marks such rows sync_status='error' instead)."""
    from api.db.db_models import DB, CodeTeam, CodeKey
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == code_team_id)
    if team is None or team.status != "active":
        raise ValueError("code team not found")
    ent = get_entitlement(team.org_id)
    if ent is None or ent.status != "active":
        raise ValueError("code entitlement is not active for this org")
    if team.litellm_team_id is None:
        raise ValueError("team not yet synced to LiteLLM — retry in a moment")

    key_id = get_uuid()
    with DB.connection_context():  # desired state FIRST
        CodeKey.create(id=key_id, code_team_id=code_team_id, label=label,
                       owner_user_id=owner_user_id, status="active",
                       sync_status="pending", created_by=created_by)

    cl = _client(client)
    try:
        out = cl.generate_key(team_id=team.litellm_team_id,
                              alias=cl.key_alias(team.org_id, key_id))
        _mark(CodeKey, key_id, litellm_key_id=out["token"], key_masked=out["masked"],
              sync_status="synced", sync_error=None)
        plain = out["plain_key"]
    except LiteLLMError as e:
        logger.warning("code_key %s generation failed (LiteLLM down): %s", key_id, e)
        _mark(CodeKey, key_id, sync_error=str(e)[:1000])
        plain = None

    with DB.connection_context():
        return CodeKey.get_by_id(key_id), plain


def revoke_code_key(*, code_key_id: str, client=None):
    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_or_none(CodeKey.id == code_key_id)
    if key is None:
        raise ValueError("key not found")

    _mark(CodeKey, code_key_id, status="revoked", sync_status="pending")
    cl = _client(client)
    try:
        if key.litellm_key_id:
            cl.block_key(key.litellm_key_id)
        _mark(CodeKey, code_key_id, sync_status="synced", sync_error=None)
    except LiteLLMError as e:
        _mark(CodeKey, code_key_id, sync_error=str(e)[:1000])

    with DB.connection_context():
        return CodeKey.get_by_id(code_key_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_provisioning.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add management/server/services/code_provisioning.py test/multitenant/test_code_provisioning.py
git commit -m "feat(code): service provisioning — invariant allocation + desired-state-first"
```

---

### Task 4: Réconciliateur (`code_reconcile.py`)

**Files:**
- Create: `management/server/services/code_reconcile.py`
- Test: `test/multitenant/test_code_reconcile.py`

**Interfaces:**
- Consumes: modèles Task 1, client Task 2, `_mark`/aliases Task 3
- Produces: `reconcile_all(client=None) -> dict` — retourne `{"keys_synced": n, "teams_synced": n, "errors": n}`. Ordre : **keys à bloquer/révoquer d'abord** (sécuritaire), puis teams pending, puis keys `pending_create` → marquées `error` (le plaintext est irrécupérable — recréation manuelle).

- [ ] **Step 1: Write the failing test**

```python
# test/multitenant/test_code_reconcile.py
"""The reconciler converges pending rows once LiteLLM is back up."""
import pytest
from test.multitenant.test_code_provisioning import FakeLiteLLM, org_with_entitlement  # noqa: F401

pytestmark = pytest.mark.p1


def test_reconciler_repairs_team_and_revocation_after_downtime(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import reconcile_all

    down = FakeLiteLLM(down=True)
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=down)
    assert team.sync_status == "pending"

    up = FakeLiteLLM()  # LiteLLM back up
    report = reconcile_all(client=up)
    assert report["teams_synced"] == 1

    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_by_id(team.id)
    assert team.sync_status == "synced"
    assert team.litellm_team_id is not None

    # key created fine, then revocation fails mid-flight -> reconciler must block it
    key, _ = cp.create_code_key(code_team_id=team.id, label="d", owner_user_id=None,
                                created_by="tester", client=up)
    up.down = True
    revoked = cp.revoke_code_key(code_key_id=key.id, client=up)
    assert revoked.sync_status == "pending"
    up.down = False
    report = reconcile_all(client=up)
    assert report["keys_synced"] == 1
    assert key.litellm_key_id in up.blocked


def test_reconciler_marks_unfinishable_pending_create_keys_as_error(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_reconcile import reconcile_all

    up = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=up)
    up.down = True
    key, plain = cp.create_code_key(code_team_id=team.id, label="d", owner_user_id=None,
                                    created_by="tester", client=up)
    assert plain is None and key.sync_status == "pending"
    up.down = False
    reconcile_all(client=up)

    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_by_id(key.id)
    assert key.sync_status == "error"  # plaintext unrecoverable -> manual recreate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: code_reconcile`

- [ ] **Step 3: Implement**

```python
# management/server/services/code_reconcile.py
"""Reconciler — converges panel desired state into LiteLLM (spec §5).

Order matters: revocations/blocks FIRST (security), then team create/update,
then unfinishable pending_create keys are flagged 'error' (their plaintext
only ever existed in the lost /key/generate response — admin must recreate).
Run via POST /api/admin/code/reconcile (Task 5) — cron it in K8s later.
"""
import logging

from management.server.services.code_provisioning import _client, _mark, get_entitlement
from management.server.services.litellm_client import LiteLLMError

logger = logging.getLogger(__name__)


def reconcile_all(client=None) -> dict:
    from api.db.db_models import DB, CodeTeam, CodeKey
    cl = _client(client)
    report = {"keys_synced": 0, "teams_synced": 0, "errors": 0}

    # --- 1. security first: pending blocks/revocations/unblocks on existing keys ---
    with DB.connection_context():
        pending_keys = list(CodeKey.select().where(
            (CodeKey.sync_status == "pending") & (CodeKey.litellm_key_id.is_null(False))))
    for key in pending_keys:
        try:
            if key.status in ("revoked", "blocked"):
                cl.block_key(key.litellm_key_id)
            else:
                cl.unblock_key(key.litellm_key_id)
            _mark(CodeKey, key.id, sync_status="synced", sync_error=None)
            report["keys_synced"] += 1
        except LiteLLMError as e:
            _mark(CodeKey, key.id, sync_error=str(e)[:1000])
            report["errors"] += 1

    # --- 2. teams: create-if-missing (idempotent via alias) or push budget ---
    with DB.connection_context():
        pending_teams = list(CodeTeam.select().where(
            (CodeTeam.sync_status == "pending") & (CodeTeam.status == "active")))
    for team in pending_teams:
        ent = get_entitlement(team.org_id)
        if ent is None:
            continue
        alias = cl.team_alias(team.org_id, team.id)
        try:
            llm_id = team.litellm_team_id or cl.find_team_by_alias(alias)
            if llm_id is None:
                llm_id = cl.create_team(alias=alias, max_budget=team.max_budget,
                                        budget_duration=ent.budget_period,
                                        models=team.model_access or [])
            else:
                cl.update_team(team_id=llm_id, max_budget=team.max_budget)
            _mark(CodeTeam, team.id, litellm_team_id=llm_id,
                  sync_status="synced", sync_error=None)
            report["teams_synced"] += 1
        except LiteLLMError as e:
            _mark(CodeTeam, team.id, sync_error=str(e)[:1000])
            report["errors"] += 1

    # --- 3. unfinishable creates: key row exists but generate response was lost ---
    with DB.connection_context():
        orphan_creates = list(CodeKey.select().where(
            (CodeKey.sync_status == "pending") & (CodeKey.litellm_key_id.is_null(True)) &
            (CodeKey.status == "active")))
    for key in orphan_creates:
        _mark(CodeKey, key.id, sync_status="error",
              sync_error="generate lost mid-flight; plaintext unrecoverable — recreate the key")
        logger.warning("code_key %s flagged for manual recreate", key.id)

    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_reconcile.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add management/server/services/code_reconcile.py test/multitenant/test_code_reconcile.py
git commit -m "feat(code): réconciliateur — révocations prioritaires, creates idempotents"
```

---

### Task 5: Schemas + auth dep + router `code.py` + audit + enregistrement

**Files:**
- Modify: `management/server/models/schemas.py` (append)
- Modify: `management/server/auth/dependencies.py` (append `require_code_team_admin`)
- Create: `management/server/routers/code.py`
- Modify: `management/server/main.py` (1 ligne `include_router`)
- Modify: `management/server/services/audit.py` (4 constantes d'action)
- Test: `test/multitenant/test_code_routes_rbac.py`

**Interfaces:**
- Consumes: services Tasks 3-4, deps auth existantes (`get_current_user_id`, `require_superuser`, `require_org_admin`)
- Produces — routes sous `/api/admin` :
  - `PUT /orgs/{org_id}/code/entitlement` (superuser) — body `CodeEntitlementUpsert{status, org_code_budget, budget_period}`
  - `GET /orgs/{org_id}/code/overview` (tout membre code de l'org, org admin, superuser) — `{entitlement, allocated, teams: [...]}`
  - `POST /orgs/{org_id}/code/teams` (org admin) — body `CodeTeamCreate{name, max_budget, model_access}`
  - `PUT /code/teams/{team_id}` (org admin) — body `CodeTeamUpdate{max_budget}`
  - `POST /code/teams/{team_id}/admins` (org admin) — body `{email}` (résolu via `UserService.query(email=...)`)
  - `POST /code/teams/{team_id}/keys` (org admin **ou** code-team admin) — body `CodeKeyCreate{label, owner_user_id?}` → `{key: {...}, plain_key: str|null}` (plain une seule fois)
  - `POST /code/keys/{key_id}/revoke` (org admin ou code-team admin de la team)
  - `POST /code/reconcile` (superuser) → report du réconciliateur
- Nouvelle dep : `require_code_team_admin(team_id, user_id)` — superuser OU org_admin de l'org de la team OU `CodeTeamMember(role=admin)`.

- [ ] **Step 1: Write the failing RBAC tests**

```python
# test/multitenant/test_code_routes_rbac.py
"""Route-level RBAC for the Code section — FastAPI TestClient, LiteLLM faked."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.p1


@pytest.fixture()
def panel_client(monkeypatch, org_with_entitlement_and_users):
    """TestClient over management.server.main:app with LiteLLM faked at module level."""
    from test.multitenant.test_code_provisioning import FakeLiteLLM
    from management.server.services import code_provisioning
    fake = FakeLiteLLM()
    monkeypatch.setattr(code_provisioning, "_client", lambda client=None: client or fake)
    from management.server.main import app
    return TestClient(app), fake


# org_with_entitlement_and_users: fixture to add in test/multitenant/conftest.py —
# seeds an org + entitlement (as in test_code_provisioning.org_with_entitlement)
# PLUS three users with JWT tokens minted via management.server.auth.jwt.create_token:
#   superuser (is_superuser=1), org_admin (OrgMember role=org_admin),
#   plain_member (OrgMember role=member).
# Yields (org_id, tokens: dict[str, str]).


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_entitlement_upsert_superuser_only(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    body = {"status": "active", "org_code_budget": 100.0, "budget_period": "1mo"}
    assert client.put(f"/api/admin/orgs/{org_id}/code/entitlement",
                      json=body, headers=_h(tokens["org_admin"])).status_code == 403
    assert client.put(f"/api/admin/orgs/{org_id}/code/entitlement",
                      json=body, headers=_h(tokens["superuser"])).status_code == 200


def test_team_create_org_admin_only(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    body = {"name": "squad", "max_budget": 10.0, "model_access": []}
    assert client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json=body, headers=_h(tokens["plain_member"])).status_code == 403
    r = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                    json=body, headers=_h(tokens["org_admin"]))
    assert r.status_code == 201


def test_key_create_delegated_to_team_admin(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()

    # plain member: 403 before delegation
    assert client.post(f"/api/admin/code/teams/{team['id']}/keys",
                       json={"label": "dev"}, headers=_h(tokens["plain_member"])).status_code == 403

    # delegate, then member can create a key and sees the plaintext ONCE
    client.post(f"/api/admin/code/teams/{team['id']}/admins",
                json={"email": tokens["plain_member_email"]}, headers=_h(tokens["org_admin"]))
    r = client.post(f"/api/admin/code/teams/{team['id']}/keys",
                    json={"label": "dev"}, headers=_h(tokens["plain_member"]))
    assert r.status_code == 201
    assert r.json()["plain_key"].startswith("sk-")

    # overview endpoint never leaks plaintext
    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview",
                    headers=_h(tokens["org_admin"])).json()
    assert "plain_key" not in str(ov)


def test_allocation_violation_returns_422(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    client.post(f"/api/admin/orgs/{org_id}/code/teams",
                json={"name": "a", "max_budget": 80.0, "model_access": []},
                headers=_h(tokens["org_admin"]))
    r = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                    json={"name": "b", "max_budget": 30.0, "model_access": []},
                    headers=_h(tokens["org_admin"]))
    assert r.status_code == 422
    assert "allocation" in r.json()["detail"]
```

- [ ] **Step 2: Add the conftest fixture**

Dans `test/multitenant/conftest.py`, ajouter `org_with_entitlement_and_users` : reprend le seed de `org_with_entitlement` (Task 3) + crée 3 `User` rows (`is_superuser` pour le premier, `OrgMember role=org_admin` pour le deuxième, `role=member` pour le troisième) et mint leurs JWT via `management.server.auth.jwt.create_token(user_id, "access")` (même helper que le login). Yield `(org_id, {"superuser": ..., "org_admin": ..., "plain_member": ..., "plain_member_email": ...})`, cleanup en fin.

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_routes_rbac.py -v`
Expected: FAIL — 404 sur les routes (router absent)

- [ ] **Step 4: Implement schemas, dep, router, registration, audit constants**

`management/server/models/schemas.py` (append) :

```python
# ---- Code product ----
class CodeEntitlementUpsert(BaseModel):
    status: Literal["active", "suspended"]
    org_code_budget: float = Field(ge=0)
    budget_period: str = "1mo"

class CodeTeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    max_budget: float = Field(gt=0)
    model_access: list[str] = []

class CodeTeamUpdate(BaseModel):
    max_budget: float = Field(gt=0)

class CodeTeamAdminAdd(BaseModel):
    email: EmailStr

class CodeKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    owner_user_id: str | None = None
```

`management/server/auth/dependencies.py` (append) :

```python
def require_code_team_admin(team_id: str, user_id: str = Depends(get_current_user_id)):
    """Superuser, org_admin of the team's org, or delegated CodeTeamMember admin."""
    user = _load_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.is_superuser:
        return user

    from api.db.db_models import DB, CodeTeam, CodeTeamMember
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None or team.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Code team not found")

    from api.db.services.org_service import OrgMemberService
    membership = OrgMemberService.get_membership(team.org_id, user_id)
    if membership and membership.role == "org_admin":
        return user

    with DB.connection_context():
        delegated = CodeTeamMember.get_or_none(
            (CodeTeamMember.code_team_id == team_id) &
            (CodeTeamMember.user_id == user_id) & (CodeTeamMember.role == "admin"))
    if not delegated:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Code team admin access required")
    return user
```

`management/server/services/audit.py` (append aux constantes existantes) :

```python
CODE_ENTITLEMENT_SET = "code.entitlement.set"
CODE_TEAM_CREATE = "code.team.create"
CODE_KEY_CREATE = "code.key.create"
CODE_KEY_REVOKE = "code.key.revoke"
```

`management/server/routers/code.py` :

```python
"""Code product routes — entitlements (superuser), teams (org admin), keys (delegated)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status

from management.server.auth.dependencies import (
    get_current_user_id, require_superuser, require_org_admin, require_code_team_admin,
)
from management.server.models.schemas import (
    CodeEntitlementUpsert, CodeTeamCreate, CodeTeamUpdate, CodeTeamAdminAdd, CodeKeyCreate,
)
from management.server.services import audit as audit_svc

router = APIRouter()


def _team_to_dict(t) -> dict:
    return {"id": t.id, "org_id": t.org_id, "name": t.name, "max_budget": t.max_budget,
            "model_access": t.model_access or [], "status": t.status,
            "sync_status": t.sync_status, "litellm_team_id": t.litellm_team_id}


def _key_to_dict(k) -> dict:
    return {"id": k.id, "code_team_id": k.code_team_id, "label": k.label,
            "key_masked": k.key_masked, "owner_user_id": k.owner_user_id,
            "status": k.status, "sync_status": k.sync_status}


@router.put("/orgs/{org_id}/code/entitlement")
def set_entitlement(request: Request, org_id: str, body: CodeEntitlementUpsert,
                    user=Depends(require_superuser)):
    from management.server.services import code_provisioning as cp
    try:
        ent = cp.upsert_entitlement(org_id=org_id, status=body.status,
                                    org_code_budget=body.org_code_budget,
                                    budget_period=body.budget_period, actor_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id,
                     action=audit_svc.CODE_ENTITLEMENT_SET, org_id=org_id,
                     resource_type="code_entitlement", resource_id=ent.id,
                     details={"status": body.status, "org_code_budget": body.org_code_budget})
    return {"org_id": org_id, "status": ent.status,
            "org_code_budget": ent.org_code_budget, "budget_period": ent.budget_period}


@router.get("/orgs/{org_id}/code/overview")
def code_overview(org_id: str, user_id: str = Depends(get_current_user_id)):
    # visible to any org member (code:view); org admin check covers superuser
    from api.db.services.org_service import OrgMemberService
    from api.db.services.user_service import UserService
    ok, user = UserService.get_by_id(user_id)
    if not (ok and user) or (not user.is_superuser
                             and not OrgMemberService.get_membership(org_id, user_id)):
        raise HTTPException(status_code=403, detail="Org membership required")

    from api.db.db_models import DB, CodeTeam, CodeKey
    from management.server.services import code_provisioning as cp
    ent = cp.get_entitlement(org_id)
    with DB.connection_context():
        teams = list(CodeTeam.select().where(
            (CodeTeam.org_id == org_id) & (CodeTeam.status == "active")))
        keys_by_team = {}
        for t in teams:
            keys_by_team[t.id] = [_key_to_dict(k) for k in
                                  CodeKey.select().where(CodeKey.code_team_id == t.id)]
    return {
        "entitlement": None if ent is None else {
            "status": ent.status, "org_code_budget": ent.org_code_budget,
            "budget_period": ent.budget_period},
        "allocated": cp.allocated_budget(org_id),
        "teams": [{**_team_to_dict(t), "keys": keys_by_team[t.id]} for t in teams],
    }


@router.post("/orgs/{org_id}/code/teams", status_code=status.HTTP_201_CREATED)
def create_team(request: Request, org_id: str, body: CodeTeamCreate,
                user_id: str = Depends(get_current_user_id)):
    user = require_org_admin(org_id, user_id)
    from management.server.services import code_provisioning as cp
    try:
        team = cp.create_code_team(org_id=org_id, name=body.name, max_budget=body.max_budget,
                                   model_access=body.model_access, created_by=user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_TEAM_CREATE,
                     org_id=org_id, resource_type="code_team", resource_id=team.id,
                     details={"name": body.name, "max_budget": body.max_budget})
    return _team_to_dict(team)


@router.put("/code/teams/{team_id}")
def update_team(team_id: str, body: CodeTeamUpdate,
                user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeTeam
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    require_org_admin(team.org_id, user_id)
    from management.server.services import code_provisioning as cp
    try:
        team = cp.update_code_team_budget(code_team_id=team_id, new_budget=body.max_budget)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _team_to_dict(team)


@router.post("/code/teams/{team_id}/admins", status_code=status.HTTP_201_CREATED)
def add_team_admin(team_id: str, body: CodeTeamAdminAdd,
                   user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeTeam, CodeTeamMember
    from api.db.services.user_service import UserService
    from api.db.services.org_service import OrgMemberService
    from common.misc_utils import get_uuid
    with DB.connection_context():
        team = CodeTeam.get_or_none(CodeTeam.id == team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Code team not found")
    require_org_admin(team.org_id, user_id)

    users = UserService.query(email=body.email, status="1")
    if not users:
        raise HTTPException(status_code=404, detail=f"No active user with email {body.email}")
    target = users[0]
    if not OrgMemberService.get_membership(team.org_id, target.id):
        raise HTTPException(status_code=422, detail="User is not a member of this org")
    with DB.connection_context():
        existing = CodeTeamMember.get_or_none(
            (CodeTeamMember.code_team_id == team_id) & (CodeTeamMember.user_id == target.id))
        if existing is None:
            CodeTeamMember.create(id=get_uuid(), code_team_id=team_id,
                                  user_id=target.id, role="admin")
    return {"code_team_id": team_id, "user_id": target.id, "role": "admin"}


@router.post("/code/teams/{team_id}/keys", status_code=status.HTTP_201_CREATED)
def create_key(request: Request, team_id: str, body: CodeKeyCreate,
               user=Depends(require_code_team_admin)):
    from management.server.services import code_provisioning as cp
    try:
        key, plain = cp.create_code_key(code_team_id=team_id, label=body.label,
                                        owner_user_id=body.owner_user_id, created_by=user.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_CREATE,
                     org_id=None, resource_type="code_key", resource_id=key.id,
                     details={"label": body.label, "team_id": team_id})
    # plain_key is returned EXACTLY once, never persisted, never logged
    return {"key": _key_to_dict(key), "plain_key": plain}


@router.post("/code/keys/{key_id}/revoke")
def revoke_key(request: Request, key_id: str, user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import DB, CodeKey
    with DB.connection_context():
        key = CodeKey.get_or_none(CodeKey.id == key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="Key not found")
    user = require_code_team_admin(key.code_team_id, user_id)
    from management.server.services import code_provisioning as cp
    key = cp.revoke_code_key(code_key_id=key_id)
    audit_svc.record(request=request, actor_user_id=user.id, action=audit_svc.CODE_KEY_REVOKE,
                     org_id=None, resource_type="code_key", resource_id=key_id,
                     details={"label": key.label})
    return _key_to_dict(key)


@router.post("/code/reconcile")
def reconcile(user=Depends(require_superuser)):
    from management.server.services.code_reconcile import reconcile_all
    return reconcile_all()
```

`management/server/main.py` — après la ligne `models.router` :

```python
app.include_router(code.router, prefix="/api/admin", tags=["Code Product"])
```

(et ajouter `code` à l'import des routers en tête de fichier.)

**Note dep FastAPI** : `require_code_team_admin` utilisé via `Depends` reçoit `team_id` du path automatiquement (même mécanique que `require_ws_admin` — paramètre de path homonyme).

- [ ] **Step 5: Run tests, verify RBAC suite passes, commit**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_routes_rbac.py test/multitenant/test_code_provisioning.py -v`
Expected: ALL PASS

```bash
git add management/server/models/schemas.py management/server/auth/dependencies.py \
        management/server/routers/code.py management/server/main.py \
        management/server/services/audit.py test/multitenant/test_code_routes_rbac.py \
        test/multitenant/conftest.py
git commit -m "feat(code): routes /api/admin code — entitlement, teams, keys, RBAC délégué, audit"
```

---

### Task 6: Front — section « Code » du panel

**Files:**
- Create: `management/web/src/pages/code/index.tsx` (overview org + teams + keys)
- Modify: `management/web/src/pages/organisations/detail.tsx` (card entitlement, superuser)
- Modify: `management/web/src/App.tsx` (route `/code`)
- Modify: `management/web/src/components/Layout.tsx` (item de nav « Code »)

**Interfaces:**
- Consumes: routes Task 5 via `api` (`@/lib/api`, axios base `/api/admin`)
- Produces: pages accessibles `/admin/code?org=<org_id>` ; card entitlement dans le détail d'org.

- [ ] **Step 1: Page principale `pages/code/index.tsx`**

Suivre le pattern `groups/index.tsx` (antd Table/Modal/Form, fetch avec `api`). Contenu complet :

```tsx
import { useEffect, useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Table, Button, Card, Modal, Form, Input, InputNumber, App, Progress, Tag, Popconfirm, Space, Typography, Alert } from 'antd';
import { PlusOutlined, StopOutlined, CopyOutlined } from '@ant-design/icons';
import api from '@/lib/api';

interface CodeKey { id: string; label: string; key_masked: string | null; status: string; sync_status: string; }
interface CodeTeam { id: string; name: string; max_budget: number; status: string; sync_status: string; keys: CodeKey[]; }
interface Overview {
  entitlement: { status: string; org_code_budget: number; budget_period: string } | null;
  allocated: number;
  teams: CodeTeam[];
}

export default function CodePage() {
  const [searchParams] = useSearchParams();
  const orgId = searchParams.get('org') || '';
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [teamModal, setTeamModal] = useState(false);
  const [keyModalTeam, setKeyModalTeam] = useState<CodeTeam | null>(null);
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const [teamForm] = Form.useForm();
  const [keyForm] = Form.useForm();
  const { message } = App.useApp();

  const fetchOverview = useCallback(() => {
    if (!orgId) { setLoading(false); return; }
    setLoading(true);
    api.get(`/orgs/${orgId}/code/overview`)
      .then((res) => setOverview(res.data))
      .finally(() => setLoading(false));
  }, [orgId]);

  useEffect(fetchOverview, [fetchOverview]);

  const onCreateTeam = async () => {
    try {
      const values = await teamForm.validateFields();
      await api.post(`/orgs/${orgId}/code/teams`, { ...values, model_access: [] });
      message.success('Code team créée');
      setTeamModal(false);
      teamForm.resetFields();
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onCreateKey = async () => {
    if (!keyModalTeam) return;
    try {
      const values = await keyForm.validateFields();
      const res = await api.post(`/code/teams/${keyModalTeam.id}/keys`, values);
      if (res.data.plain_key) {
        setFreshKey(res.data.plain_key);
      } else {
        message.warning('Gateway injoignable — la clé est en attente, réessayez.');
        setKeyModalTeam(null);
      }
      keyForm.resetFields();
      fetchOverview();
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      if (msg) message.error(msg);
    }
  };

  const onRevoke = async (keyId: string) => {
    await api.post(`/code/keys/${keyId}/revoke`);
    message.success('Clé révoquée');
    fetchOverview();
  };

  if (!orgId) return <Card><p className="text-gray-500">Sélectionne une organisation : <code>?org=ORG_ID</code></p></Card>;
  if (!loading && overview && !overview.entitlement)
    return <Card><Alert type="info" message="Le produit Code n'est pas activé pour cette organisation." /></Card>;

  const ent = overview?.entitlement;
  const allocated = overview?.allocated ?? 0;
  const total = ent?.org_code_budget ?? 0;

  const keyColumns = (team: CodeTeam) => [
    { title: 'Label', dataIndex: 'label' },
    { title: 'Clé', dataIndex: 'key_masked', render: (v: string | null) => <code>{v || '—'}</code> },
    { title: 'Statut', dataIndex: 'status', render: (s: string) => <Tag color={s === 'active' ? 'green' : 'red'}>{s}</Tag> },
    { title: 'Sync', dataIndex: 'sync_status', render: (s: string) => <Tag color={s === 'synced' ? 'blue' : 'orange'}>{s}</Tag> },
    {
      title: '', width: 60,
      render: (_: unknown, k: CodeKey) => k.status === 'active' && (
        <Popconfirm title="Révoquer cette clé ?" onConfirm={() => onRevoke(k.id)}>
          <Button type="text" danger icon={<StopOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-semibold">Code — accès gateway</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setTeamModal(true)}>
          Nouvelle code-team
        </Button>
      </div>

      {ent?.status === 'suspended' && (
        <Alert className="mb-4" type="warning" message="Produit Code suspendu — toutes les clés sont bloquées." />
      )}

      <Card className="mb-4" title="Budget de l'organisation">
        <Progress percent={total ? Math.round((allocated / total) * 100) : 0}
                  format={() => `${allocated} € alloués / ${total} € (${ent?.budget_period})`} />
      </Card>

      {(overview?.teams ?? []).map((team) => (
        <Card key={team.id} className="mb-4"
              title={<Space>{team.name}<Tag>{team.max_budget} €</Tag>
                     {team.sync_status !== 'synced' && <Tag color="orange">{team.sync_status}</Tag>}</Space>}
              extra={<Button size="small" icon={<PlusOutlined />}
                             onClick={() => setKeyModalTeam(team)}>Nouvelle clé</Button>}>
          <Table rowKey="id" size="small" pagination={false}
                 columns={keyColumns(team)} dataSource={team.keys} />
        </Card>
      ))}

      <Modal title="Nouvelle code-team" open={teamModal} onOk={onCreateTeam}
             onCancel={() => setTeamModal(false)}>
        <Form form={teamForm} layout="vertical">
          <Form.Item name="name" label="Nom" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="max_budget" label={`Budget (€ / ${ent?.budget_period ?? '1mo'})`}
                     rules={[{ required: true }]}>
            <InputNumber min={1} max={Math.max(0, total - allocated)} className="w-full" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={`Nouvelle clé — ${keyModalTeam?.name ?? ''}`} open={!!keyModalTeam}
             onOk={freshKey ? () => { setFreshKey(null); setKeyModalTeam(null); } : onCreateKey}
             okText={freshKey ? 'Fermer' : 'Créer'}
             onCancel={() => { setFreshKey(null); setKeyModalTeam(null); }}>
        {freshKey ? (
          <Alert type="success" message="Clé créée — copiez-la MAINTENANT, elle ne sera plus jamais affichée."
                 description={
                   <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>
                     {freshKey}
                   </Typography.Paragraph>
                 } />
        ) : (
          <Form form={keyForm} layout="vertical">
            <Form.Item name="label" label="Label (dev / siège)" rules={[{ required: true }]}>
              <Input placeholder="dev-alice" />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: Card entitlement dans `organisations/detail.tsx` (superuser)**

Ajouter dans la page détail d'org (visible seulement si `useAuthStore` expose `is_superuser` — même garde que les actions superuser existantes de cette page) :

```tsx
{isSuperuser && (
  <Card title="Produit Code (entitlement)" className="mb-4">
    <Form layout="inline" onFinish={async (v) => {
      await api.put(`/orgs/${orgId}/code/entitlement`, {
        status: v.enabled ? 'active' : 'suspended',
        org_code_budget: v.org_code_budget,
        budget_period: '1mo',
      });
      message.success('Entitlement mis à jour');
    }}>
      <Form.Item name="enabled" label="Activé" valuePropName="checked" initialValue={false}>
        <Switch />
      </Form.Item>
      <Form.Item name="org_code_budget" label="Budget (€/mois)" initialValue={0}>
        <InputNumber min={0} />
      </Form.Item>
      <Button htmlType="submit" type="primary">Enregistrer</Button>
    </Form>
  </Card>
)}
```

(Pré-remplir via `GET /orgs/{orgId}/code/overview` au mount — même pattern fetch que le reste de la page ; imports `Switch`, `InputNumber` à ajouter.)

- [ ] **Step 3: Route + nav**

`App.tsx` — ajouter la route à côté des existantes : `<Route path="code" element={<CodePage />} />` (+ import).
`Layout.tsx` — item de menu « Code » pointant vers `/code` (même structure que l'item Workspaces).

- [ ] **Step 4: Build + vérification manuelle**

Run: `cd management/web && PATH=/opt/homebrew/bin:$PATH npm run build`
Expected: build OK, zéro erreur TS.
Puis vérif manuelle (stack dev + mgmt server lancés) : activer l'entitlement sur une org de test en superuser → créer une team → créer une clé → voir le plaintext une fois → révoquer.

- [ ] **Step 5: Commit**

```bash
git add management/web/src/pages/code/index.tsx management/web/src/pages/organisations/detail.tsx \
        management/web/src/App.tsx management/web/src/components/Layout.tsx
git commit -m "feat(code): section Code du panel — overview budget, teams, clés one-time"
```

---

### Task 7: Intégration & E2E — compose LiteLLM + Postgres, `mock_response`

**Files:**
- Create: `docker/litellm-test/docker-compose.yml`
- Create: `docker/litellm-test/litellm_config.yaml`
- Test: `test/multitenant/test_code_litellm_integration.py`

**Interfaces:**
- Consumes: `LiteLLMClient` (Task 2) pointé sur le compose local
- Produces: suite d'intégration gated par `LITELLM_TEST_URL` (skip sinon — comme les skips env-gated existants de `test/multitenant`)

- [ ] **Step 1: Compose + config**

```yaml
# docker/litellm-test/docker-compose.yml
# Local integration stack for the Code product — NO GPU, NO vLLM.
# The mock model answers via mock_response; cost-per-token is inflated so
# a single call exhausts a tiny budget (budget-enforcement test).
services:
  litellm-db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: litellm
      POSTGRES_PASSWORD: litellm-test
      POSTGRES_DB: litellm
    ports: ["5433:5432"]

  litellm:
    image: ghcr.io/berriai/litellm:main-v1.74.0-stable  # pin — bump consciously
    depends_on: [litellm-db]
    ports: ["4000:4000"]
    volumes:
      - ./litellm_config.yaml:/app/config.yaml
    environment:
      DATABASE_URL: postgresql://litellm:litellm-test@litellm-db:5432/litellm
      LITELLM_MASTER_KEY: sk-master-test-only
      LITELLM_TELEMETRY: "False"
    command: ["--config", "/app/config.yaml", "--port", "4000"]
```

```yaml
# docker/litellm-test/litellm_config.yaml
model_list:
  - model_name: code-mock
    litellm_params:
      model: openai/mock
      api_key: not-used
      mock_response: "Hello from the mock code model."
    model_info:
      input_cost_per_token: 0.5    # inflated: 1 call ≈ several EUR -> busts tiny budgets
      output_cost_per_token: 0.5

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
```

- [ ] **Step 2: Write the integration tests**

```python
# test/multitenant/test_code_litellm_integration.py
"""Integration against a REAL LiteLLM container (docker/litellm-test compose).

Start:  docker compose -f docker/litellm-test/docker-compose.yml up -d
Gate:   LITELLM_TEST_URL=http://localhost:4000 (skipped otherwise)
"""
import os
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
    return LiteLLMClient(base_url=BASE, master_key="sk-master-test-only")


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
    second = call()
    assert second.status_code == 400
    assert "budget" in second.text.lower()
```

- [ ] **Step 3: Bring the stack up and run**

Run:
```bash
docker compose -f docker/litellm-test/docker-compose.yml up -d
sleep 10  # litellm migrations
LITELLM_TEST_URL=http://localhost:4000 PYTHONPATH=. uv run python -m pytest \
  test/multitenant/test_code_litellm_integration.py -v
```
Expected: 3 PASS. **Si `generate_key` ne retourne pas de champ `token`/`token_id`** (variation de version LiteLLM) : ajuster `LiteLLMClient.generate_key` sur le champ réel observé — c'est exactement le rôle de ce test.

- [ ] **Step 4: Verify the suite skips cleanly without the stack**

Run: `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_litellm_integration.py -v` (sans env var)
Expected: 3 SKIPPED

- [ ] **Step 5: Commit**

```bash
git add docker/litellm-test/ test/multitenant/test_code_litellm_integration.py
git commit -m "test(code): intégration LiteLLM réelle — idempotence, lifecycle key, budget temps réel"
```

---

## Follow-ups (hors périmètre de ce plan — plans séparés)

1. **Déploiement K8s/Helm** : chart LiteLLM + Postgres (PVC, Secret `LITELLM_MASTER_KEY`, placement hors nœud GPU saturé), CronJob `POST /api/admin/code/reconcile`, exposition `/v1/*` via Envoy Gateway. À planifier après validation logicielle.
2. **Vue spend** : brancher `GET /team/info` (spend réel) dans l'overview — l'endpoint existe côté client (Task 2), l'UI affiche l'alloué en attendant.
3. **Ledger unifié RAG+code** (différé YAGNI au spec §7).

## Self-review (faite à l'écriture)

- **Spec coverage** : §2 topologie → Tasks 2+7 (+ follow-up 1 pour le déploiement) ; §3 tables+invariant+cycles → Tasks 1+3 ; §4 RBAC → Task 5 (+`code_team_member` addendum Task 1) ; §5 provisioning/desired-state/réconciliation → Tasks 3+4 ; §6 front → Task 6 ; §8 tests → chaque task + Task 7.
- **Placeholder scan** : zéro TBD ; le seul point ouvert assumé est le nom exact du champ token de `/key/generate`, résolu par le test d'intégration (Task 7 Step 3, consigne explicite).
- **Type consistency** : `litellm_team_id`/`litellm_key_id` cohérents Tasks 1→5 ; signatures service (Task 3) réutilisées telles quelles dans le router (Task 5) et les tests.
