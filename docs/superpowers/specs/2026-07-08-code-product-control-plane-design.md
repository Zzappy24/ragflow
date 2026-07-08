# Design — Produit « Code » : control plane unifié (RAG + Code)

- **Date** : 2026-07-08
- **Statut** : validé (brainstorming), prêt pour plan d'implémentation
- **Périmètre** : ajouter la gestion du 2ᵉ produit « Code » (vLLM H200 + gateway LiteLLM) au panel management existant, sans toucher au produit RAG.

---

## 1. Contexte & objectif

Cyllene opère un produit RAG (fork RAGFlow multi-tenant B2B). On ajoute un 2ᵉ produit **Code** : un vLLM dédié sur H200 (séparé du vLLM Blackwell du RAG), consommé par des outils tiers (OpenCode, Kilo Code, Cline…) via une API OpenAI-compatible.

Les deux produits sont **vendus à la carte au même client** (l'un, l'autre, ou les deux — activables par toggle). Il faut donc un **control plane unifié** (identité client, orgs, entitlements, facturation, audit) tout en gardant les **plans data/modèle physiquement séparés** (souveraineté).

### Décision de fond
- **NE PAS forker LiteLLM** (vélocité upstream + code org sous licence commerciale + ne débarrasse pas du SDK).
- **NE PAS router le RAG via LiteLLM** (le RAG a déjà son gateway applicatif ; hot path embedding protégé).
- **Utiliser LiteLLM OSS (MIT) en data plane headless** devant le vLLM H200. Piloté uniquement par son API REST de management. Son UI (`/ui`) désactivée.
- **L'org vit dans notre panel**, pas dans LiteLLM. LiteLLM ne détient que Teams + Keys (primitives plates gratuites).

### Vérification licence (2026-07-08)
Confirmé OSS/MIT, enforcement temps réel : budgets **par key, par team, par internal-user, par team-member** + spend tracking. Enterprise (non utilisés) : budget par-modèle-sur-key, budget tiers, SSO>5 users, RBAC, audit logs. L'**org-budget natif est ambigu** (UI le dit enterprise, API le laisse créer, jamais clarifié — issue #12727) → **on ne s'appuie pas dessus**. Sources : docs.litellm.ai/docs/proxy/users · github.com/BerriAI/litellm/issues/12727.

---

## 2. Topologie — séparé au data plane, unifié au control plane

```
CONTROL PLANE (unifié)                DATA PLANE code (séparé, headless)
─────────────────────────            ──────────────────────────────────
Panel management/ (existant)          LiteLLM OSS proxy ──► vLLM H200
 back: management/server/       ──API──►  /team, /key, /spend  (MASTER_KEY)
 front: management/web/                   /ui  ❌ désactivé
 + nouveau domaine "Code"                 /v1/* ◄── OpenCode/Kilo (virtual key)
 = source de vérité                       + Postgres LiteLLM (propre)

RAG (INCHANGÉ) : RAGFlow app ──► vLLM Blackwell · MariaDB · Infinity
```

**Nouveaux composants infra** : 1 pod LiteLLM + 1 Postgres (pod + PVC), self-hostés, télémétrie off, version LiteLLM pinnée. À placer **hors du nœud GPU saturé** (cf. incident scheduling 2026-07 : api/executor/infinity déjà tous épinglés sur `alteraiworkergpt1`).

