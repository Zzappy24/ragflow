# Infinity v0.7.3 — segfault BM25 : cache de readers fulltext servi après cleanup (UAF)

**Statut** : diagnostiqué par nous (2026-08-28, investigation du clone v0.7.3), posté upstream :
[#3418, rapport de crash](https://github.com/infiniflow/infinity/issues/3418#issuecomment-5446478734) puis
[#3418, diagnostic racine](https://github.com/infiniflow/infinity/issues/3418#issuecomment-5446559296).
**PR de fix soumise : [infiniflow/infinity#3423](https://github.com/infiniflow/infinity/pull/3423)**
(branche `fix/ft-reader-cache-invalidation-on-chunk-cleanup` sur le fork Zzappy24/infinity —
15 lignes, miroir de CleanSegmentIndex dans CleanChunkIndex, vérifié présent sur main au 2026-08-28).
Log du crash conservé par Yoann (`infinity_segfault_*.log`). Clone d'investigation : `~/infinity-src`.

## Symptôme
Recherche fulltext (BM25) → segfault/abort du serveur entier (exit 139), déterministe par terme.
Déclenché chez nous par le corpus parent-child (~500k micro-chunks, churn DELETE+INSERT + merges).
Stack : `PhysicalMatch → BM25Score → PostingIterator::DecodeTFBuffer → FastPForLib::simdunpack`.
Indice décisif : le Select crashé a Begin TS == KV Commit TS de la txn "clean up" précédente.

## Cause racine (chaîne complète)
1. `NewCatalog::CleanChunkIndex` (new_catalog_static_impl.cpp:1135) supprime les fichiers `.pos`/`.dic`
   et détruit le `BufferObj` des `.len` (via `ChunkIndexMeta::UninitSet` puis `BufferManager::RemoveClean`
   qui `erase` la `unique_ptr`) **sans invalider `TableIndexReaderCache`** — asymétrie avec
   `CleanSegmentIndex` (:993-999) qui appelle `InvalidateFtIndexCache()`. Les merges/compactions
   fulltext droppent des metas *chunk* → chemin non-invalidant à chaque fois.
2. `TableIndexReaderCache::GetIndexReader` (column_index_reader_impl.cpp:199) teste `begin_ts >= cache_ts_`
   où `cache_ts_` = begin TS du constructeur du cache — jamais comparé à une mutation d'index → toujours
   vrai → le premier Select post-cleanup reçoit les readers empoisonnés :
   - `ColumnReaderChunkInfo::index_buffer_` = **`BufferObj*` brut** désormais dangling → `->Load()`
     sur mémoire libérée depuis `BM25Score` (column_length_io_impl.cpp:62). UAF pur.
   - `DiskIndexSegmentReader::data_ptr_` = pointeur mmap brut ; le `ByteSlice` ne possède pas le mapping.
3. Mmap servi périmé : `VirtualStore::MmapFile` = cache par CHEMIN sans revalidation
   (virtual_store_impl.cpp:411) et `DeleteFile` ne purge jamais `mapped_files_` ; les base names des
   chunks fulltext sont déterministes (`ft_{base_row_id:016x}...`) → réutilisation de chemin sous churn
   → nouveau `.dic` + VIEUX bytes `.pos` → offsets incohérents → `comp_len` poubelle → simdunpack.
4. Mort du serveur entier : `FragmentTask::OnExecute` RE-THROW l'`UnrecoverableException`
   (fragment_task_impl.cpp:103, `throw e` slicé) et `TaskScheduler::WorkerLoop` (entry de std::thread,
   task_scheduler_impl.cpp:254) n'a AUCUN try/catch → std::terminate.

## Workaround opérationnel (sans rebuild)
`SET GLOBAL cleanup_interval = 0` — désactive le trigger cleanup (runtime-settable,
physical_command_impl.cpp:264 ; défaut 10s, default_values.cppm:98). Coût : croissance disque.
À n'employer QUE si le pattern déclencheur revient (corpus normal = jamais déclenché en semaines).
Un restart purge les caches mais le crash revient au prochain cycle merge+cleanup.

## Fixes candidats (classés, si on patche nous-mêmes un jour)
1. Ajouter `InvalidateFtIndexCache()` dans `CleanChunkIndex` (classe une-ligne, protège les nouvelles requêtes).
2. Pin structurel : handle possédant dans `ColumnReaderChunkInfo` + `shared_ptr<IndexSegmentReader>`
   dans `SegmentPosting` (protège les requêtes en vol).
3. Différer les frees physiques tant qu'un txn BeginTS <= cleanup TS est vivant.
4. Purger `mapped_files_` dans `DeleteFile` + uniquifier les base names.
5. try/catch autour d'`ExecuteFTSearch` + backstop dans `WorkerLoop` (une requête empoisonnée ≠ serveur mort).

Bugs annexes relevés : transposition chunk_id/segment_id (column_index_reader_impl.cpp:104-109) ;
mutex jamais verrouillé dans ColumnIndexReader ; `~BufferHandle()` explicite puis réassignation
(column_length_io_impl.cpp:42,62).

## Protocole opérationnel (tant que le fix upstream n'est pas livré)

**Prod normale : ne rien changer.** Le bug exige un churn massif fusionnant des chunks fulltext —
jamais déclenché en fonctionnement normal (semaines de recul). Le garrot `cleanup_interval=0`
permanent coûterait (croissance disque/méta) plus qu'il ne protège.

**Avant toute opération à fort churn** (re-parse massif du corpus, tests parent-child v2) :
```sql
SET GLOBAL cleanup_interval = 0;   -- geler l'éboueur pendant l'opération
```
**Après l'opération** (fenêtre calme, ex. avec la maintenance de 01h00) :
```sql
SET GLOBAL cleanup_interval = 10;  -- le réveiller ; attendre ~2 min qu'il passe
SET GLOBAL cleanup_interval = 0;   -- (optionnel) le rendormir si une autre vague suit
```
```bash
kubectl -n rag-new2 rollout restart statefulset rag-new2-infinity
```
**Le restart est OBLIGATOIRE et c'est lui le vrai remède** : le cleanup empoisonne le cache de
readers pour la PROCHAINE recherche (déterministe, même des heures plus tard) — seul un restart
purge `TableIndexReaderCache` + `mapped_files_`. Replay WAL ~3-4 min, prouvé fiable (3× le 2026-08-28).

## Audit de robustesse complet (session Infinity, 2026-08-28)

Audit structurel v0.7.3 par 5 agents parallèles — rapport artifact :
https://claude.ai/code/artifact/2c4fde41-1770-40ff-952e-fcef78d5cf98
(mémo : `~/.claude/projects/-Users-zappy-infinity-src/memory/infinity-robustness-audit-2026-08.md`)

Découvertes supplémentaires à impact opérationnel pour NOUS :
- **COMPACT et IMPORT n'invalident JAMAIS le cache de readers fulltext** → résultats fulltext
  silencieusement PÉRIMÉS après notre maintenance nocturne de 01h00 (segments dépréciés servis,
  imports invisibles), jusqu'à une invalidation qui n'arrive pas (bug #3423) ou un restart.
  **⇒ DÉCISION (2026-08-28, après contre-analyse) : compaction MAINTENUE en prod.** Le risque
  recalculé est faible sur notre corpus à faible churn : le COMPACT droppe des SEGMENTS entiers,
  dont le cleanup (≤10 s) passe par `CleanSegmentIndex` qui invalide CORRECTEMENT le cache —
  la fenêtre se referme en secondes ; le trou « périmé pour toujours » ne concerne que les merges
  de chunks d'index (optimize), marginaux hors churn massif. Et des semaines d'empirique (config
  identique) sans crash ni dérive. Suspendre aurait coûté une dégradation lente CERTAINE des
  recherches (accumulation de chunks d'index) contre un risque théorique jamais observé.
  La suspension (`optimizeInterval/compactInterval: 720h` + `maintenance.enabled: false`) reste
  le bon geste UNIQUEMENT pendant une opération à churn massif (cf. protocole ci-dessus) —
  et devient sans objet dès que l'image patchée (invalidation COMPACT/IMPORT incluse au
  patch-set n°3) est déployée.
- **Un CleanupTask est soumis à CHAQUE tick de 10 s** (`BGTaskProcessor::last_cleanup_ts_` jamais
  écrit) → l'exposition au bug d'invalidation est PERMANENTE, pas périodique.
- **2e variante du crash élucidée** : `MmapFile` → `std::filesystem::file_size` (surcharge qui
  throw) sur fichier supprimé → exit 134 (vs 139 pour la variante simdunpack).
- **Le pin lecteur existe déjà et est ignoré** : `BufferObj::rc_` est affiché dans le message
  d'erreur de `PickForCleanup` mais jamais testé comme condition — levier n°1 du patch-set.
- **Kill switch volontaire** : `QueryContext` fait `raise(SIGUSR1)` sur UnrecoverableException
  (shutdown du serveur), l'ancien `throw e;` commenté juste à côté (`query_context_impl.cpp:307`).
- `BufferManager::RequestSpace` : underflow non signé pouvant désactiver définitivement la
  limite mémoire.

Patch-set cyllene re-priorisé (du rapport) : 1) #3423 (fait) ; 2) trio confinement ~50 lignes
(catch-all WorkerLoop + remplacer SIGUSR1 + protéger ~BufferHandle) = toute mort de process →
échec de requête ; 3) invalidation COMPACT/IMPORT ; 4) purge mapped_files_ ; 5) pins structurels
(précédés du fix transposition chunk_id/segment_id) ; 6) cache_ts_ honnête.


