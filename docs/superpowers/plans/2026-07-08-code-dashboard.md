# Code Dashboard + Per-Seat Spend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dashboard produit Code (KPIs, courbe 30 j, tops, alertes budget) + spend par clé/siège, avec capture d'historique par scheduler in-process.

**Architecture:** Une table de snapshots cumulés `(snap_date, code_team_id)` + une table d'historique de runs. Un service `code_housekeeping` (reconcile + snapshot) déclenché par une tâche asyncio horaire dans le lifespan FastAPI (gardée par `DB.lock`) et par `POST /code/housekeeping`. Le `GET /code/dashboard` est une lecture pure (live LiteLLM + snapshots). Front : section dashboard dans l'onglet Code (composant séparé), auto-refresh 30 s visibility-aware, card résumé dans le Dashboard global.

**Tech Stack:** Peewee (`on_conflict` MySQL), FastAPI lifespan + `asyncio.to_thread`, recharts (déjà utilisé par le Dashboard global), antd.

**Spec:** `docs/superpowers/specs/2026-07-08-code-dashboard-design.md`

## Global Constraints

- `spend` snapshot = **cumulé du cycle** tel que LiteLLM le rapporte ; courbe quotidienne = delta, **delta négatif (reset de cycle) → delta = valeur du jour**.
- Gateway down → **aucune écriture partielle** de snapshot ; les lectures live renvoient `null`, jamais 0.
- Le GET dashboard **n'écrit jamais** (lecture pure).
- Scheduler : **sleep d'abord, run ensuite** (pas de run au boot) + env guard `ADMIN_CODE_SCHEDULER=0` pour le désactiver (tests/TestClient) + **log de démarrage explicite** (leçon asgi.py 2026-06-30).
- Multi-replica safe : `housekeeping()` décoré `@DB.lock("code_housekeeping", 10)` (pattern `init_database_tables`, api/db/db_models.py:700).
- Nouvelles tables dans le bloc `CUSTOM B2B SaaS — Code product tables` existant de db_models.py.
- Env tests : `ADMIN_JWT_SECRET` depuis `/Users/zappy/ragflow/.env.local` (chemin absolu), dev stack up.
- Commits : conventionnels en français, **jamais** de Co-Authored-By.

---

### Task 1: Tables snapshots/runs + service `code_housekeeping`

**Files:**
- Modify: `api/db/db_models.py` (2 classes dans le bloc Code product, avant le footer du marqueur ; + 1 entrée unique-index)
- Create: `management/server/services/code_housekeeping.py`
- Test: `test/multitenant/test_code_housekeeping.py`

**Interfaces:**
- Consumes: `code_provisioning.spend_by_litellm_team(client=None)`, `code_reconcile.reconcile_all(client=None)`, modèles `CodeTeam`.
- Produces (pour Tasks 2-3):
  - Modèles `CodeSpendSnapshot(snap_date: date, org_id, code_team_id, spend: float, max_budget: float)` (unique `(snap_date, code_team_id)`), `CodeHousekeepingRun(ran_at: datetime, teams_snapshotted: int, keys_synced: int, teams_synced: int, errors: int)`
  - `snapshot_spend(client=None) -> int` (nb teams snapshotées ; 0 + warning si gateway down)
  - `housekeeping(client=None) -> dict` = `{"teams_snapshotted", "keys_synced", "teams_synced", "errors", "ran_at"}` — décoré `@DB.lock("code_housekeeping", 10)`
  - `daily_spend_series(org_ids: list[str] | None, days: int = 30) -> list[dict]` → `[{"date": "YYYY-MM-DD", "spend": float}]` (None = toutes orgs)
  - `last_run() -> CodeHousekeepingRun | None`

- [ ] **Step 1: Write the failing tests**

