# CIA-10 — Taille max des fichiers/lots à l'upload + chunking adaptatif

**Date** : 2026-08-14
**Statut** : validé pour plan d'implémentation
**Tâche source** : CIA-10 — « Augmenter autant que possible la taille maximale d'un fichier et d'un groupe de fichiers lors de l'upload pour parsing. Trouver et mettre en place une solution de contournement lorsque le fichier génère trop de chunks (méthode de parsing, taille de chunks adaptative, …). »

## Décisions de cadrage (validées utilisateur)

- **Limite unitaire cible : 1 Go par fichier** (au-delà → upload resumable, chantier séparé hors scope, à ouvrir seulement si un vrai fichier > 1 Go *parsable* se présente — l'upload n'est que la moitié facile, le parsing d'un tel fichier est l'autre problème).
- **« Groupe de fichiers » : taille de lot illimitée** en passant d'un POST multipart unique à un POST par fichier (le front met aujourd'hui TOUT le lot dans une seule requête — la limite de 128 Mo s'applique à la somme, c'est le bug perçu).
- **« Trop de chunks » : cas précis non identifié** → solution générique : taille de chunk adaptative par document, **calibrée sur le modèle d'embedding de la KB** (l'utilisateur est en bge-m3, 8192 tokens — mais la garde vaut pour toute KB).

## État des lieux (vérifié dans le code)

| Couche | Valeur actuelle | Fichier |
|---|---|---|
| HTTP body (Quart) | `MAX_CONTENT_LENGTH` env, défaut **128 Mo** | `api/apps/__init__.py:97` |
| Validation par fichier | `DOC_MAXIMUM_SIZE` = même env | `common/settings.py:410` |
| docker/.env | `MAX_CONTENT_LENGTH=1073741824` **commenté** | `docker/.env:213` |
| Chart Helm prod | `MAX_CONTENT_LENGTH` **absent** → 128 Mo en prod | aucun template ne le pose |
| Gateway prod | timeout route `/api\|/v1` **600 s hardcodé** | `helm/ragflow/templates/gateway-httproute.yaml:39` |
| Front — lot | tous les fichiers dans **un seul FormData** | `web/src/hooks/use-document-request.ts:87` |
| Go `ParseMultipartForm(32<<20)` | seuil mémoire, PAS une limite (spill disque) ; serveur Go absent du chart prod | `internal/handler/file.go:294` — rien à faire |
| Nombre de fichiers/user | `MAX_FILE_NUM_PER_USER` env (quota) | inchangé, hors scope |
| Fichiers uploadés *aux agents* | chemin distinct (note upstream docker/.env:215) | hors scope, documenté |

## Volet A — Limite unitaire 1 Go

1. `docker/.env` : décommenter `MAX_CONTENT_LENGTH=1073741824` (dev).
2. Chart Helm : nouvelle value `global.maxContentLength` (défaut `"1073741824"`), injectée en env `MAX_CONTENT_LENGTH` dans les deployments **ragflow-api** ET **ragflow-task-executor** (les deux lisent `common/settings.py`). Posée dans `values.yaml` + reprise `values-alterai.yaml`.
3. Gateway : le timeout `600s` de la route API devient une value (`gateway.apiTimeout`, défaut `900s`) — 1 Go à ~10 Mbps ≈ 14 min : 900 s couvre un lien correct, et la valeur devient ajustable sans toucher au template.
4. Front : les composants d'upload affichent la limite (« 1 Go max par fichier ») et refusent côté client avant le POST (éviter d'uploader 900 Mo pour recevoir un 413). La limite front est lue d'une constante unique (pas dupliquée par dialog).

## Volet B — Lot illimité : un POST par fichier

