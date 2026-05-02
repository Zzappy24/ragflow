# Roadmap — RAGFlow multi-tenant B2B SaaS (Cyllene)

Items identifiés comme game-changers pour le produit Cyllene au-delà
de la démo PDG. Triés en deux catégories : déjà présents dans RAGFlow
mais sous-utilisés, vs. à construire nous-mêmes.

---

## 🌟 Priorité transversale — GraphRAG sur tous les datasets de docs

**Foundation, pas une phase.** À activer dès la création de chaque dataset
documentaire (RFCs, ARCHITECTURE.md, ADRs, procédures, conventions).
RAGFlow l'a built-in (`rag/graphrag/search.py`) mais cassé chez nous par
la config par défaut (`localhost:6380` embedding inexistant).

Bénéfices :
- Cross-project pattern discovery (« quels projets utilisent JWT + Postgres ? »)
- Asset cumulatif : chaque doc ingéré enrichit le graph d'entités
- Coût marginal : 2-3× temps d'ingestion mais pas de runtime overhead majeur
- Réutilisable au-delà du code : pour les KBs métier, juridiques, etc.

Setup (~1 jour) :
1. Configurer un LLM extraction d'entités (Qwen3:4b local OK pour démarrer,
   DeepSeek V4 Flash sur Blackwell en prod)
2. Activer `use_kg=true` au niveau dataset (entity extraction à l'ingestion)
3. Ré-ingestion progressive des docs existants
4. Activer `use_kg=true` dans les nœuds Retrieval des canvases qui en bénéficient

À ne PAS appliquer au code source — pour ça, FFF MCP (grep) + Read couvrent
mieux. GraphRAG est pour les **artefacts business**.

---

## 🚀 Roadmap MCP server — V1 à V5

Le serveur MCP RAGFlow upstream existe (`mcp/server/server.py`) et marche
out-of-the-box avec notre auth multi-tenant après les fixes RBAC du
2026-05-02. La roadmap ci-dessous étend ses tools.

### Scope retenu (skip V3 codebase graph)

| Phase | Items | Effort |
|---|---|---|
| **V0 — Acquis** | `ragflow_retrieval` (upstream) + auth multi-tenant + RBAC per-user via API key | ✅ Done |
| **V1 — Exploration richer** | `list_datasets` séparé, `list_documents`, `get_document` | 3 jours |
| **V2 — Write basique** | `index_document`, `index_url`, `delete_document`, `update_document`, `create_dataset` | 1-2 sem |
| **V3 — Codebase graph** | DEFERRED — wrappe code-graph-mcp/graphify quand client demande | T+6-12 mois |
| **V4 — Agents-as-tools** | `run_agent`, `list_agents`, `continue_agent_session` | 1-2 sem |
| **V5 — Prompts catalog** | MCP `prompts/list`, `prompts/get` | 1 sem |
| **Bonus** | UI per-user API keys + Service Accounts + Audit dashboard | 1-2 sem |
| **Total** | | **~6-8 semaines** |

### Ordre d'attaque (facile → impact → différenciant)

```
1. Commit RBAC fixes (déjà appliqués 2026-05-02)
   ↓
2. V1 (exploration)         [3 jours, pattern multi-tools établi]
   ↓
3. V4 (run_agent)           [1-2 sem, KILLER FEATURE — workforce IA via IDE]
   ↓
4. V2 partiel (index_document) [1 sem, write IDE-friendly]
   ↓
5. V5 (Prompts catalog)     [1 sem, méthodo Cyllene packagée]
   ↓
6. V2 reste                 [3 jours, completion]
   ↓
7. UI per-user API keys     [1-2 sem, scale-up]
```

### Points d'attention opérationnelle (à intégrer durant l'implémentation)

| # | Item | Criticité | Quand |
|---|---|:---:|---|
| 1 | Cancellation + streaming progress sur `run_agent` | 🔴 Élevée | À spec **avant V4** |
| 2 | Schema validation JSON Schema sur tous les tools (avant exécution) | 🟠 Moyenne | Pattern dès V1 |
| 3 | Doc client / SDK config (Claude Code, Cursor, OpenCode) | 🔴 Élevée | Avant 1er client externe |
| 4 | Migration script tokens legacy sans `ApiKeyScope` | 🟠 Moyenne | Avant prod |
| 5 | HPA MCP server pour multi-replicas K8s | 🟠 Moyenne | Avant prod scale |
| 6 | Versioning de l'API MCP | 🟢 Basse | T+6 mois |
| 7 | Test que `LLMBundle._check_token_quota` enforce bien sur calls MCP | 🔴 Élevée | Avant 1er client payant |
| 8 | Cancellation propagée au `canvas.run()` côté serveur | 🟠 Moyenne | T+3 mois |
| 9 | GDPR delete pipeline (suppression chunks Infinity quand `delete_document`) | 🔴 Élevée | Avant client régulé (Bodemer/Maurin) |
| 10 | Healthchecks `/health` + readiness/liveness probes K8s | 🟢 Basse | Avant deploy K8s |

→ Total +11 jours additionnels sur les 6-8 semaines de roadmap MCP. Pas de
quoi changer la structure, juste à ne pas oublier.

### V3 codebase graph — pourquoi deferred

FFF (file-finder MCP avec grep frecency-ranked) couvre l'usage dev quotidien.
À 200K+ LOC du fork RAGFlow, grep + Read + multi_grep sémantique reste
performant. Vector search sur le code ajoute marginalement.

Reactivable plus tard via :
- **Wrapper code-graph-mcp** : 10 tools out-of-the-box (call_graph, impact_analysis,
  find_dead_code, etc.), tree-sitter + SQLite + sqlite-vec, ~1 sem pour ajouter
  notre auth multi-tenant
- **Wrapper graphify** : multimodal (code + docs + images + vidéos), NetworkX
  + Leiden, plus exotique mais riche

Conditions de réactivation :
- Client demande explicitement code search avancé sur SES repos
- Onboarding interne explose (>10 nouveaux devs / an sur le fork)
- Cyllene packe une offre « code-aware AI » premium

---

## 🎯 Présents dans RAGFlow, à activer

### 1. MCP (Model Context Protocol) comme tools agents
RAGFlow peut consommer des serveurs MCP comme tools côté LLM. Permet de
brancher l'agent sur le SI client (SIRH, ITSM, Okta, JIRA, monitoring,
APIs internes) sans rien recoder côté Cyllene. Game-changer du pitch B2B :
on passe de « générateur de docs » à « workforce IA connectée au SI ».