```python
# test/multitenant/test_code_housekeeping.py
"""Snapshots idempotents, courbe avec reset de cycle, housekeeping combiné."""
import datetime
import pytest
from test.multitenant.test_code_provisioning import FakeLiteLLM, org_with_entitlement  # noqa: F401

pytestmark = pytest.mark.p1


def _cleanup_snapshots(org_id):
    from api.db.db_models import DB, CodeSpendSnapshot, CodeHousekeepingRun
    with DB.connection_context():
        CodeSpendSnapshot.delete().where(CodeSpendSnapshot.org_id == org_id).execute()
        CodeHousekeepingRun.delete().execute()


def test_snapshot_is_idempotent_per_day(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 10.0
    assert snapshot_spend(client=fake) == 1
    fake.teams[team.litellm_team_id]["spend"] = 14.0
    assert snapshot_spend(client=fake) == 1  # même jour → upsert, pas de doublon

    with DB.connection_context():
        rows = list(CodeSpendSnapshot.select().where(CodeSpendSnapshot.code_team_id == team.id))
    assert len(rows) == 1
    assert rows[0].spend == 14.0  # dernière valeur gagne
    _cleanup_snapshots(org_with_entitlement)


def test_snapshot_gateway_down_writes_nothing(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                        model_access=[], created_by="tester", client=fake)
    fake.down = True
    assert snapshot_spend(client=fake) == 0
    with DB.connection_context():
        assert CodeSpendSnapshot.select().where(
            CodeSpendSnapshot.org_id == org_with_entitlement).count() == 0


def test_daily_series_handles_cycle_reset(org_with_entitlement):  # noqa: F811
    """J1: 10 → J2: 30 (delta 20) → J3: 5 (reset → delta 5)."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import daily_spend_series
    from api.db.db_models import DB, CodeSpendSnapshot
    from common.misc_utils import get_uuid

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    today = datetime.date.today()
    with DB.connection_context():
        for offset, spend in ((2, 10.0), (1, 30.0), (0, 5.0)):
            CodeSpendSnapshot.create(id=get_uuid(), snap_date=today - datetime.timedelta(days=offset),
                                     org_id=org_with_entitlement, code_team_id=team.id,
                                     spend=spend, max_budget=50.0)
    series = {p["date"]: p["spend"] for p in daily_spend_series([org_with_entitlement], days=5)}
    assert series[str(today - datetime.timedelta(days=2))] == 10.0  # 1er point = sa valeur
    assert series[str(today - datetime.timedelta(days=1))] == 20.0  # delta
    assert series[str(today)] == 5.0                                # reset → valeur du jour
    _cleanup_snapshots(org_with_entitlement)


def test_housekeeping_combines_and_records_run(org_with_entitlement):  # noqa: F811
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import housekeeping, last_run

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=org_with_entitlement, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 3.0
    report = housekeeping(client=fake)
    assert report["teams_snapshotted"] == 1
    run = last_run()
    assert run is not None and run.teams_snapshotted == 1
    _cleanup_snapshots(org_with_entitlement)
```

- [ ] **Step 2: Run to verify FAIL**

```bash
export ADMIN_JWT_SECRET=$(grep "^ADMIN_JWT_SECRET=" /Users/zappy/ragflow/.env.local | cut -d= -f2-)
PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_housekeeping.py -v
```
Expected: FAIL — `ImportError: CodeSpendSnapshot` / `ModuleNotFoundError: code_housekeeping`

- [ ] **Step 3: Add the models** (api/db/db_models.py, DANS le bloc `CUSTOM B2B SaaS — Code product tables`, après `CodeKey`)

```python
class CodeSpendSnapshot(DataBaseModel):
    """Daily cumulative-spend snapshot per code team (source of the dashboard curve).

    spend is the CYCLE-CUMULATIVE value LiteLLM reports at snapshot time; the
    daily curve is the delta between consecutive snapshots (negative delta =
    cycle reset -> that day's delta is the day's raw value).
    """
    id = CharField(max_length=32, primary_key=True)
    snap_date = DateField(null=False, index=True)
    org_id = CharField(max_length=32, null=False, index=True)
    code_team_id = CharField(max_length=32, null=False, index=True)
    spend = FloatField(null=False, default=0.0)
    max_budget = FloatField(null=False, default=0.0)

    class Meta:
        db_table = "code_spend_snapshot"


class CodeHousekeepingRun(DataBaseModel):
    """One row per housekeeping pass (reconcile + snapshot) — scheduler observability."""
    id = CharField(max_length=32, primary_key=True)
    ran_at = DateTimeField(null=False, index=True)
    teams_snapshotted = IntegerField(null=False, default=0)
    teams_synced = IntegerField(null=False, default=0)
    keys_synced = IntegerField(null=False, default=0)
    errors = IntegerField(null=False, default=0)

    class Meta:
        db_table = "code_housekeeping_run"
```

