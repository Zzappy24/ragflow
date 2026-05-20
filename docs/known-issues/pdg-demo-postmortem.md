# Post-mortem — Démo PDG « Procédure embauche fiche S3 »

Issues rencontrées pendant la mise en place de la démo agentic sur Cyllene-fork
RAGFlow (mai 2026). Document maintenu pour éviter de retomber dans les mêmes
trous.

---

## 🔴 Critique — friction maximale

### 1. Caches multi-niveaux invisibles

Trois caches stockent l'état runtime du canvas ; aucun n'est visible
côté admin :

| Cache | Où | TTL | Symptôme quand stale |
|---|---|---|---|
| `canvas:replica:{canvas_id}:{tenant}:{user}` | Redis db 1 | 3 h | DSL édité en SQL ne s'applique pas — runtime continue d'utiliser l'ancienne version |
| `_MODEL_CONFIG_CACHE` | mémoire process `ragflow_server` | 5 min | Changements `is_tools` / `api_base` / suppression de modèle ignorés |
| TenantLLM lookup interne | dépend du flux | variable | Non documenté |

**Statut** : ✅ **Résolu via auto-hash fingerprint** (commit `feat(canvas): auto-invalidate Redis replica…`).
Le runtime hash le DSL au build du replica, et au `load_for_run` compare avec
le DSL canonique en MySQL ; mismatch = drop + re-bootstrap automatique. Aucune
action utilisateur requise.

Le cache `_MODEL_CONFIG_CACHE` (TenantLLM) est invalidé par le code à chaque
update via `_invalidate_model_config_cache(tenant_id)`. Si jamais un cas reste
coincé, escape hatch ops :
```python
from api.apps.services.canvas_replica_service import CanvasReplicaService
CanvasReplicaService.invalidate(canvas_id, tenant_id, user_id)  # per-user
```

---

### 2. `is_tools=False` par défaut sur tout nouveau modèle

Quand l'admin ajoute un modèle via le modal `Add LLM` (factories locales —
Ollama, OpenAI-API-Compatible, VLLM, etc.), la ligne insérée dans la table
`llm` a `is_tools=0`. Conséquence :

- L'agent qui a un tool attaché ne reçoit jamais le param `tools` côté API.
- Le LLM répond en texte (parfois en mettant le JSON tool dans la réponse).
- Aucune erreur visible à l'utilisateur — juste un warning serveur :
  `Model X does not support tool call, but you have assigned one or more tools to it!`

**Statut** : ✅ **Résolu via toggle UI explicite** (commit `feat(admin): supports_tool_calling toggle…`).
Le modal admin « Add LLM » a un toggle **« Supports function calling »**
(default OFF, conformément au comportement upstream — l'admin opt-in
explicitement). Quand activé, l'endpoint `add_llm` insère les deux variantes
du nom (`bare` + `___OpenAI-API`) avec `is_tools=1` dans la table `llm`, donc
la résolution `get_model_config_by_type_and_name` les trouvera quel que soit
le suffixe utilisé.

Si tu omets de cocher la case et que tu vois `Model X does not support tool
call` dans les logs, retourne dans le modal Edit du modèle, coche la case,
sauvegarde.

---

## 🟠 Sérieux

### 3. Frontend re-écrit `llm_id` au save → casse les factories custom

Quand l'utilisateur sélectionne un modèle dans le dropdown de l'Agent, le
frontend regénère `llm_id` à partir du `model_name` + factory du modèle
choisi. Si `llm_id` pointait sur une factory custom (`@Ollama-Hermes`,
`@MyVLLM`, etc.), la sélection écrase silencieusement vers
`@OpenAI-API-Compatible`.

**À faire** : dans `LlmDropdown` du canvas, préserver la `factory_hint`
quand elle correspond à une factory custom enregistrée (`Ollama-Hermes`,
etc.), au lieu de la regénérer.

### 4. Citations cliquables → `Permission denied: document.read`

Cliquer sur la pill `fiche_s3_sec…nnees.md` dans la réponse d'un agent
RAG ouvre une route document_api qui retourne 403 même pour le créateur
de la KB. Le File Manager (`/api/v1/files/<id>`) marche. La route citation
manque probablement `@add_tenant_id_to_kwargs`.

**À faire** : audit de toutes les routes `api/apps/restful_apis/document_api.py`,
ajouter `@add_tenant_id_to_kwargs` sur celles qui appellent
`@require_permission(Permission.DOCUMENT_READ)`.

### 5. `cite: true` par défaut pollue les sortie de tool-call

`cite=true` injecte ~2 KB de prompt système (citation rules + 6 exemples
type smartphone/quantum/Tesla). Pour un agent qui doit produire un
tool_call structuré JSON, c'est néfaste :
- Le modèle apprend à coller `[ID:0]`, `[ID:1]` partout, y compris dans
  les arguments JSON du tool.
- Le contexte est saturé, le modèle a moins d'attention pour le métier.
- Le toggle est planqué dans `Advanced Settings` du formulaire Agent.

**Statut** : ✋ **Pas de change automatique**, parce que (a) certains agents
chat-RAG sans tools légitimes veulent des citations, et (b) skip auto = magie
cachée qu'on regrettera au moment où l'utilisateur veut explicitement les
citations malgré ses tools. Donc :