### 2. Categorize node + multi-agents spécialisés
Routeur LLM en frontal qui dispatche vers des agents spécialisés (RH,
sécurité, juridique, finance). Un seul point d'entrée par workspace.
Chaque agent a sa propre KB, ses propres tools, son propre sys_prompt.
Voir détail dans la section Architecture en fin de doc.

### 3. Webhook agents + Cron
Composants présents, jamais utilisés. Cas Cyllene :
- HR system crée un collab → webhook → procédure générée + envoyée pour
  relecture
- Cron mensuel → revue RBAC du workspace → mail synthèse aux admins
- Cron trimestriel → audit fraîcheur KB → ticket si docs périmés
- Cron quotidien → backup KB versionné

Effet produit : passe d'« outil utilisé » à « automate qui tourne ».

### 4. GraphRAG (au lieu de retrieval vectoriel simple)
Pour des KB procédurales avec acteurs qui se référencent (RSSI, DPO,
officier sécurité), un graphe d'entités donne des chunks plus cohérents.
Activé dans la config par défaut mais cassé chez nous (le default
embedding pointe sur un endpoint inexistant `localhost:6380`). À fixer
+ à activer sur workspaces clients régulés.

### 5. Begin — formulaire structuré
Pour les cas d'usage répétitifs (procédure d'embauche, brief mission,
habilitation), un formulaire Begin avec champs typés (dropdown rôle,
période, périmètre) > texte libre. Sortie ultra-déterministe, pas
d'ambiguïté pour le modèle.

---

## ❌ À construire — manquants critiques pour scaler

### 6. Analytics d'usage par workspace (au-delà des tokens)
Tokens p/workspace existe déjà. Manque :
- Latence p50/p95 par agent
- Cost attribution par agent (pas seulement par workspace)
- Top queries / top failures
- Tool call success rate
- KB retrieval quality (top chunks, taux empty_response)
- User satisfaction (thumbs feedback)
- Trend hebdo/mensuel/trimestriel
- Token cost vs business value (« 240 h économisées »)

