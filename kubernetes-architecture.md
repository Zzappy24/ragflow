# Architecture Kubernetes — RAGFlow Multi-Tenant

> Audit réalisé avril 2026. Modèles cibles : Gemma4 35B MoE ou Qwen3 30B MoE sur 4× Blackwell RTX 6000 96GB.

---

## Contexte

RAGFlow fork B2B SaaS avec couche multi-tenant RBAC (orgs, workspaces, rôles).
Stack : Quart/Hypercorn · Peewee/MySQL · Redis · Infinity · vLLM · Kubernetes.

---

## Audit isolation inter-tenant

### Serveur HTTP — Quart async

- Quart + Hypercorn = ASGI async. Les handlers sont `async def`, le streaming LLM est non-bloquant.
- **Problème** : Peewee ORM est synchrone (malgré le "asyncio support" du README, aucune API async dans Peewee 3.19.0). Les appels DB bloquent l'event loop.
- **Mitigation** : multi-pod Kubernetes (HPA) — chaque pod a son propre event loop. Un pod lent n'affecte pas les autres.
- Migration async DB : **non prioritaire**. Le DB représente < 0.1% du temps d'une requête chat. Le LLM domine tout.

### Pipeline d'indexation (task_executor)

- Queue Redis Streams (xreadgroup), FIFO global, deux niveaux de priorité (`svr_queue` et `svr_queue_1`).
- Par défaut `WS=1` = 1 process. Une org avec un gros upload bloque tout le monde.
- **Fix** : deux Deployments Kubernetes task-executor sur deux queues séparées (standard/premium).

### Inférence LLM (vLLM)

- Aucun rate limiting par org dans RAGFlow ni dans vLLM (scheduler FIFO global).
- **Quota mensuel par org / virtual keys par workspace** : déjà couvert par notre layer custom (`api/db/services/quota_service.py`, `Organisation.max_tokens_monthly`, API keys per-workspace dans le panel admin).
- **Rate limiting RPS real-time par org** : seul vrai gap restant. Plusieurs solutions possibles, par ordre de préférence pour notre contexte souverain :
  1. **nginx ingress rate limiting** sur `X-Workspace-Id` (2h config, 0 nouvelle stack — privilégié)
  2. **vLLM `--max-num-seqs N`** côté backend pour cap concurrent global (1h config)
  3. **LiteLLM Proxy** devant vLLM — alternative plus complète (RPS + virtual keys + dashboards + failover) mais ajoute une stack supplémentaire (Postgres dédié + container LiteLLM). Voir section dédiée plus bas.
- Deux instances vLLM (standard/premium) — l'isolation tier-level est de toute façon nécessaire pour la qualité de service.

### Stockage vectoriel (Infinity)

- Infinity est léger (~200MB RAM idle) et est le moteur par défaut.
- Isolation données : déjà en place — tables par tenant/KB (`ragflow_{tenant_id}_{kb_id}`).
- Isolation compute : non — une seule instance partagée par défaut.
- **Modèle cible** : plusieurs instances Infinity (StatefulSet + PVC), routage par org dans le code RAGFlow.
- Elasticsearch écarté : trop lourd opérationnellement pour ce use case.

### Base de données MySQL

- Peewee PooledMySQLDatabase — pool de connexions présent mais non configuré explicitement.
- Non bloquant jusqu'à ~50 users simultanés actifs.
- Si besoin : ProxySQL comme connection pooler, puis read replicas pour les dashboards analytics.

### Rate limiting HTTP

- Inexistant dans RAGFlow par org/tenant.
- **Fix** : annotations nginx ingress sur `X-Workspace-Id`. Config pure, zéro code.

---

## Ce que Kubernetes résout automatiquement

| Problème | Solution K8s | Effort |
|---|---|---|
| Single process Quart | HPA + N replicas | Config |
| Event loop bloqué par DB | Isolation par pod | Config |
| Scaling LLM | GPU node pool | Config |
| Multi-instance Infinity | StatefulSet + PVC | Config |
| Failure domain | Pod restart indépendant | Natif |

---

## Ce que Kubernetes ne résout pas (nécessite du code ou config applicative)

| Problème | Solution | Effort |
|---|---|---|
| Saturation vLLM inter-org | nginx ingress rate-limit + vLLM `--max-num-seqs` (option lourde : LiteLLM Proxy) | 2-3h (option lourde : 1-2 jours) |
| Indexation inter-org | 2 queues + 2 Deployments | 1 jour |
| Routage multi-Infinity | Code RAGFlow : org→URI router | 2-3 jours |
| Rate limit HTTP par org | nginx ingress annotations | 2h |
| MySQL sous forte charge | ProxySQL | Demi-journée |

---

## Architecture cible