`DateField`/`DateTimeField`/`IntegerField` sont déjà importés/utilisés dans le module. Puis ajouter dans `_add_rbac_unique_indexes` (même liste que `code_team_member`) : `("code_spend_snapshot", ("snap_date", "code_team_id"), True)`.

- [ ] **Step 4: Create the service**

```python
# management/server/services/code_housekeeping.py
"""Housekeeping du produit Code : reconcile + snapshot de spend, + courbe quotidienne.

Appelé par le scheduler in-process (management/server/main.py) et par
POST /api/admin/code/housekeeping. Protégé par DB.lock -> multi-replica safe.
"""
import datetime
import logging

from common.misc_utils import get_uuid

logger = logging.getLogger(__name__)


def snapshot_spend(client=None) -> int:
    """Upsert le snapshot du jour pour chaque team active. 0 si gateway down."""
    from api.db.db_models import DB, CodeTeam, CodeSpendSnapshot
    from management.server.services.code_provisioning import spend_by_litellm_team

    spend_map = spend_by_litellm_team(client=client)
    if spend_map is None:
        logger.warning("snapshot_spend: gateway injoignable, aucun snapshot écrit")
        return 0

    today = datetime.date.today()
    count = 0
    with DB.connection_context():
        teams = list(CodeTeam.select().where(
            (CodeTeam.status == "active") & (CodeTeam.litellm_team_id.is_null(False))))
        for t in teams:
            spend = spend_map.get(t.litellm_team_id, 0.0)
            (CodeSpendSnapshot.insert(
                id=get_uuid(), snap_date=today, org_id=t.org_id,
                code_team_id=t.id, spend=spend, max_budget=t.max_budget)
             .on_conflict(update={CodeSpendSnapshot.spend: spend,
                                  CodeSpendSnapshot.max_budget: t.max_budget})
             .execute())
            count += 1
    return count


def _housekeeping_impl(client=None) -> dict:
    from api.db.db_models import DB, CodeHousekeepingRun
    from management.server.services.code_reconcile import reconcile_all

    rec = reconcile_all(client=client)
    snapped = snapshot_spend(client=client)
    ran_at = datetime.datetime.now()
    with DB.connection_context():
        CodeHousekeepingRun.create(id=get_uuid(), ran_at=ran_at,
                                   teams_snapshotted=snapped,
                                   teams_synced=rec["teams_synced"],
                                   keys_synced=rec["keys_synced"], errors=rec["errors"])
    return {"teams_snapshotted": snapped, "ran_at": ran_at.isoformat(), **rec}


def housekeeping(client=None) -> dict:
    """Reconcile + snapshot, sérialisé cross-replicas par un lock DB."""
    from api.db.db_models import DB

    @DB.lock("code_housekeeping", 10)
    def _locked():
        return _housekeeping_impl(client=client)
    return _locked()


def last_run():
    from api.db.db_models import DB, CodeHousekeepingRun
    with DB.connection_context():
        return (CodeHousekeepingRun.select()
                .order_by(CodeHousekeepingRun.ran_at.desc()).first())


def daily_spend_series(org_ids: list[str] | None, days: int = 30) -> list[dict]:
    """Courbe spend/jour agrégée sur les orgs visibles. Delta négatif = reset -> valeur du jour."""
    from api.db.db_models import DB, CodeSpendSnapshot

    since = datetime.date.today() - datetime.timedelta(days=days)
    with DB.connection_context():
        q = CodeSpendSnapshot.select().where(CodeSpendSnapshot.snap_date >= since)
        if org_ids is not None:
            q = q.where(CodeSpendSnapshot.org_id.in_(org_ids))
        rows = list(q.order_by(CodeSpendSnapshot.code_team_id, CodeSpendSnapshot.snap_date))

    daily: dict[str, float] = {}
    prev_by_team: dict[str, float] = {}
    for r in rows:
        prev = prev_by_team.get(r.code_team_id)
        delta = r.spend if prev is None or r.spend < prev else r.spend - prev
        prev_by_team[r.code_team_id] = r.spend
        key = str(r.snap_date)
        daily[key] = daily.get(key, 0.0) + delta
    return [{"date": d, "spend": round(daily[d], 4)} for d in sorted(daily)]
```

- [ ] **Step 5: Create tables, run tests, ruff, commit**

