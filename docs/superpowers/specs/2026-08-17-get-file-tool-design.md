# Tool d'agent `get_file` — pont Files (UI) → sandbox par URL présignée

**Date** : 2026-08-17
**Statut** : validé (design discuté et approuvé en conversation)
**Objectif** : permettre à un agent d'analyser un fichier uploadé par l'UI (section Files du workspace) dans le sandbox k8s, sans bucket public, sans kubectl, sans credentials dans le sandbox — l'expérience opérateur devient : uploader dans Files → poser sa question à l'agent.

## Contexte

- Les recettes sandbox (FAMAT, futur data analyst) lisent leurs données par simple GET HTTP (`famat_recipes.load_url`) — le pod Job est isolé par design : aucun credential MinIO, egress restreint (DNS + MinIO).
- Le stockage RAGFlow est privé : un fichier uploadé via l'UI est dans MinIO mais illisible en GET anonyme.
- Mécanismes existants examinés : l'upload de fichier au message d'agent (`canvas.get_files_async`) **parse le contenu en texte dans le contexte LLM** (impossible pour 90k lignes) ; `ExcelProcessor` lit les fichiers **par référence côté serveur** (`FileService.get_blob(created_by, file_id)`, pattern de permission à réutiliser) mais exécute en process API (pas de sandbox/DuckDB/matplotlib/artefacts).
- Chaînon manquant : résoudre « fichier des Files » → « URL lisible par le sandbox », côté serveur.

## Design

**Nouveau tool d'agent** `agent/tools/get_file.py` (pattern `ToolBase` comme `exesql`/`retrieval`, auto-découvert) :

- **Entrée** (paramètre LLM) : `name` — le nom du fichier dans les Files accessibles au tenant/workspace du canvas. Optionnel : sélection plus fine si homonymes (le tool retourne une erreur listant les candidats si ambigu).
- **Résolution** : lookup via `FileService` **scopé au tenant du canvas** (même sémantique de permission que `ExcelProcessor`/`get_blob` — aucun accès cross-workspace). Fichier introuvable → message d'erreur actionnable pour le LLM.
- **Sortie** : URL **présignée MinIO** (GET, TTL défaut 900 s — couvre largement une exécution de recette) + nom + taille. Le LLM passe cette URL à `code_exec` → `fr.load_url(url)` **inchangé** (zéro modification des recettes ni de l'image sandbox).
- **Sécurité** : URL signée à durée courte, host interne au cluster ; les credentials MinIO restent dans le process API ; le sandbox ne reçoit qu'une capacité temporaire de lecture d'UN objet. La NetworkPolicy du sandbox autorise déjà l'egress MinIO — rien à changer.

**Piège dev connu (host de signature)** : la signature présignée est liée au host. En prod, l'endpoint MinIO configuré est le service interne → directement joignable du sandbox. En dev local, l'endpoint est `localhost:9000`, injoignable depuis le conteneur sandbox (`host.containers.internal` requis) — et on ne peut pas réécrire le host après signature. Solution : env optionnelle `SANDBOX_PRESIGN_ENDPOINT` — si posée, le tool présigne via un second client MinIO construit sur cet endpoint (pattern standard « external endpoint »). Vide en prod (endpoint interne déjà bon), posée en dev.

**Canvas FAMAT mis à jour** : le prompt système remplace la `DATA_URL` codée en dur par « appelle d'abord `get_file("Payload-20260526.csv")`, puis passe l'URL obtenue à la recette ». Ré-export du DSL versionné. Le prochain dump mensuel = ré-upload dans Files sous le même nom, aucun autre geste.

## Hors scope

Chemin base Cyllene (`load_db` + egress + Secret K8s — chantier séparé déclenché par l'ouverture des flux) ; écriture de fichiers par l'agent vers Files (le sens sandbox→Files passe déjà par les artefacts) ; listing/navigation des Files par le LLM (le tool prend un nom, il ne browse pas).

## Critères de réussite

- E2e dev : uploader un CSV dans Files via l'UI/API → question à l'agent FAMAT → `get_file` appelé (visible dans la trace), recette exécutée dans le sandbox via l'URL présignée, carte SPC jointe.
- Un fichier d'un AUTRE workspace est introuvable pour le tool (test de permission).
- URL expirée (TTL dépassé) → GET refusé par MinIO (vérifié une fois).
- Tests unitaires : lookup scopé, introuvable, homonymes, presign appelé avec TTL, endpoint override.
- Le tool apparaît et se configure dans l'UI agent (form minimal — TTL optionnel).