```
Ingress nginx
  └── rate limit par header X-Workspace-Id (10 rps/org)
        │
        ├── ragflow-api (Deployment, 2-4 replicas, HPA sur RPS)
        │     └── routeur org → Infinity instance  [à implémenter]
        │
        ├── task-executor-standard (2 replicas)
        │     └── Redis stream : svr_queue
        │
        ├── task-executor-premium (4 replicas)
        │     └── Redis stream : svr_queue_1
        │
        ├── LiteLLM Proxy
        │     ├── rate limit / budget par org
        │     ├── vllm-standard  (1× Blackwell 96GB)
        │     └── vllm-premium   (2× Blackwell, tensor parallel)
        │
        ├── Infinity-premium  (StatefulSet, PVC NVMe SSD)
        ├── Infinity-standard (StatefulSet, PVC HDD)
        │
        ├── MySQL (RDS ou CloudNativePG)
        └── Redis (ElastiCache ou Redis Cluster)
```

---

## Implémentation multi-Infinity (détail technique)

Aujourd'hui : connexion Infinity singleton global via `settings.INFINITY["uri"]`.

Cible : un registry `org_id → Infinity URI`, injecté via `active_tenant_id()` déjà disponible dans notre layer RBAC.

```python
# À créer dans common/doc_store/
INFINITY_INSTANCES = {
    "org_bodemer": "infinity-premium:23817",
    "default":     "infinity-standard:23817",
}

def get_infinity_conn(org_id: str) -> InfinityConnectionBase:
    uri = INFINITY_INSTANCES.get(org_id, INFINITY_INSTANCES["default"])
    return get_or_create_pool(uri)
```

Fichiers impactés : `infinity_conn_base.py`, `infinity_conn.py`, les services qui instancient `dataStore`.

---

## Plan d'action par priorité

| Priorité | Action | Effort | Quand |
|---|---|---|---|
| P0 | HPA + 2 replicas ragflow-api | 1h | Avant mise en prod |
| P0 | Rate limit nginx ingress sur `X-Workspace-Id` | 2h | Avant mise en prod |
| P0 | vLLM `--max-num-seqs` cap concurrent global | 1h | Avant mise en prod |
| P0 | Validation que le quota custom enforce bien | 1h | Avant mise en prod |
| P2 | LiteLLM Proxy devant vLLM (alternative riche, voir section) | 1-2 jours | Si besoin opérationnel se confirme (multi-backend, virtual keys avancées, failover) |
| P1 | 2 queues task-executor (std/premium) | 1 jour | J+1 mois |
| P1 | 2 instances Infinity + routeur org | 2-3 jours | J+1 mois |
| P2 | ProxySQL connection pooler MySQL | Demi-journée | Si >50 users actifs |
| P3 | Read replicas MySQL (dashboards) | Config RDS | Si analytics lentes |

---

## Ce qui est overkill pour la phase actuelle