```bash
PYTHONPATH=. uv run python -c "from api.db.db_models import init_database_tables; init_database_tables()"
PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_housekeeping.py -v   # 4 PASS
uv tool run ruff check management/server/services/code_housekeeping.py test/multitenant/test_code_housekeeping.py
git add api/db/db_models.py management/server/services/code_housekeeping.py test/multitenant/test_code_housekeeping.py
git commit -m "feat(code): snapshots de spend + housekeeping (reconcile+snapshot) + courbe quotidienne"
```

---

### Task 2: Scheduler in-process + `POST /code/housekeeping`

**Files:**
- Modify: `management/server/main.py` (lifespan)
- Modify: `management/server/routers/code.py` (remplacer la route `POST /code/reconcile` par `POST /code/housekeeping` — garder `/code/reconcile` en alias)
- Test: `test/multitenant/test_code_routes_rbac.py` (adapter le test reconcile + 1 test housekeeping)

**Interfaces:**
- Consumes: `code_housekeeping.housekeeping()` (Task 1)
- Produces: tâche asyncio horaire (env guard `ADMIN_CODE_SCHEDULER`, défaut "1" ; intervalle `ADMIN_CODE_SCHEDULER_INTERVAL_S`, défaut 3600) ; routes `POST /api/admin/code/housekeeping` et alias `POST /api/admin/code/reconcile` (superuser) → report du housekeeping.

- [ ] **Step 1: Adapter les tests**

Dans `test/multitenant/test_code_routes_rbac.py`, remplacer `test_reconcile_superuser_only` par :

```python
def test_housekeeping_superuser_only_and_records_run(panel_client, org_with_entitlement_and_users):
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    assert client.post("/api/admin/code/housekeeping",
                       headers=_h(tokens["org_admin"])).status_code == 403
    r = client.post("/api/admin/code/housekeeping", headers=_h(tokens["superuser"]))
    assert r.status_code == 200
    assert "teams_snapshotted" in r.json()
    # alias rétro-compatible
    assert client.post("/api/admin/code/reconcile",
                       headers=_h(tokens["superuser"])).status_code == 200
```

- [ ] **Step 2: Run to verify FAIL** — `PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_routes_rbac.py -k housekeeping -v` → FAIL (404)

- [ ] **Step 3: Routes** (management/server/routers/code.py — remplacer la fonction `reconcile`)

```python
@router.post("/code/housekeeping")
def run_housekeeping(user=Depends(require_superuser)):
    """Force un passage reconcile + snapshot (le scheduler in-process le fait toutes les heures)."""
    from management.server.services.code_housekeeping import housekeeping
    return housekeeping()


@router.post("/code/reconcile")
def reconcile(user=Depends(require_superuser)):
    """Alias rétro-compatible de /code/housekeeping."""
    from management.server.services.code_housekeeping import housekeeping
    return housekeeping()
```

- [ ] **Step 4: Scheduler dans le lifespan** (management/server/main.py, dans `lifespan`, juste avant `yield`)

```python
    # CUSTOM B2B SaaS — Code product : housekeeping scheduler in-process.
    # Sleep-first (pas de run au boot), DB.lock dans housekeeping() -> multi-replica safe.
    # Désactivable via ADMIN_CODE_SCHEDULER=0 (tests / TestClient).
    import asyncio
    scheduler_task = None
    if os.getenv("ADMIN_CODE_SCHEDULER", "1") == "1":
        interval = int(os.getenv("ADMIN_CODE_SCHEDULER_INTERVAL_S", "3600"))

        async def _code_housekeeping_loop():
            from management.server.services.code_housekeeping import housekeeping
            while True:
                await asyncio.sleep(interval)
                try:
                    report = await asyncio.to_thread(housekeeping)
                    logging.info(f"code housekeeping run: {report}")
                except Exception:
                    logging.exception("code housekeeping run failed")

        scheduler_task = asyncio.get_running_loop().create_task(_code_housekeeping_loop())
        logging.info(f"code housekeeping scheduler started (interval={interval}s)")
    else:
        logging.warning("code housekeeping scheduler DISABLED (ADMIN_CODE_SCHEDULER=0)")

    yield

    if scheduler_task is not None:
        scheduler_task.cancel()
```

(le `yield` existant est remplacé par ce bloc ; `logging` est déjà importé dans le lifespan.)

