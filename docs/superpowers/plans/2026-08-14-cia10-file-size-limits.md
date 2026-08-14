# CIA-10 File Size Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fichiers jusqu'à 1 Go, lots de taille illimitée (un POST séquentiel par fichier), et chunking adaptatif calibré sur le modèle d'embedding quand un document produirait trop de chunks.

**Architecture:** Volet A = plomberie de limites (env/Helm/gateway/front). Volet B = refactor du hook front d'upload (séquentiel par fichier, la logique serveur n'est PAS touchée — décision anti-races documentée au spec). Volet C = **two-pass dans `build_chunks`** (task_executor) : premier chunking normal ; si le nombre de chunks dépasse le plafond, recalcul du `chunk_token_num` effectif via un helper pur (borné par 90 % du max_tokens du modèle d'embedding) et re-chunk unique — `rag/app/naive.py` et les autres parsers upstream ne sont pas modifiés.

**Tech Stack:** Python (task_executor), Helm, React/TS (hook + dialog upload), env vars.

## Global Constraints

- Branche de travail : `cia-10` (créée depuis `sync-from-github`).
- Spec : `docs/superpowers/specs/2026-08-14-cia10-file-size-limits-design.md` — le volet B est SÉQUENTIEL (concurrence 1, constante ajustable) par décision utilisateur.
- Fichiers upstream touchés : `rag/svr/task_executor.py` UNIQUEMENT (déjà « Keep ours » dans CLAUDE.md — étendre sa ligne du tableau avec le chunking adaptatif, marqueur `CUSTOM B2B SaaS — adaptive chunking (CIA-10)` sur le bloc injecté). `rag/app/naive.py` et `web/src/services/knowledge-service.ts` (piège axios/X-Workspace-Id) ne doivent PAS être modifiés.
- Valeurs par défaut (spec) : `MAX_CONTENT_LENGTH=1073741824` ; `ADAPTIVE_CHUNK_SIZE=1` ; `MAX_CHUNKS_PER_DOC=4096` ; `ADAPTIVE_CHUNK_TOKEN_MAX=2048` ; cap embedding = `int(embd_max_tokens * 0.9)` ; timeout gateway `900s`.
- Sémantique « par document » : pour les formats paginés (PDF), le plafond s'applique par tâche (plage de pages) — documenté, pas un bug.
- Commits sans trailer `Co-Authored-By`. Front : `PATH=/opt/homebrew/bin:$PATH` pour npm/npx/tsc.
- Ne pas casser : suite `agent/sandbox/tests/` (20 tests), `poc/famat/tests/` (16), et les tests upload de `test/multitenant_http_api`.

---

### Task 1: Volet A — limites 1 Go (env, Helm, gateway)

**Files:**
- Modify: `docker/.env` (~ligne 213)
- Modify: `helm/ragflow/values.yaml`, `helm/ragflow/values-alterai.yaml`
- Modify: `helm/ragflow/charts/ragflow-api/templates/deployment.yaml`, `helm/ragflow/charts/ragflow-task-executor/templates/deployment.yaml`
- Modify: `helm/ragflow/templates/gateway-httproute.yaml` (~ligne 39)

**Interfaces:**
- Produces: env `MAX_CONTENT_LENGTH` disponible dans les pods api et task-executor ; value `global.maxContentLength` (string, défaut `"1073741824"`) ; value `gateway.apiTimeout` (défaut `"900s"`).

- [ ] **Step 1: docker/.env** — décommenter la ligne `# MAX_CONTENT_LENGTH=1073741824` → `MAX_CONTENT_LENGTH=1073741824` (garder les commentaires environnants, y compris la note sur les fichiers d'agents).

- [ ] **Step 2: values** — dans `values.yaml`, bloc `global:` :
```yaml
  # Taille max d'un fichier uploadé (HTTP body Quart + validation DOC_MAXIMUM_SIZE).
  # Lue par ragflow-api ET ragflow-task-executor (common/settings.py). 1 GiB.
  maxContentLength: "1073741824"
```
Et un bloc `gateway:` (ou l'étendre s'il existe) :
```yaml
gateway:
  # Timeout de la route /api|/v1 — uploads volumineux (1 GiB à ~10 Mbps ≈ 14 min → 900s
  # couvre un lien correct) ET streaming SSE. Était hardcodé 600s.
  apiTimeout: "900s"
```
`values-alterai.yaml` : rien à surcharger (les défauts conviennent) — vérifier qu'aucune ancienne valeur ne les contredit.

- [ ] **Step 3: injection env** — dans les DEUX deployments (api, task-executor), au bloc `env:` existant :
```yaml
            - name: MAX_CONTENT_LENGTH
              value: {{ .Values.global.maxContentLength | default "1073741824" | quote }}
```
(Adapter la référence si le sous-chart lit `global` via `.Values.global` — vérifier avec les env voisines du même template.)

- [ ] **Step 4: gateway** — remplacer `request: 600s` par `request: {{ .Values.gateway.apiTimeout | default "900s" }}` dans `gateway-httproute.yaml` (uniquement la route `/api|/v1` ; la route catch-all frontend reste intacte).

- [ ] **Step 5: vérification par rendu**
```bash
helm template rag helm/ragflow -f helm/ragflow/values-kind-dev.yaml | grep -B2 -A1 "MAX_CONTENT_LENGTH" | head -20   # présent dans les 2 deployments
helm template rag helm/ragflow -f helm/ragflow/values-kind-dev.yaml | grep -A2 "timeouts:"                            # request: 900s
helm template rag helm/ragflow -f helm/ragflow/values-alterai.yaml --set sandbox.enabled=false 2>/dev/null | grep -c "MAX_CONTENT_LENGTH"  # >= 2
```

- [ ] **Step 6: Commit**
```bash
git add docker/.env helm/ragflow
git commit -m "feat(cia-10): MAX_CONTENT_LENGTH 1 GiB — env dev, values Helm (api+task-executor), timeout gateway paramétré"
```

---

### Task 2: Volet A — limite côté client (refus avant POST + affichage)

**Files:**
- Create: `web/src/constants/upload.ts`
- Modify: le(s) dialog(s) d'upload de documents de dataset (localiser : composants utilisant `useUploadDocument` / `file-upload.tsx` — suivre les imports depuis `web/src/hooks/use-document-request.ts`)
- Test: `PATH=/opt/homebrew/bin:$PATH npx tsc -b` (web) + lint

**Interfaces:**
- Produces: `MAX_UPLOAD_FILE_SIZE_BYTES = 1024 * 1024 * 1024` et `MAX_UPLOAD_FILE_SIZE_LABEL = '1 Go'` — source unique, consommée par la Task 3 aussi.

- [ ] **Step 1: constante**
```ts
// web/src/constants/upload.ts
// Aligné sur MAX_CONTENT_LENGTH (docker/.env + Helm global.maxContentLength).
// Si la valeur serveur change, changer ici aussi — le serveur reste l'autorité (413).
export const MAX_UPLOAD_FILE_SIZE_BYTES = 1024 * 1024 * 1024;
export const MAX_UPLOAD_FILE_SIZE_LABEL = '1 Go';
```

- [ ] **Step 2: câblage** — dans le dialog d'upload de documents : passer `maxSize={MAX_UPLOAD_FILE_SIZE_BYTES}` au composant d'upload (le support existe — `web/src/components/file-upload.tsx:478` rejette déjà `file.size > maxSize`) ; vérifier que le rejet produit un message utilisateur incluant le nom du fichier et `MAX_UPLOAD_FILE_SIZE_LABEL` (l'ajouter si le composant rejette silencieusement) ; afficher la limite dans le texte du dialog (« Taille max par fichier : 1 Go »). Vérifier s'il existe d'autres points d'entrée d'upload de documents KB (recherche des usages du hook) et leur appliquer la même constante.

- [ ] **Step 3: vérifier** — `PATH=/opt/homebrew/bin:$PATH npx tsc -b` dans `web/` : 0 erreur. Lancer le lint du projet front si configuré (`npm run lint` — tolérer les erreurs pré-existantes hors des fichiers touchés, les signaler).

- [ ] **Step 4: Commit**
```bash
git add web/src/constants/upload.ts web/src/<fichiers touchés>
git commit -m "feat(cia-10): limite 1 Go côté client — refus avant POST + affichage dans le dialog d'upload"
```

---

### Task 3: Volet B — un POST séquentiel par fichier

**Files:**
- Modify: `web/src/hooks/use-document-request.ts` (mutation `UploadDocument`, ~lignes 78-112)
- Modify: le composant dialog appelant (affichage du résultat agrégé)
- Test: `npx tsc -b` + vérification manuelle documentée

**Interfaces:**
- Consumes: `uploadDocument(datasetId, formData)` de `web/src/services/knowledge-service.ts` — **NE PAS MODIFIER ce service** (axios direct + X-Workspace-Id, piège CLAUDE.md).
- Produces: la mutation retourne `{ code: number, results: Array<{ name: string; ok: boolean; message?: string }> }` — `code = 0` si tous ok, sinon le code de la première erreur ; l'UI affiche le décompte et les fichiers en échec.

- [ ] **Step 1: refactor de la mutation**
```ts
const UPLOAD_CONCURRENCY = 1; // séquentiel — décision spec (races duplicate_name/quota
                              // préexistantes côté serveur ; monter à 3 = chantier futur
                              // conditionné au durcissement serveur)

mutationFn: async ({ fileList, parserConfig }) => {
  if (!id) return { code: 500, message: 'Dataset ID is required', results: [] };
  const results: Array<{ name: string; ok: boolean; message?: string }> = [];
  for (const file of fileList) {
    const formData = new FormData();
    formData.append('file', file);
    if (parserConfig) formData.append('parser_config', JSON.stringify(parserConfig));
    try {
      const ret = await uploadDocument(id, formData);
      const code = get(ret, 'code');
      results.push({ name: file.name, ok: code === 0, message: get(ret, 'message') });
    } catch (error) {
      results.push({ name: file.name, ok: false, message: String(error) });
    }
  }
  queryClient.invalidateQueries({ queryKey: [DocumentApiAction.FetchDocumentList] });
  const failed = results.filter((r) => !r.ok);
  return {
    code: failed.length === 0 ? 0 : 500,
    message: failed.length ? `${failed.length}/${results.length} fichiers en échec` : '',
    results,
  };
},
```
(Adapter à la structure exacte du hook existant — conserver les comportements annexes : invalidation, retours attendus par les appelants. `UPLOAD_CONCURRENCY` reste une constante documentée même à 1 — la boucle est écrite pour être triviale à paralléliser plus tard.)

- [ ] **Step 2: consommateurs** — trouver tous les appelants de cette mutation (le dialog d'upload) et adapter la gestion du retour : succès partiel = message listant les fichiers en échec (toast/notification), le dialog se ferme si au moins un fichier est passé ; échec total = dialog reste ouvert avec les messages. Vérifier qu'aucun appelant ne dépend du format de retour précédent (une seule réponse serveur brute).

- [ ] **Step 3: vérifier** — `npx tsc -b` : 0 erreur. Vérification manuelle sur le stack dev (documentée dans le rapport avec captures des réponses) : lot de 3 petits fichiers → 3 POST distincts visibles (network), 3 documents créés ; lot avec 1 fichier en doublon de nom → les 2 autres passent, message d'échec partiel correct.

- [ ] **Step 4: Commit**
```bash
git add web/src/hooks/use-document-request.ts web/src/<dialog>
git commit -m "feat(cia-10): upload de lot séquentiel — un POST par fichier, échec isolé, lot illimité"
```

---

### Task 4: Volet C — chunking adaptatif two-pass calibré embedding

**Files:**
- Create: `rag/svr/adaptive_chunk.py`
- Modify: `rag/svr/task_executor.py` (`build_chunks`, autour de l'appel `chunker.chunk` ~ligne 380 — bloc injecté avec marqueur)
- Modify: `CLAUDE.md` (étendre la ligne `rag/svr/task_executor.py` du tableau « Custom files to watch »)
- Test: `rag/svr/tests` n'existe pas — créer `test/unit_test/rag/svr/test_adaptive_chunk.py` (suivre l'arborescence existante de `test/unit_test/`)

**Interfaces:**
- Produces (helper pur, aucune dépendance RAGFlow) :
```python
def effective_chunk_token_num(configured: int, first_pass_chunks: int, embd_max_tokens: int,
                              max_chunks: int = 4096, hard_cap: int = 2048) -> tuple[int, str | None]:
    """Retourne (chunk_token_num_effectif, raison|None).

    raison None => pas d'adaptation (first_pass_chunks <= max_chunks, ou configured
    invalide <= 0, ou embd_max_tokens <= 0).
    Sinon : new = ceil(configured * first_pass_chunks / max_chunks), borné par
    min(hard_cap, int(embd_max_tokens * 0.9)) et > configured sinon None.
    raison = message français lisible pour la progression (« document volumineux :
    N chunks à X tokens → taille portée à Y »), utilisé tel quel par build_chunks.
    """
```
- Consumes: dans `build_chunks`, le nombre de chunks du premier passage (`len(cks)`), `parser_config_for_chunk.get("chunk_token_num", 128)`, et le `max_tokens` du modèle d'embedding de la tâche — résolu depuis la config du modèle `task["embd_id"]` (lire comment `embd_model_config` est construit vers la ligne 855-860 du même fichier et réutiliser le même chemin de résolution, AVANT le chunking ; le `embedding_model.max_length` de la ligne 864 montre la valeur cible).
- Env : `ADAPTIVE_CHUNK_SIZE` (défaut "1"), `MAX_CHUNKS_PER_DOC` (défaut "4096"), `ADAPTIVE_CHUNK_TOKEN_MAX` (défaut "2048") — lues dans `adaptive_chunk.py` via une petite fonction `adaptive_settings()` (testable par monkeypatch env).

- [ ] **Step 1: tests du helper (échec d'abord)**
```python
# test/unit_test/rag/svr/test_adaptive_chunk.py
import pytest

from rag.svr.adaptive_chunk import effective_chunk_token_num


def test_no_adaptation_under_threshold():
    num, reason = effective_chunk_token_num(512, 1000, 8192)
    assert (num, reason) == (512, None)


def test_scales_up_to_fit_max_chunks():
    # 512 tokens × 20000 chunks → il faut ~2442 tokens pour retomber à 4096 chunks,
    # borné par hard_cap 2048
    num, reason = effective_chunk_token_num(512, 20000, 8192)
    assert num == 2048
    assert reason and "2048" in reason


def test_capped_by_embedding_model_bge512():
    # bge-large 512 tokens → cap = 460 : on ne dépasse JAMAIS ce que le modèle encode
    num, reason = effective_chunk_token_num(128, 20000, 512)
    assert num == 460
    assert reason


def test_bge_m3_room():
    # bge-m3 8192 → cap 7372 ; besoin ~1250 → non borné
    num, reason = effective_chunk_token_num(512, 10000, 8192)
    assert num == 1250
    assert reason


def test_configured_already_above_cap_no_regression():
    # configuré 600 sur bge-512 (cap 460) : on ne RÉDUIT pas (comportement historique
    # conservé), pas d'adaptation à la hausse possible → (600, None)
    num, reason = effective_chunk_token_num(600, 20000, 512)
    assert (num, reason) == (600, None)


def test_invalid_inputs_passthrough():
    assert effective_chunk_token_num(0, 99999, 8192) == (0, None)
    assert effective_chunk_token_num(512, 99999, 0)[1] is None
```

- [ ] **Step 2: vérifier l'échec** — `PYTHONPATH=. uv run python -m pytest test/unit_test/rag/svr/test_adaptive_chunk.py -v` → ModuleNotFoundError.

- [ ] **Step 3: implémenter `rag/svr/adaptive_chunk.py`** — helper pur conforme au contrat (math : `needed = math.ceil(configured * first_pass_chunks / max_chunks)` ; `cap = min(hard_cap, int(embd_max_tokens * 0.9))` ; `new = min(needed, cap)` ; si `new <= configured` → `(configured, None)`) + `adaptive_settings()` lisant les 3 env avec défauts.

- [ ] **Step 4: vérifier les 6 tests PASS.**

- [ ] **Step 5: intégration two-pass dans `build_chunks`** — après le premier `cks = await thread_pool_exec(chunker.chunk, ...)` réussi, bloc marqué :
```python
# CUSTOM B2B SaaS — adaptive chunking (CIA-10). Two-pass : si le document
# produit plus de MAX_CHUNKS_PER_DOC chunks, on augmente chunk_token_num
# (borné par 90% du max_tokens du modèle d'embedding — au-delà les chunks
# seraient tronqués à l'encode, cf. truncate() plus bas) et on re-chunke UNE fois.
# La config de la KB n'est jamais modifiée. ADAPTIVE_CHUNK_SIZE=0 désactive.
```
Logique : flag actif ET `len(cks) > max_chunks` ET le parser utilise `chunk_token_num` (présent dans `parser_config_for_chunk`) → résoudre `embd_max_tokens` (même chemin que l'embedding plus bas), appeler le helper ; si adaptation : `progress_callback` avec la raison, mettre à jour `parser_config_for_chunk["chunk_token_num"]`, re-exécuter le même appel `chunker.chunk` (sous `chunk_limiter`), remplacer `cks`. En plus, hors adaptation : si `configured > cap` → `progress_callback` warning anti-troncature (« chunk_token_num X > capacité du modèle d'embedding Y : les chunks seront tronqués à l'encodage »). Toute exception dans le bloc adaptatif → log + on garde les chunks du premier passage (jamais bloquant).

- [ ] **Step 6: CLAUDE.md** — étendre la ligne existante `rag/svr/task_executor.py` du tableau : mentionner « + bloc adaptive chunking CIA-10 (grep `CUSTOM B2B SaaS — adaptive chunking`) + rag/svr/adaptive_chunk.py (fichier custom) ».

- [ ] **Step 7: vérifier l'ensemble** — tests helper PASS + smoke import : `PYTHONPATH=. uv run python -c "import rag.svr.task_executor" ` (tolérer l'échec env ES/DB tardif comme d'habitude — l'important est qu'aucune SyntaxError/ImportError du bloc n'apparaisse avant) ; `ruff check rag/svr/adaptive_chunk.py`.

- [ ] **Step 8: Commit**
```bash
git add rag/svr/adaptive_chunk.py rag/svr/task_executor.py test/unit_test/rag/svr/ CLAUDE.md
git commit -m "feat(cia-10): chunking adaptatif two-pass — plafond par doc, calibré sur le max_tokens du modèle d'embedding"
```

---

### Task 5: Validation d'ensemble + documentation

**Files:**
- Modify: `helm/ragflow/PRODUCTION_NOTES.md` (section courte « Limites d'upload »)
- Aucun autre fichier de code.

- [ ] **Step 1: suites de non-régression** (stack dev up, env vars de test comme d'habitude — `.env.local` pour ADMIN_JWT_SECRET, RAGFLOW_TEST_LOCAL_AUTH=1, RSA_PASSPHRASE=Welcome, VIEWER/EDITOR_EMAIL, HOST_ADDRESS, ZHIPU/SILICONFLOW dummy, PYTHONPATH=.) :
```bash
uv run python -m pytest test/unit_test/rag/svr/test_adaptive_chunk.py agent/sandbox/tests/ -q        # nouveaux + sandbox
uv run python -m pytest test/multitenant_http_api/test_upload_documents.py -q                        # upload http (adapter le nom réel du fichier de tests upload)
FAMAT_CSV=poc/famat/data/Payload-20260526.csv uv run --with duckdb --with requests --with matplotlib python -m pytest poc/famat/tests/ -q
```
Attendu : tout vert (les 6 échecs self_managed/aliyun pré-existants de `agent/sandbox/tests` restent tolérés).

- [ ] **Step 2: test réel 1 Go en dev** — redémarrer le stack avec la nouvelle env (`MAX_CONTENT_LENGTH` maintenant active), générer un fichier texte de ~900 Mo dans le scratchpad (PAS dans le repo), l'uploader via l'API (curl avec l'auth de test) → 200 ; un fichier de 1,1 Go → rejet propre (413 ou message `DOC_MAXIMUM_SIZE`). Ne PAS lancer son parsing complet (annuler/supprimer le doc après) — le test porte sur l'upload.

- [ ] **Step 3: PRODUCTION_NOTES.md** — section « Limites d'upload (CIA-10) » : la valeur `global.maxContentLength`, le timeout `gateway.apiTimeout`, le fait que le front borne à 1 Go (constante `web/src/constants/upload.ts` à changer en même temps que la value), et les 3 env du chunking adaptatif avec leurs défauts.

- [ ] **Step 4: Commit**
```bash
git add helm/ragflow/PRODUCTION_NOTES.md
git commit -m "docs(cia-10): limites d'upload et chunking adaptatif — notes de prod"
```