- Istio service mesh (trop complexe opérationnellement)
- Vitess / PlanetScale (pour des centaines d'orgs, pas des dizaines)
- Migration ORM async (gain < 0.1% sur la latence totale)
- Autoscaler GPU (cold start inacceptable pour du chat interactif)
- Queue LLM maison dans RAGFlow (LiteLLM Proxy couvre déjà ce besoin)

---

## Notes modèles LLM

**Gemma4 35B MoE / Qwen3 30B MoE sur Blackwell :**
- MoE = activation sparse ~3-7B paramètres par token → très efficace en débit
- ~60-80GB BF16 → tient dans 1-2 cartes Blackwell 96GB
- TTFT attendu avec 4K tokens contexte RAG : < 200ms sur Blackwell (vs 1-3s sur Mac M-series)
- Le débit (tokens/sec) dépend de la bande passante mémoire — Blackwell ~960 GB/s par carte

**Distribution des 4 cartes :**
```
vllm-premium  : 2× Blackwell (tensor parallel=2)  →  orgs Premium/Enterprise
vllm-standard : 1× Blackwell                      →  orgs Standard
spare/overflow: 1× Blackwell                      →  burst ou second standard
```

---

## LiteLLM Proxy — alternative à creuser (pas P0)

> **Statut décisionnel** : pas adopté en P0 parce que notre stack custom
> couvre déjà l'essentiel pour notre contexte souverain on-prem :
> quotas mensuels par org, virtual keys per-workspace dans l'admin panel,
> tracking async des tokens. Le seul vrai gap (RPS real-time) est couvert
> par nginx ingress rate limiting (P0, 2h config). LiteLLM Proxy reste
> documenté ici comme **option à activer plus tard** si :
> - on ouvre du multi-backend LLM (rare vu la stratégie souveraineté)
> - on veut un dashboard ops temps réel des appels LLM (overlap avec Langfuse)
> - on veut des budgets par key au-delà du quota mensuel
> - on a besoin de retry/failover automatique au niveau gateway
>
> Pas adopté ≠ exclu — on le réévalue si l'un des cas ci-dessus émerge.

### Tester en local

```yaml
# docker-compose.litellm.yml
services:
  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    ports:
      - "4000:4000"
    volumes:
      - ./litellm_config.yaml:/app/config.yaml
    command: --config /app/config.yaml --detailed_debug
    environment:
      - LITELLM_MASTER_KEY=sk-admin-local-xxx
      - DATABASE_URL=postgresql://litellm:litellm@litellm-db:5432/litellm

  litellm-db:
    image: postgres:16
    environment:
      POSTGRES_USER: litellm
      POSTGRES_PASSWORD: litellm
      POSTGRES_DB: litellm
```

```yaml
# litellm_config.yaml
model_list:
  - model_name: gemma4-premium
    litellm_params:
      model: openai/gemma4
      api_base: http://host.docker.internal:8000/v1  # vLLM local
      api_key: none

  - model_name: gemma4-standard
    litellm_params:
      model: openai/gemma4
      api_base: http://host.docker.internal:8000/v1
      api_key: none

general_settings:
  master_key: sk-admin-local-xxx
```

Créer une clef par org :

```bash
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer sk-admin-local-xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "key_alias": "bodemer",
    "model": "gemma4-premium",
    "tpm_limit": 50000,
    "metadata": {"org_id": "bodemer"}
  }'
# → retourne sk-bodemer-xxxx
```

Dashboard usage : `http://localhost:4000/ui`

Dans RAGFlow, la config du workspace pointe sur :
```
endpoint : http://localhost:4000/v1
api_key  : sk-bodemer-xxxx
model    : gemma4-premium
```

### Sécurité — version minimale requise : 1.83.0

Trois incidents majeurs découverts en 2026, tous corrigés dans la v1.83.0 :

| Incident | CVE | Versions affectées | Sévérité |
|---|---|---|---|
| Supply chain PyPI — credential stealer | (pas de CVE) | 1.82.7 et 1.82.8 **uniquement** | Critique |
| Privilege escalation / RCE via `/config/update` | CVE-2026-35029 | < 1.83.0 | High (CVSS 8.7) |
| Auth bypass OIDC cache collision | CVE-2026-35030 | < 1.83.0 | Critique (CVSS 9.4) |

**Supply chain (24 mars 2026)** : le groupe "TeamPCP" a compromis le pipeline CI/CD de LiteLLM via Trivy (scanner non pинné en version) et publié deux packages PyPI malveillants. Le payload exfiltrait SSH keys, variables d'environnement, credentials AWS/GCP/K8s, mots de passe DB, vers un domaine attaquant. L'image Docker officielle n'était pas affectée (dépendances pinnées).

**CVE-2026-35029** : le endpoint `/config/update` ne vérifiait pas le rôle `proxy_admin` — n'importe quel utilisateur authentifié pouvait modifier la config, injecter du code Python arbitraire → RCE.

**CVE-2026-35030** : cache OIDC keyed sur les 20 premiers caractères du JWT → collision → auth bypass total. Uniquement si `enable_jwt_auth: true` (off par défaut).

**À faire :**
- Utiliser **≥ 1.83.0** impérativement
- Toujours utiliser l'image Docker officielle (pas `pip install` direct en prod)
- Ne jamais pinner `latest` — utiliser un tag de version explicite
- Si `enable_jwt_auth: true`, désactiver le cache OIDC ou mettre TTL à 0

---

### Base de données LiteLLM — ne pas partager avec MySQL RAGFlow

LiteLLM supporte MySQL techniquement, mais il faut une base séparée.

**Pourquoi pas la même MySQL que RAGFlow :**
1. RAGFlow fait des migrations de schéma automatiques au démarrage — un lock de table impacte LiteLLM simultanément
2. Si MySQL est surchargé (gros upload, analytics), LiteLLM ne peut plus vérifier les budgets → toutes les requêtes LLM échouent
3. LiteLLM utilise SQLAlchemy + Alembic — faire cohabiter deux ORMs avec leurs migrations dans la même DB est une source de conflits futurs

**En local :** Postgres dans le docker-compose ci-dessus — léger, zéro config.

**En prod Kubernetes :** instance RDS/CloudNativePG dédiée, petite (db.t3.small suffit, LiteLLM n'écrit pas beaucoup).

---

## Références

- [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/quick_start) — rate limiting, virtual keys, budget par org
- [Infinity StatefulSet](https://github.com/infiniflow/infinity) — moteur vectoriel léger, défaut RAGFlow
- [vLLM priority scheduling](https://docs.vllm.ai/en/latest/serving/scheduling_policy.html) — expérimental, FIFO par défaut
- Code routage Infinity : `common/doc_store/infinity_conn_base.py`, `rag/utils/infinity_conn.py`
- Layer RBAC custom : `api/apps/extensions/rbac.py`, `api/utils/tenant_context.py`