- Si ton agent a des tools attachés ET tu vois `[ID:X]` dans la sortie,
  toggle **Cite OFF** dans `Agent → Advanced Settings`. Manuel mais
  prévisible.
- Le toggle reste à l'endroit où l'utilisateur s'attend à le trouver
  (Advanced Settings du nœud Agent, sous le tooltip « cite »).

---

## 🟡 Moyen

### 6. Schéma `content: string` (JSON-in-JSON-string)

`render_docx_template` prend un seul argument `content` qui est un objet
JSON sérialisé en string. Les modèles 4B/7B se gourrent royalement sur
le double-escaping (mélange `\"key\"` + `"key"` dans la même string).

**À faire** : refactor `RenderDocxTemplateParam.meta.parameters` en
propriétés à plat (`procedure_name: string`, `beneficiaires: array`,
`finalite: string`, `sections: array`). Le tool n'est pas encore en
prod ailleurs, pas de rétro-compat à préserver.

### 7. `dsl.variables` (UI) vs `dsl.globals['env.X']` (runtime)

Le panel « Conversation variable » affiche/édite `dsl.variables`. Le
runtime canvas résout `{env.X}` contre `dsl.globals['env.X']`. Le
frontend reconstruit `globals` depuis `variables` au save — mais un
changement SQL direct casse la sync (le runtime utilise l'ancien
state des globals).

**À faire** : que le runtime canvas (`Canvas.load`) résolve `env.X`
directement depuis `dsl.variables[X].value` au lieu de
`dsl.globals['env.X']`. Plus de double stockage, plus de désync
possible. Petit refactor `agent/canvas.py`.

### 8. Slugify strippait les accents

`procédure` → `procdure` (majorité des lettres perdues). Fix appliqué
dans commit `fix(agent/tools): make render_docx_template robust…` —
`_slugify` utilise maintenant `unicodedata.NFKD` pour translittérer
au lieu de stripper.

---

## 🟢 Trompeurs documentaires

### 9. `/no_think` documenté comme solution mais inopérant sur Qwen3-instruct-2507

Les locales `web/src/locales/{en,fr,…}.ts` mentionnent que ajouter
`//no_thinking` au prompt désactive le reasoning des modèles natifs.
**Inopérant** sur Qwen3.5 / Qwen3-instruct-2507 via Ollama OpenAI-compat
(testé en curl direct, le modèle continue de réfléchir).

**À faire** : retirer la mention de `//no_thinking` des locales, ou
ajouter une note « ne marche que sur certains modèles via certains
backends ».

### 10. Ollama OpenAI-compat avale silencieusement les tool_calls Hermes-XML

Qwen3-instruct-2507 émet ses tool calls au format Hermes
(`<tool_call>{"name":"…","arguments":{…}}</tool_call>` en texte).
Ollama essaie de parser → fail sur le template instruct-2507 → strip
silencieusement le contenu. Renvoie `content:""` + `tool_calls:null`
en facturant les tokens générés (`completion_tokens > 0`).

**Workaround actif** : factory custom `Ollama-Hermes`
(`rag/llm/chat_model_custom.py`) qui injecte la spec de tools dans le
sys_prompt et parse les blocs `<tool_call>` côté nous.

**Référence externe** : [SMFloris/ollama-qwen3-coder-proxy](https://github.com/SMFloris/ollama-qwen3-coder-proxy)
documente le même bug avec une approche sidecar.

**À faire** : surveiller [Ollama issue #14617](https://github.com/ollama/ollama/issues/14617)
et issue #12610. Quand Ollama upstream fixe leur parser, retirer la
factory custom et basculer les modèles sur la factory standard `Ollama`.

---

## Watchlist upstream merges

Quand on merge depuis upstream RAGFlow :

- Vérifier que `rag/llm/chat_model.py` garde la ligne d'import
  `from rag.llm.chat_model_custom import OllamaHermesChat`
- Vérifier que `api/db/init_data.py` garde `Ollama-Hermes` dans
  `_SELF_HOST_FACTORIES`
- Vérifier que `conf/llm_factories.json` garde l'entrée `Ollama-Hermes`
- Si upstream change la signature de `Base.async_chat_streamly_with_tools`,
  ajuster `OllamaHermesChat` en miroir
- Si upstream ajoute des champs au schéma `dsl.globals` ou `dsl.variables`,
  vérifier que le panel « Conversation variable » et le runtime restent
  alignés

---

## Récap des quick wins

| # | Item | Statut |
|---|---|---|
| Q1 | Cache stale (Redis replica + Python TTL) | ✅ Auto-hash fingerprint à `load_for_run` |
| Q2 | `is_tools=False` silent failure | ✅ Toggle explicite dans modal Add LLM admin |
| Q3 | Bouton « Reload from DB » | ❌ Rejeté — auto-hash le couvre, le bouton serait dangereux (cross-user impact) |
| Q4 | Préserver factory_hint dans `LlmDropdown` | ⏳ TODO ~1 h |
| Q5 | Audit RBAC routes `document_api` | ⏳ TODO ~2 h |
| Q6 | Refactor schéma `RenderDocxTemplate` à plat | ⏳ TODO ~2 h |
| Q7 | Runtime canvas lit `dsl.variables` direct | ⏳ TODO ~3 h |
| Q8 | Cite default `True` (upstream) | ✋ Pas de change — manuel via Advanced Settings, voir item #5 |
