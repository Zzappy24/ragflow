# Roadmap — RAGFlow multi-tenant B2B SaaS (Cyllene)

Items identifiés comme game-changers pour le produit Cyllene au-delà
de la démo PDG. Triés en deux catégories : déjà présents dans RAGFlow
mais sous-utilisés, vs. à construire nous-mêmes.

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

**Stack recommandée** :

| Couche | Outil | Pour quoi |
|---|---|---|
| LLM observability | **Langfuse** (open-source, self-host) | Tokens, latence, traces, audit RGPD |
| Product analytics | PostHog (free tier) | DAU/WAU, funnel, top queries, feature adoption |
| Billing & SLA | Custom (déjà fait pour tokens) | Vue client dans le panel admin |
| Infra | Datadog/Grafana existants | ASGI, MySQL, Redis |

**Surprise : Langfuse est déjà câblé dans RAGFlow** —
[api/db/services/tenant_llm_service.py:491-502](api/db/services/tenant_llm_service.py#L491-L502)
gère per-tenant les credentials Langfuse. Activer = configurer une instance
Langfuse + renseigner les clés par workspace via `TenantLangfuseService`.
Pas besoin de réécrire l'observability LLM, juste de l'allumer.

Custom reste nécessaire pour la **vue client-facing** (factures B2B,
SLA reporting), mais Langfuse couvre 80% du gap interne.

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
