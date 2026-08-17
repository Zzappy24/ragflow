# Get File Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tool d'agent `get_file` : un fichier uploadé dans les Files du workspace devient analysable par le sandbox via une URL présignée courte durée — l'opérateur uploade dans l'UI, l'agent fait le reste.

**Architecture:** Nouveau tool `ToolBase` auto-découvert (`agent/tools/get_file.py`) : lookup du fichier par nom via `FileService` scopé au tenant du canvas (pattern de permission d'`ExcelProcessor`/`get_blob`), presign via `get_presigned_url()` existant du wrapper MinIO, override d'endpoint pour le dev (`SANDBOX_PRESIGN_ENDPOINT`). Le prompt du canvas FAMAT appelle `get_file(...)` puis passe l'URL aux recettes (`load_url` inchangé). Enregistrement UI dans le picker de tools de l'éditeur d'agent.

**Tech Stack:** Python (ToolBase, FileService, minio), canvas DSL, React/TS (registre de tools UI), i18n en/fr.

## Global Constraints

- Branche `get-file-tool` depuis `sync-from-github`.
- Spec : `docs/superpowers/specs/2026-08-17-get-file-tool-design.md`.
- **Permission stricte** : le lookup ne voit QUE les fichiers accessibles au tenant du canvas (`self._canvas._tenant_id`) — aucun accès cross-workspace. En cas de doute sur la sémantique Files du fork (fichiers par user/workspace), copier EXACTEMENT le chemin de résolution d'`ExcelProcessor` (`agent/component/excel_processor.py:~148`) et de `FileService.get_blob`.
- Le sandbox et les recettes ne changent PAS (ni `famat_recipes.py`, ni l'image). `web/src/services/knowledge-service.ts` interdit (piège CLAUDE.md).
- TTL présign défaut 900 s (paramètre de tool `url_expires_s`, borne max 3600).
- Env `SANDBOX_PRESIGN_ENDPOINT` (optionnelle) : si posée, presigner via un client MinIO secondaire construit sur cet endpoint (mêmes credentials) — jamais réécrire le host d'une URL déjà signée. Vide en prod.
- Erreurs du tool = messages actionnables pour le LLM (« fichier introuvable », « plusieurs fichiers nommés X : … », jamais d'exception brute).
- Commits sans trailer `Co-Authored-By`. Front : `PATH=/opt/homebrew/bin:$PATH`, tsc baseline 207 erreurs (0 nouvelle).

---

### Task 1: Tool backend `get_file` (TDD, mocks)

**Files:**
- Create: `agent/tools/get_file.py`
- Test: `test/unit_test/agent/tools/test_get_file.py` (créer l'arborescence avec `__init__.py` selon le pattern des dossiers voisins de `test/unit_test/`)

**Interfaces:**
- Produces: classes `GetFileParam(ToolParamBase)` + `GetFile(ToolBase)` — modèle de structure : `agent/tools/exesql.py`. Meta function-calling :
```python
self.meta: ToolMeta = {
    "name": "get_file",
    "description": "Returns a short-lived internal URL for a file stored in the workspace Files. Use it to hand data files (CSV, Excel, ...) to code_exec recipes: call get_file first, then pass the returned url to the code.",
    "parameters": {
        "name": {"type": "string", "description": "Exact file name as uploaded in Files (e.g. 'Payload-20260526.csv')", "default": "", "required": True}
    },
}
```
  Param de config : `url_expires_s` (défaut 900, check borné 60..3600).
- Sortie (`set_output`) : `formalized_content` = texte pour le LLM contenant l'URL + nom + taille (ex. `File 'X' (10.4 MB) available at: <url> (valid 15 min)`) — vérifier le nom de clé de sortie standard des tools existants (lire `exesql.py`/`retrieval.py` et faire pareil).
- Résolution interne : (a) trouver le fichier par nom, scopé tenant — lire `FileService` pour la bonne requête (et `ExcelProcessor._get/...` comme référence d'appel avec `created_by`) ; (b) en déduire `(bucket, key)` du storage — lire l'implémentation de `FileService.get_blob` et REPRODUIRE sa dérivation bucket/clé (ne pas deviner : c'est la seule vérité) ; (c) presign : `STORAGE_IMPL.get_presigned_url(bucket, key, timedelta(seconds=expires))` (signature réelle à vérifier dans `rag/utils/minio_conn.py:263` — adapter le type d'`expires` à ce que la lib minio attend) ; (d) si `os.environ.get("SANDBOX_PRESIGN_ENDPOINT")` : construire un client `Minio` secondaire sur cet endpoint avec les mêmes credentials (lire comment `minio_conn.py` construit le sien) et presigner avec lui.
- Cas d'erreur (retournés comme contenu, pas levés) : introuvable ; homonymes (liste les candidats avec leur dossier) ; storage indisponible.

- [ ] **Step 1: Tests qui échouent** — mocks de `FileService` et du storage ; cas : (1) succès → l'URL du mock presign apparaît dans la sortie ; (2) introuvable → message « not found » ; (3) deux homonymes → message listant les deux ; (4) `url_expires_s` transmis au presign ; (5) `SANDBOX_PRESIGN_ENDPOINT` posé (monkeypatch) → le client secondaire est utilisé (mock du constructeur Minio) ; (6) le lookup est appelé avec le tenant du canvas mocké (assertion sur les kwargs). Écrire les 6 tests AVANT l'implémentation, vérifier l'échec (ModuleNotFoundError).

- [ ] **Step 2: Implémenter** — en respectant les lectures imposées ci-dessus (exesql pour la structure, get_blob pour bucket/clé, minio_conn pour le presign). Import de `Minio` lazy (uniquement dans le chemin override).

- [ ] **Step 3: Vérifier** — 6 tests PASS ; `PYTHONPATH=. uv run python -c "from agent.tools import get_file"` OK ; `ruff check` clean ; vérifier l'auto-découverte : `python -c "from agent.tools.get_file import GetFile"` + le mécanisme `__init__.py` le charge sans erreur.

- [ ] **Step 4: Commit** — `feat(agent): tool get_file — URL présignée courte durée depuis les Files du workspace`.

---

### Task 2: Canvas FAMAT + e2e dev

**Files:**
- Modify: `poc/famat/system_prompt.md`, `poc/famat/famat_agent_canvas.json` (ré-export), `poc/famat/README.md` (section Source de données : le chemin bucket public devient « legacy POC », le chemin nominal = Files + get_file)
- Test: e2e réel sur le stack dev.

**Interfaces:**
- Consumes: le tool de la Task 1 (nom `get_file`, param `name`).
- Prompt : remplacer le bloc `DATA_URL = "http://host.containers.internal:9000/famat-poc/..."` par la consigne : « Pour obtenir les données, appelle D'ABORD le tool get_file avec name="Payload-20260526.csv", puis utilise l'URL retournée : `con = fr.load_url("<url du tool>")` dans les recettes. Ne réutilise jamais une URL d'une question précédente (elle expire). » — adapter les 3 recettes en conséquence.

- [ ] **Step 1: Préparer le dev** — stack dev up ; sandbox executor-manager up (pool OK) ; exporter `SANDBOX_PRESIGN_ENDPOINT=http://host.containers.internal:9000` dans l'env du serveur API avant de le (re)lancer (piège : les scripts dev ne sourcent pas docker/.env — même précaution) ; uploader le CSV FAMAT dans les **Files** du workspace de test via l'API (`poc/famat/data/Payload-20260526.csv` — PAS dans un dataset ; trouver la route d'upload de Files dans `api/apps/` — file_app / file API — et documenter la commande).
- [ ] **Step 2: Mettre à jour prompt + canvas** — éditer `system_prompt.md`, pousser dans le canvas via l'API (procédure connue des rapports FAMAT : auth admin@test.local, canvas `a01649f6958a11f192717178b890f94c`), ré-exporter le DSL versionné (grep anti-secret).
- [ ] **Step 3: E2e** — poser la recette 1 en chat : vérifier dans la trace/logs que `get_file` est appelé AVANT code_exec, que l'URL présignée est passée à `load_url`, que la carte SPC revient. Chiffres attendus inchangés (93 pièces / 1 segment). Test négatif : demander un fichier inexistant → l'agent répond proprement « introuvable ». Test permission : un fichier uploadé dans un AUTRE workspace est introuvable pour ce canvas.
- [ ] **Step 4: Commit** — `feat(famat): canvas via get_file — les données viennent des Files du workspace`.

---

### Task 3: Enregistrement UI du tool

**Files:**
- Modify: le registre des tools de l'éditeur d'agent (INVESTIGUER : comment le picker « Add tool » du composant Agent construit sa liste — chercher un tool simple existant, ex. Wikipedia/DuckDuckGo, dans `web/src/pages/agent/` : constantes, form, icône, i18n) + `web/src/locales/en.ts`/`fr.ts`.
- Test: `npx tsc -b` (0 nouvelle erreur), vérification visuelle documentée (le tool apparaît dans le picker, form minimal avec `url_expires_s`).

- [ ] **Step 1: Investiguer le pattern** — documenter dans le rapport la liste exacte des fichiers qu'un tool existant touche côté front (constantes/Operator ou liste de tools du composant Agent, form, icônes, i18n, initial values).
- [ ] **Step 2: Ajouter `get_file`** en suivant ce pattern à l'identique — form minimal : champ `url_expires_s` (défaut 900). Libellés en/fr.
- [ ] **Step 3: Vérifier** — tsc au baseline ; capture/description du picker avec le tool visible ; ajout du tool à un agent de test via l'UI si le front dev tourne (sinon le documenter comme vérifié via DSL).
- [ ] **Step 4: Commit** — `feat(web): tool get_file dans l'éditeur d'agent`.

---

### Task 4: Docs + validation d'ensemble

**Files:**
- Modify: `helm/ragflow/PRODUCTION_NOTES.md` (courte note : `SANDBOX_PRESIGN_ENDPOINT` vide en prod — l'endpoint interne est déjà joignable du sandbox ; le flux opérateur Files→agent ; TTL) ; `CLAUDE.md` si un fichier upstream a été touché (a priori aucun — tool et tests sont des fichiers neufs, le front est du custom).
- [ ] **Step 1: Suites** — `test/unit_test/agent/tools/test_get_file.py` + `test/unit_test/rag/svr/` + `agent/sandbox/tests/` (échecs self_managed/aliyun pré-existants tolérés) + `poc/famat/tests/` : tout vert.
- [ ] **Step 2: Docs + commit** — `docs: get_file — flux Files→sandbox, note presign endpoint`.
