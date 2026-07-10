# Design — Dashboard produit Code + spend par siège

- **Date** : 2026-07-08 · **Statut** : validé (brainstorming)
- **Demande** : vue agrégée du produit Code (« c'est trop peu là ») + consommation par user/siège.
- **Décisions actées** : courbes dès la v1 (tout est en dev) · dashboard **dans l'onglet Code** + 1 card résumé dans le Dashboard global · capture par **scheduler in-process** (pas de read-through, pas de cron requis).

## 1. Capture d'historique — table + scheduler

### Table `code_spend_snapshot` (MariaDB, api/db/db_models.py, marqueurs CUSTOM B2B SaaS)
| Champ | Type | Note |
|---|---|---|
| `id` | char(32) PK | |
| `snap_date` | date, index | jour du snapshot |
| `org_id` | char(32), index | dénormalisé pour agrégation directe |
| `code_team_id` | char(32), index | |
| `spend` | float | **cumulé du cycle en cours** (tel que LiteLLM le rapporte) |
| `max_budget` | float | budget de la team au moment du snapshot |
| **unique** | `(snap_date, code_team_id)` | upsert idempotent |

Courbe quotidienne = delta entre snapshots consécutifs ; delta négatif = reset de cycle → le delta du jour = valeur du jour.

### Scheduler in-process (management/server/main.py, lifespan FastAPI)
- Tâche asyncio démarrée au startup : boucle horaire → `housekeeping()` = `reconcile_all()` + `snapshot_spend()`.
- **Protégée par `DB.lock`** (même mécanisme que `init_database_tables`) → safe multi-replicas.
- **Log de démarrage explicite** (`"code housekeeping scheduler started (interval=1h)"`) — leçon du freeze silencieux asgi.py 2026-06-30.
- Chaque run persiste `last_housekeeping_at` (table existante de settings ou champ dédié simple) → affiché dans le dashboard (« Dernier relevé : il y a X min ») pour que l'absence du scheduler soit détectable en 5 s.
- `POST /api/admin/code/housekeeping` (superuser) pour forcer un passage. Le GET dashboard **n'écrit jamais**.
- `snapshot_spend()` : un seul `GET /team/list`, upsert par (date, team). Gateway down → run loggé en warning, pas d'écriture partielle.

## 2. Spend par siège/clé

- `LiteLLMClient.list_keys(team_id)` existe déjà — chaque item porte le `spend` de la clé.
- Route `overview` : enrichit chaque key avec `spend` (1 appel `/key/list` par team de l'org — volumes faibles ; `null` si gateway down, jamais 0).
- UI vue org : colonne **« Dépensé »** dans la table des clés de chaque team.
- Le rattachement humain reste `label` / `owner_user_id` (le « user » du spend = le siège).

## 3. Dashboard Code (landing de l'onglet, au-dessus du tableau des orgs)

Nouvelle route `GET /api/admin/code/dashboard` (RBAC : superuser → global ; org member → scoped à ses orgs) :

```json
{
  "kpis": {"cycle_spend": 123.4, "active_orgs": 3, "teams": 7, "active_keys": 21,
            "budget_alerts": 2},              // teams à ≥ 80 % de leur budget
  "daily": [{"date": "2026-07-01", "spend": 12.5}, ...],   // 30 j, delta des snapshots
  "top_orgs": [{"org_id", "org_name", "spend"}, ...],       // top 5, cycle courant (live)
  "top_teams": [{"code_team_id", "name", "org_name", "spend", "max_budget"}, ...],
  "last_housekeeping_at": "2026-07-08T14:00:00Z"            // health du scheduler
}
```

UI (réutilise les patterns du Dashboard global : `KpiCard`, recharts `AreaChart`) :
- Rangée de KPIs (dont badge alertes budget, rouge si > 0)
- `AreaChart` spend/jour 30 j
- Deux mini-tables : top orgs / top teams (avec barre spend/budget)
- Mention discrète « Dernier relevé : … »
- Le tout au-dessus du tableau d'orgs existant (masqué pendant la recherche ? non — reste affiché, simple)

**Auto-refresh (temps réel perçu, charge bornée par la présence)** : le front re-fetch `GET /code/dashboard` toutes les **30 s** tant que la page est **ouverte et visible** (pause via l'API `document.visibilitychange` quand l'onglet est en arrière-plan ; reprise + fetch immédiat au retour). Charge : 2 req/min par admin présent, zéro quand personne ne regarde — les KPIs/tops étant des lectures live LiteLLM + SELECTs snapshots triviaux, pas de risque DB. Pas de WebSocket/SSE (sur-ingénierie pour un écran d'admin).

## 4. Card Code dans le Dashboard global

Une `Card` « Produit Code » dans `pages/dashboard/index.tsx` : spend total du cycle + orgs actives + alertes budget + lien `→ /code`. Alimentée par le même `GET /code/dashboard` (kpis seulement). Visible selon le même gate que l'onglet Code.

## 5. Tests

- **Unit** : `snapshot_spend()` upsert idempotent (2 runs même jour → 1 ligne) · delta de courbe avec reset de cycle (spend descend → delta = valeur du jour) · gateway down → pas d'écriture.
- **Routes** : RBAC dashboard (superuser global vs member scopé) · shape des kpis/daily · `spend` par clé dans overview (FakeLiteLLM enrichi d'un spend par key).
- **Scheduler** : le lifespan démarre la tâche (flag/mock) · `POST /housekeeping` déclenche reconcile+snapshot (FakeLiteLLM).
- **Visuel** : drive Playwright du dashboard (KPIs visibles, courbe rendue, card globale présente).

## Extension v1.1 (validée 2026-07-09) — tokens + erreurs

Alignement avec le Dashboard RAG (qui parle en tokens) : la consommation s'affiche en **tokens**, la facturation en **€**, la santé en **erreurs**.

- **Source** : spend-logs LiteLLM (chaque requête loggée avec prompt/completion tokens + statut). ⚠️ La forme exacte de l'endpoint d'agrégation OSS doit être **validée contre le vrai container en début d'implémentation** (même méthode que le champ `token` de `/key/generate`) ; le fallback si aucun agrégat OSS ne convient = lecture des logs bruts bornée à la journée (volumes faibles).
- **Capture** : le housekeeping enrichit le snapshot du jour de colonnes `tokens` et `errors` **du jour** par team (valeurs quotidiennes directes, pas de delta).
- **Dashboard** : KPIs « Tokens (30 j) » (format K/M identique au Dashboard RAG) et « Erreurs (30 j) » (rouge si > 0) ; tokens dans la courbe quotidienne ; tokens dans top teams.
- **Overview org** : tokens par team.
- **Sémantique** : `null` = donnée indisponible (gateway down / endpoint absent), jamais 0.

## Hors périmètre (différé)
- Alerting push (email/Slack) sur dépassement — le badge suffit en v1.
- Rétention/purge des snapshots (volumes négligeables : orgs × teams × 365 ≈ quelques milliers de lignes/an).
- Export CSV / facturation — c'est le ledger unifié.
- A/B testing (traffic-split pondéré du routeur LiteLLM) — quand un 2ᵉ modèle code existera.
- Métriques perf/infra (TTFT, débit, KV-cache, GPU) — plan ops : scrape Prometheus du `/metrics` natif vLLM → Grafana, pas le panel.