**Approche tiered, par ordre d'effort** :

**Tier 1 — 1-2 jours, zéro infra ajoutée** (recommandé pour stade actuel)
Table `llm_call_log` dans le MySQL existant : `tenant_id`, `canvas_id`,
`agent_id`, `model`, `started_at`, `ended_at`, `prompt_tokens`,
`completion_tokens`, `success`, `error`. Wrap les appels LLM dans
`LLMBundle` pour écrire une ligne. Vues admin via SQL (latence p95,
top failures, top queries, cost par agent). **Couvre 80% du besoin
réel** : pas de nouvelle stack à maintenir.

**Tier 2 — 1 semaine, si Datadog/Grafana déjà en place**
SDK OpenTelemetry dans `LLMBundle` → traces + métriques dans l'obs infra
existante. Pas de stack séparée, dashboards partagés avec les SRE.

**Tier 3 — 2 semaines, +Langfuse self-host**
Justifié quand on a vraiment besoin de :
- Visualisation traces multi-step parent/child (agent → tool → retrieval)
- Workflow d'annotation/feedback collaboratif client-side
- Volumes >10M traces/mois (ClickHouse pertinent à ce volume)

À noter : RAGFlow câble déjà Langfuse per-tenant nativement
([api/db/services/tenant_llm_service.py:491-502](api/db/services/tenant_llm_service.py#L491-L502)),
donc le jour où Tier 3 devient justifié, l'intégration côté code = 0.
Reste à monter la stack Langfuse (Postgres + ClickHouse + Redis + Node API
+ Worker + Web). **Lourd** : à ne lancer qu'avec ≥5 clients en prod et un
besoin documenté qui dépasse Tier 1/2.

**Pré-requis spécifiques à notre fork multi-tenant** (à vérifier AVANT d'allumer Langfuse) :

1. **Audit UI de configuration Langfuse** — grep `langfuse` dans
   `web/src/pages/user-setting/`. Vérifier que la page de config Langfuse
   utilise `active_tenant_id()` (workspace) et pas `user_id`, et qu'elle
   est gardée par une permission accessible aux ws_admin (typiquement
   `LLM_CONFIGURE` ou équivalent). ~1 jour d'audit + correctifs.

2. **Modèle déploiement : 1 projet Langfuse par workspace**
   ```
   Instance Langfuse (self-host on-prem)
      ├── Projet "Cyllene-Workspace-Bodemer"
      ├── Projet "Cyllene-Workspace-Maurin"
      └── Projet "Cyllene-Workspace-Demo"
   ```
   Chaque `ws_admin` ne voit que son projet — isolation native Langfuse.
   Création des projets via Langfuse API au moment de la création du
   workspace côté Cyllene.

3. **Fallback `_fallback_personal_tenant_id` ne doit PAS s'appliquer
   à Langfuse** — sinon une workspace sans config Langfuse leak ses traces
   dans le projet personnel du créateur du workspace. À tester
   explicitement : workspace sans `tenant_langfuse` row → aucun appel
   Langfuse, pas un fallback silencieux.

4. **Champs `userId` et `sessionId` dans les traces Langfuse** — vérifier
   que RAGFlow pousse :
   - `userId` = vrai user ragflow qui a déclenché (pas le tenant_id)
   - `sessionId` = canvas session_id (pour grouper la conversation)

   Sinon les dashboards Langfuse vont confondre tous les users d'un
   workspace en un seul. ~10 lignes à ajouter dans `LLMBundle` si manquant.

**Custom reste nécessaire** pour la vue client-facing (factures B2B,
SLA reporting affiché aux clients dans leur panel admin). Pas remplaçable
par un outil tiers parce que c'est partie intégrante de ton produit.

### 7. Audit trail RGPD
Qui, quand, quel agent, quelle query, quels chunks retrieved, quel
output. Obligation légale + différenciant en B2B régulé.

### 8. Schema validation à la frontière des tools
Valider le JSON arg contre un JSON Schema explicite avant l'exécution
du tool. Couvre TOUS les tools, pas juste `render_docx_template`.
Erreurs claires > regex défensives accumulées dans `_ensure_dict`.

### 9. Permissions par canvas
Aujourd'hui tout `ws_admin` édite tout canvas du workspace. À 50+
agents par workspace, granularité nécessaire (privé / lecture seule /
collaboratif).

### 10. CI / regression tests pour agents
Lancer une fixture (query + context + KB version pinned) et asserter
sur les outputs. Sans ça, un upgrade de modèle peut dégrader un cas
d'usage en silence.

---

## 11. RAGFlow MCP server — exposer la plateforme aux IDE IA

Probablement **le plus gros levier de croissance** T+6 mois. Tu construis
un serveur MCP (Model Context Protocol) qui enveloppe l'API REST RAGFlow,
et n'importe quel client MCP-compatible (Claude Code, Cursor, OpenCode,
Kilo Code, Continue, Cline, Aider…) peut consommer tes KBs et agents
en 30 secondes côté client.

**Tools à exposer (v1)** :
```
search_kb(workspace_id, kb_id, query)     → chunks
run_agent(workspace_id, canvas_id, query) → result
list_kbs(workspace_id)                    → [...]
list_agents(workspace_id)                 → [...]
get_document(workspace_id, doc_id)        → content
chat_with_agent(ws, canvas, session, msg) → streamed
```

**Cas d'usage qui changent le pitch** :
1. **Devs Cyllene branchent leur IDE IA sur la KB interne** — productivité
   des 200+ devs internes. *« Hey Claude, comment on rédige une procédure
   habilitation S3 ? »* → ton MCP → réponse fact-based depuis la KB.
2. **Clients B2B intègrent vos agents en 30 secondes** — leurs devs ajoutent
   ton serveur MCP dans leur IDE, accèdent à tes agents compliance/RH/sécu
   sans coder un client.
3. **Workforce automation cross-product** — un bot Slack client appelle ton
   agent S3 via MCP. Tu deviens commodité IA dans leur stack.

**MCP vs REST** :
| | REST API existante | MCP server (à ajouter) |
|---|---|---|
| Découverte | Doc + lecture | Auto-introspection par le client |
| Auth | API key custom | OAuth/token standard MCP |
| Streaming | SSE custom | Natif |
| Compat IDE IA | 0 (chacun écrit son client) | Tous out-of-the-box |
| Friction côté client | 1-3 jours | 30 secondes |

Tu **ne remplaces pas** REST, tu l'**enveloppes** (~500-1000 lignes Python
avec le SDK FastMCP). Auth via les API keys workspace existantes.

**Stratégique** :
- Tu deviens « AI infrastructure provider », pas juste « SaaS B2B »
- Aucun concurrent n'expose son RAG en MCP aujourd'hui sur la verticale
  Cyllene → position de référence à prendre
- Effort v1 : ~1-2 semaines

---

## Architecture proposée — multi-agents avec Categorize

```
Begin (form: type_demande, périmètre)
  ↓
Categorize (LLM léger — Qwen3-4B)
  ├──→ Agent_RH         + KB-RH      + tools_RH (SIRH, paie)
  ├──→ Agent_Sécurité   + KB-sécu    + tools_sécu (SIEM, IAM)
  ├──→ Agent_Juridique  + KB-jur     + tools_jur (signature)
  ├──→ Agent_IT_Ops     + KB-it      + tools_it (ITSM, monitoring)
  └──→ Agent_Default    (fallback poli si hors scope)
  ↓
Message (avec mention de l'agent qui a répondu)
```

Bonnes pratiques :
- 5-8 catégories par workspace, mutuellement exclusives + exhaustives
- Catégorize utilise un modèle petit/cheap (Qwen3-4B suffit)
- Conversation history propagée entre catégories (user peut switcher
  RH → sécurité mid-chat)
- Le tag de l'agent qui a répondu est exposé dans la réponse
  (transparence + audit)
- Fallback explicite : « Désolé, je ne peux pas t'aider sur ce sujet »
  plutôt que d'halluciner

Pièges :
- Catégorisation trop coarse → agent générique, sortie diluée
- Catégorisation trop fine → maintenance de 20 prompts différents
- Pas de fallback → l'agent default tente des sujets hors scope
