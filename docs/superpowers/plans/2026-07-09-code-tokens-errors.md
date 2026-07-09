# Code Dashboard v1.1 — Tokens + Erreurs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Afficher la consommation en tokens et les erreurs (alignement Dashboard RAG) dans l'onglet Code, capturées quotidiennement par le housekeeping.

**Architecture:** Le client LiteLLM gagne `daily_usage()` (agrégat tokens/erreurs par team pour un jour donné, depuis les spend-logs — endpoint validé contre le vrai container en Task 1). Le snapshot du jour gagne 2 colonnes quotidiennes directes (`tokens`, `errors`, nullable). La route dashboard agrège sur 30 j ; le front ajoute 2 KPIs (format K/M du Dashboard RAG), les tokens dans la courbe et les tops.

**Tech Stack:** LiteLLM spend-logs API (validée Task 1), Peewee (colonnes nullable), recharts (2ᵉ axe Y).

**Spec:** `docs/superpowers/specs/2026-07-08-code-dashboard-design.md` §Extension v1.1

## Global Constraints

- **`null` = indisponible, jamais 0** — tokens/erreurs compris (gateway down OU endpoint absent → colonnes snapshot NULL, KPIs `null`, front `—`).
- Les valeurs snapshot `tokens`/`errors` sont **quotidiennes directes** (pas cumulées, pas de delta).
- Le contrat de `daily_usage` est fixé par la **réalité du container** (Task 1), pas par la doc — même méthode que le champ `token` de `/key/generate`.
- Format tokens au front : réutiliser la convention K/M du Dashboard RAG (`fmtTokens`).
- Tests env : `ADMIN_JWT_SECRET` depuis `/Users/zappy/ragflow/.env.local` ; suites robustes aux données réelles de la DB partagée (asserts scopés/`>=`, pattern des tests existants) ; intégration gated `LITELLM_TEST_URL`.
- Commits français conventionnels, JAMAIS de Co-Authored-By.

---

### Task 1: `LiteLLMClient.daily_usage()` — validé contre le vrai container

**Files:**
- Modify: `management/server/services/litellm_client.py`
- Test: `test/multitenant/test_code_litellm_client.py` (unit, MockTransport) + `test/multitenant/test_code_litellm_integration.py` (append, gated `LITELLM_TEST_URL`)

**Interfaces:**
- Produces: `daily_usage(day: datetime.date) -> dict[str, dict] | None` — map `litellm_team_id -> {"tokens": int, "errors": int}` pour la journée UTC `day` ; `{}` si aucun trafic ; lève `LiteLLMError` si gateway injoignable (les appelants convertissent en None/NULL). Contrat de champs fixé après validation réelle.