Refactor de `useUploadDocument` (`web/src/hooks/use-document-request.ts`) :
- Une requête `uploadDocument` **par fichier**, **séquentielle** (concurrence = 1, constante front ajustable), `parser_config` répété sur chaque requête.
- **Décision concurrence (question utilisateur)** : la logique serveur n'est PAS touchée, mais un lot parallèle augmenterait la probabilité de deux races check-then-act préexistantes : `duplicate_name()` (query-loop, `api/db/services/__init__.py:45` — deux uploads parallèles du même nom peuvent obtenir le même `nom(1).ext`) et le contrôle de quota stockage du fork. Le but de CIA-10 (taille de lot illimitée) est entièrement atteint en séquentiel → concurrence 1 par défaut, zéro risque nouveau. Passer à 3 = chantier ultérieur conditionné au durcissement de `duplicate_name` (contrainte d'unicité + retry).
- Agrégation : le lot continue si un fichier échoue ; résultat final = liste `{fichier → ok/erreur}` remontée à l'UI (le composant d'upload affiche l'état par fichier) ; invalidation du cache une fois le lot terminé.
- **Contrainte fork** : `uploadDocument` de `web/src/services/knowledge-service.ts` utilise `axios` direct + header `X-Workspace-Id` (piège documenté CLAUDE.md) — le refactor ne touche PAS ce chemin, il ne change que l'appelant.
- Effet : la taille du lot n'a plus aucune limite HTTP ; seule la limite unitaire s'applique.

## Volet C — Chunking adaptatif calibré embedding

Dans le task executor, au moment du chunking (chemin `naive`/token-based en priorité — c'est lui qui explose) :

1. **Plafond embedding** : récupérer `max_tokens` du modèle d'embedding de la KB (`embd_id` → config modèle) ; `chunk_cap = int(embd_max_tokens * 0.9)` (marge : les tokenizers du chunker et du modèle divergent). Pour bge-m3 : cap ≈ 7372 ; pour un bge-512 : cap ≈ 460.
2. **Adaptation** : si `tokens_extraits / chunk_token_num_kb > MAX_CHUNKS_PER_DOC` (env, défaut 4096), augmenter le `chunk_token_num` **effectif de ce document uniquement** jusqu'à `min(nécessaire, chunk_cap, ADAPTIVE_CHUNK_TOKEN_MAX)` (env, défaut 2048 — garde de qualité : au-delà, le retrieval se dilue même sans troncature).
3. **Si toujours au-dessus du plafond de chunks** : parser quand même, warning visible dans la progression du document (« document volumineux : N chunks, taille de chunk portée à X »).
4. **Garde troncature (bénéfice collatéral)** : si le `chunk_token_num` configuré par l'utilisateur dépasse `chunk_cap` (même sans adaptatif), warning dans la progression — aujourd'hui la troncature à l'embedding est silencieuse et dégrade le retrieval sans aucun signal.
5. Feature flag : `ADAPTIVE_CHUNK_SIZE=1` par défaut ; `=0` restaure le comportement actuel.
6. La config de la KB n'est **jamais** modifiée — l'adaptation est locale au run de parsing du document (visible dans le log de progression, reproductible au re-parse).

## Critères de réussite

- Upload d'un fichier de ~900 Mo OK en dev ET en prod (gateway, API, validation) ; un fichier > 1 Go est refusé proprement côté client ET serveur.
- Lot de 20 fichiers × 100 Mo : passe intégralement, progression par fichier, un échec n'annule pas le lot.
- Un document dont le chunking naïf produirait > 4096 chunks est parsé avec un `chunk_token_num` adapté, tracé dans la progression, chunks finaux ≤ plafond (ou warning si impossible).
- Aucun chunk produit ne dépasse 90 % du max tokens du modèle d'embedding de la KB.
- Tests : unitaires sur la fonction d'adaptation (cas bge-512/bge-m3, plafonds, flag off) ; test front sur l'agrégation d'erreurs du lot ; suite `test/multitenant_http_api` upload inchangée verte.

## Hors scope

Upload resumable multi-Go (tus/chunked) ; fichiers uploadés aux agents (chemin distinct) ; `MAX_FILE_NUM_PER_USER` ; optimisation du parsing lui-même (OCR long sur gros PDF — orthogonal).