- [ ] **Step 5: Run, verify, commit**

```bash
PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_routes_rbac.py test/multitenant/test_code_housekeeping.py -v
# Expected: ALL PASS (le TestClient déclenche le lifespan : sleep-first => aucune requête réelle,
# la task est annulée à la sortie ; si un test flake apparaît, exporter ADMIN_CODE_SCHEDULER=0 dans la fixture)
uv tool run ruff check management/server/main.py management/server/routers/code.py
git add management/server/main.py management/server/routers/code.py test/multitenant/test_code_routes_rbac.py
git commit -m "feat(code): scheduler housekeeping in-process (lifespan) + POST /code/housekeeping"
```

---

### Task 3: Spend par clé + route `GET /code/dashboard`

**Files:**
- Modify: `management/server/routers/code.py` (overview : spend par key ; nouvelle route dashboard)
- Modify: `test/multitenant/test_code_provisioning.py` (FakeLiteLLM : stocker les keys + `list_keys`)
- Test: `test/multitenant/test_code_routes_rbac.py` (2 tests)

**Interfaces:**
- Consumes: `LiteLLMClient.list_keys(team_id) -> list[dict]` (existant, items avec `token` + `spend`), `code_housekeeping.daily_spend_series` / `last_run` (Task 1), `code_provisioning.spend_by_litellm_team`.
- Produces:
  - `overview.teams[].keys[]` gagne `"spend": float | None`
  - `GET /api/admin/code/dashboard` → `{"kpis": {"cycle_spend": float|None, "active_orgs": int, "teams": int, "active_keys": int, "budget_alerts": int}, "daily": [{"date","spend"}], "top_orgs": [{"org_id","org_name","spend"}], "top_teams": [{"code_team_id","name","org_name","spend","max_budget"}], "last_housekeeping_at": str|None}` — RBAC : superuser → global, sinon scoped aux orgs membres (403 si aucune).

- [ ] **Step 1: FakeLiteLLM — stocker les keys** (test/multitenant/test_code_provisioning.py)

Dans `__init__` ajouter `self.keys: dict[str, dict] = {}`. Dans `generate_key`, avant le `return` :

```python
        self.keys[f"hash-{alias}"] = {"team_id": team_id, "key_alias": alias, "spend": 0.0}
```

Et ajouter la méthode :

```python
    def list_keys(self, team_id):
        self._maybe_down()
        return [{"token": tok, "key_alias": k["key_alias"], "spend": k.get("spend", 0.0)}
                for tok, k in self.keys.items() if k["team_id"] == team_id]
```

- [ ] **Step 2: Write the failing tests** (append à test_code_routes_rbac.py)

```python
def test_overview_exposes_per_key_spend(panel_client, org_with_entitlement_and_users):
    client, fake = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 40.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    key = client.post(f"/api/admin/code/teams/{team['id']}/keys",
                      json={"label": "dev-x"}, headers=_h(tokens["org_admin"])).json()["key"]
    token = next(t for t, k in fake.keys.items())
    fake.keys[token]["spend"] = 4.2

    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview", headers=_h(tokens["org_admin"])).json()
    key_row = next(k for k in ov["teams"][0]["keys"] if k["id"] == key["id"])
    assert key_row["spend"] == 4.2


def test_dashboard_rbac_and_shape(panel_client, org_with_entitlement_and_users, second_org_admin):
    client, fake = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 40.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    fake.teams[team["litellm_team_id"]]["spend"] = 39.0  # ≥ 80% de 40 → alerte

    d = client.get("/api/admin/code/dashboard", headers=_h(tokens["superuser"])).json()
    assert d["kpis"]["cycle_spend"] == 39.0
    assert d["kpis"]["teams"] >= 1 and d["kpis"]["budget_alerts"] >= 1
    assert isinstance(d["daily"], list)
    assert any(t["code_team_id"] == team["id"] for t in d["top_teams"])

    # scoped : l'admin de l'org B ne voit pas le spend de l'org A
    org_b_id, org_b_token, _ = second_org_admin
    db = client.get("/api/admin/code/dashboard", headers=_h(org_b_token)).json()
    assert all(t["code_team_id"] != team["id"] for t in db["top_teams"])
    assert db["kpis"]["cycle_spend"] in (0.0, None)
```