La seule surface LiteLLM qu'un humain/outil client touche = l'endpoint `/v1/*` (une URL d'API). Toute UI = notre panel brandé.

**Secret `MASTER_KEY`** : stocké en K8s Secret, monté uniquement dans le backend du panel (server-to-server). Jamais exposé au front, jamais loggé, rotation documentée dans le runbook infra.

---

## 3. Data-model (MariaDB, dans le panel management)

Tables neuves, accrochées à l'`org` existante.

| Table | Champs clés | Rôle |
|---|---|---|
| `code_entitlement` | `org_id`, `status` (active/suspended), **`org_code_budget`**, `budget_period`, timestamps | « code activé + budget total de l'org ». **Piloté Cyllene** (levier commercial). |
| `code_team` | `id`, `org_id` (FK), `name`, **`litellm_team_id`**, `max_budget`, `budget_duration`, `model_access`, `status`, timestamps | une squad = une Team LiteLLM |
| `code_team_member` | id, code_team_id (FK), user_id (FK), role | délégation « code-team admin » (§4) |
| `code_key` | `id`, `code_team_id` (FK), `label`, **`litellm_key_id`**, `key_masked`, `owner_user_id?`, `status`, timestamps | un siège/dev = une key |

- **Réutilise** l'existant : `org`, membres, rôles RBAC. Aucune nouvelle notion d'identité.
- **Plaintext de key jamais stocké** (affiché 1× à la création ; on garde `key_masked`).
- Colonnes `litellm_*` = binding vers le data plane, **même pattern que `sync_tenant_model_tables`**.
- Charge MariaDB négligeable : lignes low-cardinality, écritures uniquement sur action admin. Le spend haute fréquence vit dans le **Postgres LiteLLM**, pas MariaDB.

### Invariant d'allocation (= la gestion du budget org)
```
Σ(code_team.max_budget WHERE org_id = X)  ≤  code_entitlement.org_code_budget
```
Validé par le panel à chaque création/màj de team. Conséquence : l'org ne peut jamais dépasser la somme de ses teams, elle-même plafonnée à l'allocation. **Pas de cap-org runtime nécessaire, zéro code custom d'enforcement.** L'enforcement runtime est fait nativement par LiteLLM au niveau team (temps réel). La vue org (« 650€/1000€ ») = agrégation en **lecture** (`GET /spend`), hors hot path.

**Alignement des cycles (requis pour que l'invariant soit sain)** : `code_team.budget_duration` n'est **pas** librement choisi — le panel l'impose **uniformément** égal à `code_entitlement.budget_period` (le cycle de facturation de l'org). Sans cet alignement, sommer des budgets de périodes différentes rendrait l'invariant incohérent.

---

## 4. RBAC — Modèle 1 (délégation à deux niveaux)

Permissions ajoutées au système de rôles existant : `code:team:manage`, `code:key:manage`, `code:view`.

| Rôle | Capacités |
|---|---|
| **Cyllene super-admin** | toggle entitlement + fixe `org_code_budget` (le « combien acheté ») |
| **Org admin** (client) | `code:*` sur toutes les teams de son org — répartit le budget (le « comment divisé ») |
| **Code-team admin** (délégué) | `code:key:manage` + `code:view` sur sa/ses team(s) uniquement |

Séparation nette : **Cyllene contrôle le combien, le client contrôle le comment.** Un client ne peut jamais s'auto-augmenter son budget total. Le `ws admin` du RAG reste inchangé (branche parallèle).

---

## 5. Flux de provisioning — panel ↔ LiteLLM (idempotent + réconciliation)

| Action panel | Appel LiteLLM | Effet |
|---|---|---|
| Cyllene active le code | *(aucun)* | crée `code_entitlement`, pose `org_code_budget` |
| Org admin crée une code-team | `POST /team/new` | valide invariant → stocke `litellm_team_id` |
| Ajoute un siège | `POST /key/generate` | stocke `litellm_key_id` + masque ; plaintext 1× |
| Modifie un budget | `POST /team/update` | re-valide invariant |
| Révoque un siège | `POST /key/block` | **ne supprime pas** → garde historique spend |
| Cyllene désactive l'entitlement org | `POST /key/block` sur **toutes les keys de toutes les teams** de l'org (fan-out) | `code_entitlement.status = suspended` ; teams/keys conservées → réactivation = unblock, instantanée |
| Vue usage | `GET /team/info`, `/spend` | agrégation en lecture |

**Garde-fous sync burden :**
1. **Idempotence par clé externe déterministe** — sur les **deux** objets : alias team = `org:{org_id}:team:{code_team_id}`, et `key_alias` = `org:{org_id}:key:{code_key_id}` → un retry (team **ou** key) ne duplique jamais.
2. **Desired-state-first (ordre des écritures)** : toute mutation écrit **d'abord l'intention dans MariaDB** (`status = pending_create | pending_revoke | …`, transactionnel), **puis** tente l'appel LiteLLM en synchrone. Succès → statut final (`active`/`revoked`). Échec (LiteLLM down) → la ligne reste `pending_*`, l'UI affiche « en cours », et le réconciliateur converge. La colonne de statut **est** la file d'attente — pas de broker asynchrone à ajouter.
3. **Job de réconciliation périodique** : retente tout ce qui est `pending_*` (backoff ; **révocations en priorité** — sécuritaire) et compare panel vs état LiteLLM pour réparer la dérive (y compris un fan-out de suspension interrompu à mi-course). Réponse directe au piège `legacy_id` déjà vécu.

Nuance sur la fenêtre de risque « LiteLLM down pendant une révocation » : l'API de management et `/v1/*` sont servis par le **même process** — si le management est down, le data plane l'est très probablement aussi, donc la key à révoquer est inutilisable pendant la panne. Le cas résiduel (partition réseau panel→proxy avec clients atteignant encore le proxy) est couvert par le retry prioritaire du réconciliateur.

---

## 6. Front (`management/web`) — section « Code »

Réutilise composants existants (tables, forms, auth, layout, i18n).

| Vue | Contenu | Gate |
|---|---|---|
| Code — overview (org) | statut entitlement, budget, alloué/non-alloué, spend org | `code:view` |
| Code-teams (liste + CRUD) | nom, budget, model-access ; barre « alloué X / total Y » | `code:team:manage` |
| Sièges/keys (par team) | créer (key affichée 1×), révoquer, usage/siège | `code:key:manage` |
| Panneau Cyllene | toggle entitlement + setter `org_code_budget` | super-admin only |

Consommation client (MVP) : **gestion déléguée dès le départ** via RBAC (org admin + code-team admin). Le portail read-only pur / self-serve avancé s'ajoutent par-dessus le **même data-model** plus tard (ajout de vues, pas de migration).

---

## 7. Coûts & risques assumés

| Coût | Mitigation |
|---|---|
| Nouveau Postgres + pod LiteLLM (infra, backup, scope souveraineté) | self-hosté, télémétrie off, hors nœud GPU saturé |
| Sync panel ↔ LiteLLM | idempotence clé externe + job de réconciliation (§5) |
| Cap-org = invariant d'allocation, pas de pool partagé | documenté ; hook Redis différé (data-model inchangé si ajouté) |
| Couplage API REST LiteLLM entre versions | léger (API stable) ; version pinnée |
| Dépendance enterprise | **aucune** — vérifié (§1) |

### Différé explicitement (YAGNI)
- **Hook Redis pre-call** pour cap-org temps réel : nécessaire seulement si (a) pool partagé dynamique entre squads, ou (b) overcommit. Ni l'un ni l'autre au départ. Ajout sans changer le data-model.
- **Portail client self-serve** (création/révocation par le client) au-delà de la délégation RBAC.
- **Ledger unifié RAG+code** : agrégation facturation cross-produit (aggregat grossier depuis Postgres LiteLLM + usage RAG). Pas requis pour livrer le code.

---

## 8. Stratégie de test (patterns `test/multitenant`)

- **Unit** : invariant `Σ teams ≤ org_budget` (accept/reject) · checks RBAC (org admin vs code-team admin vs membre) · client de provisioning avec LiteLLM mocké.
- **Intégration** : panel → LiteLLM stubbé (team/key create, block, spend read) · job de réconciliation répare une dérive injectée.
- **E2E** : activer code → créer team → créer key → appeler vLLM via LiteLLM avec la key → spend remonte → dépasser budget team → requête **bloquée temps réel**.

---

## 9. Journal des décisions

1. LiteLLM OSS headless en data plane, **pas de fork**, **pas de RAG via LiteLLM**.
2. Org dans notre panel ; LiteLLM ne détient que Teams+Keys gratuites.
3. Mapping **Option B** : code-teams = branche parallèle sous l'org (≠ workspaces RAG).
4. RBAC **Modèle 1** : délégation org-admin + code-team-admin.
5. Budget org = **invariant d'allocation** (per-squad quota), enforcement runtime natif LiteLLM par team. Pas de pool partagé au départ.
6. Zéro dépendance enterprise (vérifié).
