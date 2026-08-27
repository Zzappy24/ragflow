# Infinity v0.7.3 — segfault BM25 : cache de readers fulltext servi après cleanup (UAF)

**Statut** : diagnostiqué par nous (2026-08-28, investigation du clone v0.7.3), posté upstream :
[#3418, rapport de crash](https://github.com/infiniflow/infinity/issues/3418#issuecomment-5446478734) puis
[#3418, diagnostic racine](https://github.com/infiniflow/infinity/issues/3418#issuecomment-5446559296).
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