- [ ] **Step 3: Run to verify FAIL** — `-k "per_key_spend or dashboard_rbac"` → FAIL

- [ ] **Step 4: Implement** (management/server/routers/code.py)

Dans `code_overview`, après le calcul de `spend_map`, enrichir les keys (remplacer la boucle `keys_by_team`) :

```python
        keys_by_team = {}
        for t in teams:
            key_rows = list(CodeKey.select().where(CodeKey.code_team_id == t.id))
            key_spend = None
            if spend_map is not None and t.litellm_team_id:
                try:
                    from management.server.services.code_provisioning import _client
                    key_spend = {k.get("token"): float(k.get("spend") or 0.0)
                                 for k in _client().list_keys(t.litellm_team_id)}
                except Exception:
                    key_spend = None
            keys_by_team[t.id] = [
                {**_key_to_dict(k),
                 "spend": (key_spend or {}).get(k.litellm_key_id) if key_spend is not None else None}
                for k in key_rows]
```

**Attention ordre** : le calcul de `spend_map` doit être déplacé AVANT ce bloc (il est aujourd'hui après la boucle keys) — réorganiser la fonction : ent → teams → spend_map → keys_by_team → return.

Nouvelle route (après `orgs_summary`) :

```python
@router.get("/code/dashboard")
def code_dashboard(user=Depends(get_current_user)):
    """KPIs + courbe 30j + tops. Lecture pure (live LiteLLM + snapshots), n'écrit jamais."""
    from api.db.db_models import DB, CodeTeam, CodeKey, Organisation
    from api.db.services.org_service import OrgService, OrgMemberService
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import daily_spend_series, last_run

    if user.is_superuser:
        org_ids = None
    else:
        org_ids = [m.org_id for m in OrgMemberService.list_orgs_for_user(user.id)]
        if not org_ids:
            raise HTTPException(status_code=403, detail="Org membership required")

    with DB.connection_context():
        tq = CodeTeam.select().where(CodeTeam.status == "active")
        if org_ids is not None:
            tq = tq.where(CodeTeam.org_id.in_(org_ids))
        teams = list(tq)
        team_ids = [t.id for t in teams]
        active_keys = (CodeKey.select().where(
            (CodeKey.code_team_id.in_(team_ids)) & (CodeKey.status == "active")).count()
            if team_ids else 0)
        org_names = {o.id: o.name for o in Organisation.select(Organisation.id, Organisation.name)
                     .where(Organisation.id.in_(list({t.org_id for t in teams})))} if teams else {}

    spend_map = cp.spend_by_litellm_team()
    def _spend(t):
        if spend_map is None or not t.litellm_team_id:
            return None
        return spend_map.get(t.litellm_team_id, 0.0)

    team_spends = [(t, _spend(t)) for t in teams]
    known = [(t, s) for t, s in team_spends if s is not None]
    cycle_spend = round(sum(s for _, s in known), 4) if spend_map is not None else None
    alerts = sum(1 for t, s in known if t.max_budget > 0 and s >= 0.8 * t.max_budget)

    by_org: dict[str, float] = {}
    for t, s in known:
        by_org[t.org_id] = by_org.get(t.org_id, 0.0) + s
    top_orgs = [{"org_id": oid, "org_name": org_names.get(oid, oid), "spend": round(sp, 4)}
                for oid, sp in sorted(by_org.items(), key=lambda x: -x[1])[:5]]
    top_teams = [{"code_team_id": t.id, "name": t.name,
                  "org_name": org_names.get(t.org_id, t.org_id),
                  "spend": round(s, 4), "max_budget": t.max_budget}
                 for t, s in sorted(known, key=lambda x: -x[1])[:5]]

    run = last_run()
    return {
        "kpis": {"cycle_spend": cycle_spend,
                 "active_orgs": len({t.org_id for t in teams}),
                 "teams": len(teams), "active_keys": active_keys,
                 "budget_alerts": alerts},
        "daily": daily_spend_series(org_ids, days=30),
        "top_orgs": top_orgs, "top_teams": top_teams,
        "last_housekeeping_at": run.ran_at.isoformat() if run else None,
    }
```

- [ ] **Step 5: Run all backend suites, ruff, commit**

```bash
PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_routes_rbac.py test/multitenant/test_code_housekeeping.py test/multitenant/test_code_provisioning.py test/multitenant/test_code_reconcile.py test/multitenant/test_code_litellm_client.py -v
uv tool run ruff check management/server/routers/code.py test/multitenant/test_code_provisioning.py
git add management/server/routers/code.py test/multitenant/test_code_provisioning.py test/multitenant/test_code_routes_rbac.py
git commit -m "feat(code): spend par clé dans overview + route GET /code/dashboard (RBAC scoped)"
```

---

### Task 4: Front — dashboard Code + auto-refresh + card globale

**Files:**
- Create: `management/web/src/pages/code/dashboard-section.tsx`
- Modify: `management/web/src/pages/code/index.tsx` (afficher la section au-dessus du tableau ; colonne « Dépensé » par clé)
- Modify: `management/web/src/pages/dashboard/index.tsx` (card « Produit Code »)

**Interfaces:**
- Consumes: `GET /code/dashboard` (Task 3), `overview.teams[].keys[].spend`
- Produces: composant `<CodeDashboardSection />` (auto-refresh interne 30 s visibility-aware, expose rien)

- [ ] **Step 1: Composant dashboard**

```tsx
// management/web/src/pages/code/dashboard-section.tsx
import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, Col, Row, Statistic, Table, Tag, Progress } from 'antd';
import { EuroOutlined, BankOutlined, TeamOutlined, KeyOutlined, WarningOutlined } from '@ant-design/icons';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import api from '@/lib/api';

interface DashboardData {
  kpis: { cycle_spend: number | null; active_orgs: number; teams: number; active_keys: number; budget_alerts: number };
  daily: { date: string; spend: number }[];
  top_orgs: { org_id: string; org_name: string; spend: number }[];
  top_teams: { code_team_id: string; name: string; org_name: string; spend: number; max_budget: number }[];
  last_housekeeping_at: string | null;
}

const REFRESH_MS = 30_000;

export default function CodeDashboardSection() {
  const [data, setData] = useState<DashboardData | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(() => {
    api.get('/code/dashboard').then((res) => setData(res.data)).catch(() => {});
  }, []);

  useEffect(() => {
    fetchData();
    const start = () => { if (!timer.current) timer.current = setInterval(fetchData, REFRESH_MS); };
    const stop = () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };
    const onVisibility = () => { if (document.hidden) stop(); else { fetchData(); start(); } };
    start();
    document.addEventListener('visibilitychange', onVisibility);
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility); };
  }, [fetchData]);

  if (!data) return <Card loading className="mb-4" />;
  const { kpis } = data;

  return (
    <div className="mb-4">
      <Row gutter={12} className="mb-3">
        <Col span={5}><Card size="small"><Statistic title="Dépensé (cycle)" prefix={<EuroOutlined />}
          value={kpis.cycle_spend ?? '—'} suffix={kpis.cycle_spend != null ? '€' : ''} /></Card></Col>
        <Col span={5}><Card size="small"><Statistic title="Orgs actives" prefix={<BankOutlined />} value={kpis.active_orgs} /></Card></Col>
        <Col span={5}><Card size="small"><Statistic title="Teams" prefix={<TeamOutlined />} value={kpis.teams} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="Clés actives" prefix={<KeyOutlined />} value={kpis.active_keys} /></Card></Col>
        <Col span={5}><Card size="small"><Statistic title="Alertes budget (≥80%)" prefix={<WarningOutlined />}
          value={kpis.budget_alerts} valueStyle={kpis.budget_alerts > 0 ? { color: '#cf1322' } : undefined} /></Card></Col>
      </Row>
      <Row gutter={12}>
        <Col span={12}>
          <Card size="small" title="Dépense par jour (30 j)">
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={data.daily}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} />
                <YAxis />
                <Tooltip formatter={(v: number) => `${v} €`} />
                <Area type="monotone" dataKey="spend" stroke="#6366f1" fill="#6366f1" fillOpacity={0.25} />
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" title="Top organisations">
            <Table rowKey="org_id" size="small" pagination={false} showHeader={false}
              dataSource={data.top_orgs}
              columns={[{ dataIndex: 'org_name' },
                        { dataIndex: 'spend', width: 90, render: (v: number) => `${v} €` }]} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small" title="Top teams">
            <Table rowKey="code_team_id" size="small" pagination={false} showHeader={false}
              dataSource={data.top_teams}
              columns={[
                { dataIndex: 'name', render: (v: string, r) => <>{v} <Tag>{r.org_name}</Tag></> },
                { width: 110, render: (_: unknown, r) => (
                    <Progress size="small" percent={r.max_budget ? Math.min(100, Math.round((r.spend / r.max_budget) * 100)) : 0}
                              status={r.spend >= r.max_budget ? 'exception' : undefined} />) },
              ]} />
          </Card>
        </Col>
      </Row>
      {data.last_housekeeping_at && (
        <div className="text-gray-400 text-xs mt-2">
          Dernier relevé : {new Date(data.last_housekeeping_at).toLocaleString()}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: L'intégrer + colonne « Dépensé » par clé** (pages/code/index.tsx)

1. `import CodeDashboardSection from './dashboard-section';` ; dans le rendu no-org, insérer `<CodeDashboardSection />` entre le header (titre + recherche) et la `<Card>` du tableau.
2. `CodeKey` interface : ajouter `spend: number | null;`.
3. Dans `keyColumns`, après la colonne « Clé » :

```tsx
    { title: 'Dépensé', dataIndex: 'spend', width: 100,
      render: (v: number | null) => (v == null ? '—' : `${Math.round(v * 100) / 100} €`) },
```

- [ ] **Step 3: Card « Produit Code » dans le Dashboard global** (pages/dashboard/index.tsx)

Ajouter un state + fetch (une fois, pas d'auto-refresh ici) et une card dans la grille des KPIs existants (repérer la `Row` des `KpiCard`) :

```tsx
const [codeKpis, setCodeKpis] = useState<{ cycle_spend: number | null; active_orgs: number; budget_alerts: number } | null>(null);
useEffect(() => {
  api.get('/code/dashboard').then((r) => setCodeKpis(r.data.kpis)).catch(() => {});
}, []);
```

```tsx
{codeKpis && (
  <Col xs={24} md={8}>
    <Card size="small" title="Produit Code" extra={<Link to="/code">→ ouvrir</Link>}>
      <Row gutter={8}>
        <Col span={8}><Statistic title="Dépensé (cycle)" value={codeKpis.cycle_spend ?? '—'} suffix={codeKpis.cycle_spend != null ? '€' : ''} /></Col>
        <Col span={8}><Statistic title="Orgs actives" value={codeKpis.active_orgs} /></Col>
        <Col span={8}><Statistic title="Alertes" value={codeKpis.budget_alerts}
          valueStyle={codeKpis.budget_alerts > 0 ? { color: '#cf1322' } : undefined} /></Col>
      </Row>
    </Card>
  </Col>
)}
```

(adapter `xs/md` à la grille voisine ; `Link` est déjà importé dans ce fichier.)

- [ ] **Step 4: Build + vérif Playwright**

```bash
cd management/web && PATH=/opt/homebrew/bin:$PATH npm run build   # zéro erreur TS
```
Puis drive Playwright (adapter `qa_ui_table.py`) : landing → KPIs visibles (`Dépensé (cycle)`) + courbe rendue (`svg` recharts présent) + top teams ; forcer `POST /code/housekeeping` (token superuser) puis reload → « Dernier relevé » affiché ; vue org → colonne « Dépensé » dans la table des clés ; Dashboard global → card « Produit Code ».

- [ ] **Step 5: Commit**

```bash
git add management/web/src/pages/code/dashboard-section.tsx management/web/src/pages/code/index.tsx management/web/src/pages/dashboard/index.tsx
git commit -m "feat(code-ui): dashboard Code (KPIs, courbe 30j, tops, auto-refresh 30s) + card globale + spend par clé"
```

## Self-review

- **Spec coverage** : table+scheduler §1 → Tasks 1-2 ; spend par siège §2 → Tasks 3-4 ; dashboard §3 (KPIs/courbe/tops/last-run/auto-refresh) → Tasks 3-4 ; card globale §4 → Task 4 ; tests §5 → chaque task. Différés du spec respectés (pas d'alerting push, pas de purge, pas d'export).
- **Placeholders** : aucun.
- **Type consistency** : `daily_spend_series(org_ids, days)` (T1) = appel T3 ; report housekeeping keys (T1) = assertions T2 ; shape dashboard (T3) = interface `DashboardData` (T4) ; `spend` nullable partout où le gateway peut être down.
