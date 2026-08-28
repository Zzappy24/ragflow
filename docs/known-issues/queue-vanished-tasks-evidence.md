# Messages disparus / docs figés RUNNING — dossier de preuve (2026-08-28)

## Reproduction du 2026-08-28 (bench ES — moteur doc HORS DE CAUSE)

Kickoff re-parse de 960 docs (`crag-v5-dev`) à ~18:50, DOC_ENGINE=elasticsearch,
image v0.9.15, 3 executors × WORKER_MAX_TASKS=4.

- ~19:00 : les 3 executors s'auto-recyclent (mécanisme de recyclage périodique
  v0.9.13 — `Last State: Terminated, Reason: Completed, Exit Code: 0`).
- Bilan : 917 DONE, **43 docs figés** run=RUNNING, progress ≈ 0.008-0.01,
  progress_msg « 0 tasks are ahead in the queue... », 0 FAIL. Aucune
  consommation ultérieure — état stable >15 min jusqu'à intervention.
- Déblocage : SQL `run='0'` + DELETE des task rows + re-trigger API → les 43
  passent DONE en ~5 min. (⚠ le DELETE a détruit les retry_count — preuve
  partielle ; conserver les rows la prochaine fois.)

## Logs executors (extraits, 18:58-19:05)

```
18:58:44 INFO  task_service.get_task: 99b90db2… already claimed by another worker — leaving the message in the stream
18:58:44 WARN  collect task 99b90db2… is unknown
(× ~8 occurrences similaires 18:58-18:59, container AVANT recycle)
19:02:12 INFO  task_service.get_task: ab46a9b8… already claimed by another worker — leaving the message in the stream
19:02:12 WARN  collect task ab46a9b8… is unknown
19:05:55 WARN  collect task 9152869e… is unknown      ← SANS message "already claimed"
19:05:56 WARN  collect task 9301a560… is unknown      ← idem (None silencieux :
19:05:56 WARN  collect task 9d6d56f2… is unknown         retry_count>=3 ou row absente)
```

## Lecture du code (api/db/services/task_service.py:get_task)

- `get_task` **mute à chaque livraison** : `retry_count += 1` + progress écrasé
  (`Task has been received`, prog = `random.random()/10` → **explique le 0.008
  exact** des docs figés).
- Garde anti-contention = `FOR UPDATE NOWAIT` (transitoire, sain) → « already
  claimed » = deux workers sur le même message au même instant (livraisons
  dupliquées du re-scan XAUTOCLAIM 120 s × 3 pods).
- `retry_count >= 3` (lu avant incrément) → `return None` **silencieux**
  (ligne ~179) → `collect` logge « is unknown » et le message meurt sans
  marquer le doc. Le doc reste RUNNING à ~0.008 pour toujours.
- Chaîne : burst 960 msgs + recycles simultanés → livraisons multiples sans
  traitement réel → retry brûlés → abandon silencieux.

Cohérent avec l'incident des runs Infinity (182 puis 40 tâches « disparues »,
XPENDING vide, 6 « is unknown » loggés) — même mécanisme, moteur indifférent.

## Fix plan (v0.9.16)

1. **Compter une tentative au début de traitement réel** (dans handle_task,
   après acquisition du slot), pas à la livraison — séparer get_task en
   lecture pure + claim explicite.
2. **Abandon bruyant** : à l'écartement définitif (retry>=3, row absente),
   marquer le doc FAIL (progress=-1 + msg) et ACK, au lieu du None silencieux.
3. **Drain au recycle** : attendre/libérer les tâches en vol avant exit 0
   (le lease meurt avec le process → 5 min de latence de reclaim + retries
   brûlés à chaque redémarrage groupé).
4. Test de pin : simuler burst + kill des workers, vérifier 0 doc figé.