## Posture opérationnelle FINALE (synthèse des deux analyses, 2026-08-28)

**Fenêtrer, pas désactiver.** Le danger n'est pas la maintenance en soi mais la maintenance
CONCURRENTE aux requêtes ; et la désactivation permanente capitalise le risque (prolifération
de chunks, perfs dégradées, maintenance finale énorme donc plus risquée).

- **Régime normal** : intervalles internes longs (24h, déjà en place) — pas d'optimize manuel,
  pas de compaction forcée, pas de CREATE INDEX sur table active (Munmap brut sous scans).
- **Fenêtre nocturne (cron 01h00)**, ordre : compact → optimize → **RESTART du statefulset**.
  Le restart final est NON NÉGOCIABLE : compact laisse le cache ft périmé, le cleanup chunk-level
  n'invalide pas (#3423), optimize mute les octets en place — seul le restart purge tout.
  → Implémenté dans le CronJob helm (initContainer maintenance + conteneur kubectl restart,
  RBAC restreint au pod infinity-0, `infinity.maintenance.restartAfter` pour désactiver).
- **Churn massif exceptionnel** : protocole dédié plus haut (geler cleanup pendant, fenêtre après).
- **État cible** : l'image patchée (trio confinement + invalidation COMPACT/IMPORT) rend ces
  précautions inutiles — une base saine n'a pas besoin de fenêtres.