- [ ] **Step 1: Spike contre le vrai container (décide l'endpoint)**

```bash
docker compose -f docker/litellm-test/docker-compose.yml up -d
# attendre /health/liveliness → 200, créer team+key via LiteLLMClient, faire 2 completions mock
# (réutiliser le pattern de test_code_litellm_integration.py)
# Candidats à sonder avec le MASTER_KEY (sk-master-test-only), dans cet ordre :
curl -s -H "Authorization: Bearer sk-master-test-only" "http://localhost:4000/spend/logs?start_date=2026-07-09&end_date=2026-07-09" | head -c 2000
curl -s -H "Authorization: Bearer sk-master-test-only" "http://localhost:4000/spend/logs" | head -c 2000
# noter les champs réels : team_id, prompt_tokens/completion_tokens (ou total_tokens), status, startTime…
# si /spend/logs est paginé/filtrable par date → l'utiliser ; s'il renvoie du par-requête brut → agréger client-side.
# Si un endpoint renvoie 4xx "premium/enterprise" → le documenter et passer au candidat suivant.
```

Documenter la forme observée en commentaire de `daily_usage` (« validated against main-v1.74.0-stable: … »).

- [ ] **Step 2: Write the failing unit test (MockTransport reflétant la forme OBSERVÉE)**

```python
# append à test/multitenant/test_code_litellm_client.py — ADAPTER le JSON mock à la forme
# réellement observée au Step 1 (celui-ci suppose du par-requête brut) :
def test_daily_usage_aggregates_tokens_and_errors_per_team():
    import datetime

    def handler(request):
        assert request.url.path == "/spend/logs"
        return httpx.Response(200, json=[
            {"team_id": "t1", "prompt_tokens": 10, "completion_tokens": 5, "status": "success"},
            {"team_id": "t1", "prompt_tokens": 8, "completion_tokens": 2, "status": "failure"},
            {"team_id": "t2", "prompt_tokens": 1, "completion_tokens": 1, "status": "success"},
        ])

    client = make_client(handler)
    usage = client.daily_usage(datetime.date(2026, 7, 9))
    assert usage["t1"] == {"tokens": 25, "errors": 1}
    assert usage["t2"] == {"tokens": 2, "errors": 0}


def test_daily_usage_gateway_down_raises():
    from management.server.services.litellm_client import LiteLLMError
    import datetime

    def handler(request):
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LiteLLMError):
        client.daily_usage(datetime.date(2026, 7, 9))
```

- [ ] **Step 3: Implement `daily_usage`** (forme finale selon Step 1 ; squelette si par-requête brut)

```python
    def daily_usage(self, day) -> dict:
        """Tokens + erreurs par team pour la journée UTC `day` (spend-logs).

        Validated against ghcr.io/berriai/litellm main-v1.74.0-stable: <compléter au Step 1>.
        Raises LiteLLMError when the gateway is unreachable — callers map to None/NULL.
        """
        params = {"start_date": day.isoformat(), "end_date": day.isoformat()}
        rows = self._request("GET", "/spend/logs", params=params)
        out: dict[str, dict] = {}
        for r in rows:
            tid = r.get("team_id")
            if not tid:
                continue
            agg = out.setdefault(tid, {"tokens": 0, "errors": 0})
            agg["tokens"] += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)
            if (r.get("status") or "success") != "success":
                agg["errors"] += 1
        return out
```

- [ ] **Step 4: Integration test (append à test_code_litellm_integration.py, même gating)**

```python
def test_daily_usage_counts_real_traffic(client):
    import datetime
    alias = f"org:it:team:{uuid.uuid4().hex[:8]}"
    team_id = client.create_team(alias=alias, max_budget=50.0, budget_duration="1mo", models=[])
    out = client.generate_key(team_id=team_id, alias=f"org:it:key:{uuid.uuid4().hex[:8]}")
    for _ in range(2):
        httpx.post(f"{BASE}/v1/chat/completions",
                   headers={"Authorization": f"Bearer {out['plain_key']}"},
                   json={"model": "code-mock", "messages": [{"role": "user", "content": "hi"}]},
                   timeout=30.0)
    import time; time.sleep(8)  # propagation spend-logs
    usage = client.daily_usage(datetime.datetime.now(datetime.timezone.utc).date())
    assert team_id in usage
    assert usage[team_id]["tokens"] > 0
```

- [ ] **Step 5: Run (unit sans réseau, intégration avec stack), ruff, commit ; `docker compose ... down -v`**

```bash
PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_litellm_client.py -v
LITELLM_TEST_URL=http://localhost:4000 PYTHONPATH=. uv run python -m pytest test/multitenant/test_code_litellm_integration.py -k daily_usage -v
git commit -m "feat(code): LiteLLMClient.daily_usage — tokens+erreurs par team (validé container réel)"
```

---

### Task 2: Colonnes snapshot + capture housekeeping + série quotidienne enrichie

**Files:**
- Modify: `api/db/db_models.py` (2 colonnes sur `CodeSpendSnapshot`, dans le bloc marqueurs)
- Modify: `management/server/services/code_housekeeping.py`
- Test: `test/multitenant/test_code_housekeeping.py` (append)

**Interfaces:**
- Consumes: `daily_usage(day)` (Task 1)
- Produces: `CodeSpendSnapshot.tokens`/`.errors` (`IntegerField(null=True)` — NULL = indisponible) ; `daily_spend_series` retourne `[{"date", "spend", "tokens": int|None, "errors": int|None}]` (somme par jour ; None si toutes les valeurs du jour sont NULL).

- [ ] **Step 1: Failing tests (append, utiliser la fixture `hk_org` existante + FakeLiteLLM)**

D'abord ajouter à `FakeLiteLLM` (test_code_provisioning.py) : `self.usage: dict[str, dict] = {}` dans `__init__` et

```python
    def daily_usage(self, day):
        self._maybe_down()
        return self.usage
```

Puis :

```python
def test_snapshot_captures_daily_tokens_and_errors(hk_org):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend, daily_spend_series
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    fake.teams[team.litellm_team_id]["spend"] = 3.0
    fake.usage = {team.litellm_team_id: {"tokens": 1234, "errors": 2}}
    assert snapshot_spend(client=fake) >= 1

    with DB.connection_context():
        row = CodeSpendSnapshot.get(CodeSpendSnapshot.code_team_id == team.id)
    assert row.tokens == 1234 and row.errors == 2

    series = daily_spend_series([hk_org], days=2)
    today = series[-1]
    assert today["tokens"] >= 1234 and today["errors"] >= 2


def test_snapshot_tokens_null_when_usage_unavailable(hk_org, monkeypatch):
    """spend dispo mais usage KO (endpoint absent) → tokens/errors restent NULL, spend écrit."""
    from management.server.services import code_provisioning as cp
    from management.server.services.code_housekeeping import snapshot_spend
    from management.server.services.litellm_client import LiteLLMError
    from api.db.db_models import DB, CodeSpendSnapshot

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=hk_org, name="t", max_budget=50.0,
                               model_access=[], created_by="tester", client=fake)
    def broken_usage(day):
        raise LiteLLMError("spend-logs unavailable")
    fake.daily_usage = broken_usage
    assert snapshot_spend(client=fake) >= 1
    with DB.connection_context():
        row = CodeSpendSnapshot.get(CodeSpendSnapshot.code_team_id == team.id)
    assert row.tokens is None and row.errors is None and row.spend == 0.0
```

- [ ] **Step 2: Run → FAIL. Step 3: Implement**

Modèle : `tokens = IntegerField(null=True)` + `errors = IntegerField(null=True)` sur `CodeSpendSnapshot` (+ `init_database_tables()` pour créer ; colonnes nouvelles sur table existante → ajouter 2 `alter_db_column_type`? NON — colonnes absentes : utiliser le helper d'ajout de colonnes du fichier (`migrate(migrator.add_column(...))` pattern présent dans `migrate_db` — grep `add_column` et imiter, wrap try/except comme les voisins).

`snapshot_spend` : après `spend_map`, tenter `usage = cl.daily_usage(today)` dans un try/except `LiteLLMError` → `usage = None`. Dans la boucle d'upsert : `u = (usage or {}).get(t.litellm_team_id)` ; colonnes `tokens=u["tokens"] if u else (0 if usage is not None else None)` — attention : team présente dans teams mais absente d'usage avec usage dispo = 0 réel (aucun trafic ce jour) ; usage None = NULL. Même logique pour `errors`. Ajouter les 2 colonnes au `update={}` de l'upsert (les 2 branches MySQL/Postgres).

`daily_spend_series` : sommer aussi tokens/errors par jour (`None`-safe : si toutes les rows du jour ont NULL → None ; sinon somme des non-NULL).

- [ ] **Step 4: Run all housekeeping tests (anciens + nouveaux) ×2, ruff. Step 5: Commit**

```bash
git commit -m "feat(code): snapshots enrichis tokens+erreurs quotidiens (NULL = indisponible)"
```

---

### Task 3: Route dashboard + overview enrichis

**Files:**
- Modify: `management/server/routers/code.py`
- Test: `test/multitenant/test_code_routes_rbac.py` (append)

**Interfaces:**
- Consumes: série enrichie (Task 2), `daily_usage` (Task 1)
- Produces: `GET /code/dashboard` → `kpis` gagne `"tokens_30d": int|None` et `"errors_30d": int|None` (sommes de la série ; None si tous None) ; `top_teams[]` gagne `"tokens": int|None` (valeur du jour via `daily_usage`, None si indisponible). `GET /orgs/{id}/code/overview` → chaque team gagne `"tokens_today": int|None`.

- [ ] **Step 1: Failing tests**

```python
def test_dashboard_exposes_tokens_and_errors(panel_client, org_with_entitlement_and_users):
    client, fake = panel_client
    org_id, tokens = org_with_entitlement_and_users
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 40.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    fake.usage = {team["litellm_team_id"]: {"tokens": 500, "errors": 1}}
    # snapshot du jour via housekeeping (client fake patché par la fixture)
    client.post("/api/admin/code/housekeeping", headers=_h(tokens["superuser"]))

    d = client.get("/api/admin/code/dashboard", headers=_h(tokens["superuser"])).json()
    assert d["kpis"]["tokens_30d"] >= 500
    assert d["kpis"]["errors_30d"] >= 1
    mine = next(t for t in d["top_teams"] if t["code_team_id"] == team["id"])
    assert mine["tokens"] == 500

    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview", headers=_h(tokens["org_admin"])).json()
    assert ov["teams"][0]["tokens_today"] == 500
```

(Nettoyage : la fixture panel_client nettoie déjà les runs ; les snapshots créés ici appartiennent à l'org de la fixture — vérifier que le teardown org supprime aussi ses CodeSpendSnapshot ; sinon l'ajouter au conftest.)

- [ ] **Step 2: Run → FAIL. Step 3: Implement**

Dashboard : `tokens_30d`/`errors_30d` = sommes None-safe de la série (déjà calculée) ; `top_teams` : un seul `daily_usage(today)` try/except → map, `tokens` par team. Overview : même appel unique `daily_usage(today)` (try/except → None) → `tokens_today` par team.

- [ ] **Step 4: Run suites RBAC + housekeeping ×2, ruff. Step 5: Commit**

```bash
git commit -m "feat(code): tokens+erreurs dans dashboard (kpis 30j, top teams) et overview (tokens_today)"
```

---

### Task 4: Front — KPIs, courbe, tops, cards teams

**Files:**
- Modify: `management/web/src/pages/code/dashboard-section.tsx`
- Modify: `management/web/src/pages/code/index.tsx`

**Interfaces:**
- Consumes: shapes Task 3.

- [ ] **Step 1: dashboard-section.tsx**

1. Types : `kpis` += `tokens_30d: number | null; errors_30d: number | null;` ; `daily[]` += `tokens: number | null; errors: number | null;` ; `top_teams[]` += `tokens: number | null;`.
2. Copier le helper du Dashboard RAG (même formatage) :

```tsx
const fmtTokens = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n ?? 0);
};
```

3. Rangée KPIs : passer les `Col` à `span` cohérents (7 cards → utiliser `Row gutter` avec spans 4/3/3/3/3/4/4 ou deux lignes — garder lisible) et ajouter :

```tsx
<Col span={3}><Card size="small"><Statistic title="Tokens (30 j)"
  value={kpis.tokens_30d == null ? '—' : fmtTokens(kpis.tokens_30d)} /></Card></Col>
<Col span={3}><Card size="small"><Statistic title="Erreurs (30 j)"
  value={kpis.errors_30d ?? '—'}
  valueStyle={(kpis.errors_30d ?? 0) > 0 ? { color: '#cf1322' } : undefined} /></Card></Col>
```

4. Courbe : 2ᵉ axe Y + 2ᵉ Area tokens :

```tsx
<YAxis yAxisId="left" />
<YAxis yAxisId="right" orientation="right" tickFormatter={(v: number) => fmtTokens(v)} />
<Tooltip formatter={(v, name) => name === 'tokens' ? fmtTokens(Number(v)) : `${v} €`} />
<Area yAxisId="left" type="monotone" dataKey="spend" stroke="#6366f1" fill="#6366f1" fillOpacity={0.25} />
<Area yAxisId="right" type="monotone" dataKey="tokens" stroke="#10b981" fill="#10b981" fillOpacity={0.15} />
```

5. Top teams : afficher `{fmtTokens(r.tokens)} tokens` (ou `—`) à côté de la barre de budget.

- [ ] **Step 2: index.tsx** — `CodeTeam` += `tokens_today: number | null;` ; sur la card de team, à côté du tag spend :

```tsx
<Tag color="geekblue">{team.tokens_today == null ? '— tokens' : `${fmtTokens(team.tokens_today)} tokens auj.`}</Tag>
```

(exporter `fmtTokens` depuis dashboard-section.tsx ou le dupliquer localement — préférer un export nommé.)

- [ ] **Step 3: Build + vérif Playwright** — backend 9381 relancé (nouveau code), housekeeping forcé, page Code : KPIs tokens/erreurs visibles, 2ᵉ courbe rendue, tag tokens sur la card team. Screenshot.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(code-ui): tokens + erreurs — KPIs 30j, double courbe, tokens par team"
```

## Self-review

Spec §v1.1 : source validée container (T1) ✅ ; capture quotidienne directe (T2) ✅ ; KPIs/courbe/tops + overview (T3-T4) ✅ ; null ≠ 0 partout (contraintes + tests dédiés) ✅ ; format K/M RAG ✅. Placeholders : la forme du mock unit T1 est explicitement à adapter à l'observation réelle — c'est le mécanisme voulu, documenté, pas un TBD. Type consistency : `daily_usage` (T1) = consommé T2/T3 ; shapes T3 = types T4.
