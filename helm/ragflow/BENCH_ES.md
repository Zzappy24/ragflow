# Bench ES 9.5.2 vs Infinity — runbook d'installation

Release Helm **isolée** dans le namespace `rag-bench-es` : MariaDB, Redis, MinIO
et Elasticsearch dédiés (aucun partage avec `rag-new2` — partager le Redis
ferait voler les tâches du bench par les executors Infinity de la prod).
Seuls les services vLLM (namespace `vllm`) sont réutilisés : mêmes modèles
bge-m3 / bge-reranker-v2-m3 / Qwen que la référence Infinity → comparaison
à pipeline égal.

## Pré-requis

1. **Image `data/ragflow:v0.9.15`** buildée et poussée sur Harbor.
   Le fix `exclude_source_vectors` (ES 9.x, ragflow#13272) est commité APRÈS
   le build v0.9.14 — une image ≤ v0.9.14 casse silencieusement citations et
   ranking sans reranker sur ES 9. Build habituel :
   ```
   docker build --platform linux/amd64 -f Dockerfile -t harbor.cylndata.cyllene.pro/data/ragflow:v0.9.15 .
   docker push harbor.cylndata.cyllene.pro/data/ragflow:v0.9.15
   ```
2. **Secrets copiés** de `rag-new2` vers `rag-bench-es` (externalSecrets off
   sur ce cluster — les secrets sont pré-créés). Une commande par secret :
   ```
   kubectl create namespace rag-bench-es
   for s in ragflow-app-secrets ragflow-mariadb-root ragflow-mariadb-app ragflow-redis-auth ragflow-minio-root harbor-pull-secret; do kubectl -n rag-new2 get secret $s -o yaml | sed 's/namespace: rag-new2/namespace: rag-bench-es/' | kubectl apply -f -; done
   ```
   (mots de passe identiques à la prod : acceptable pour un bench interne,
   le namespace n'est pas exposé publiquement)
3. Les **operators** MariaDB / Redis (OT) / MinIO sont cluster-wide — rien à faire.

## Install

```
helm upgrade --install rag-bench helm/ragflow \
  -f helm/ragflow/values-alterai.yaml \
  -f helm/ragflow/values-bench-es.yaml \
  -n rag-bench-es --create-namespace
```

(ou app ArgoCD dédiée `rag-bench-es` avec les DEUX values files, alterai en premier)

Attendu au premier boot : ES Ready en ~2 min, puis api/executors. La base
MariaDB est vierge → `init_database_tables` prend ~1-2 min (pas 25 min : la
MariaDB du cluster est sur du vrai stockage, pas Docker Desktop).

## Accès (pas d'exposition publique)

```
kubectl -n rag-bench-es port-forward svc/rag-bench-ragflow-api 9380:9380
# UI si besoin :
kubectl -n rag-bench-es port-forward svc/rag-bench-ragflow-frontend 9222:80
```

## Bootstrap du bench (une fois les pods up)

1. Register + login sur `http://127.0.0.1:9380` (routes `/api/v1/users`,
   `/api/v1/auth/login` — mot de passe chiffré Base64+RSA `conf/public.pem`).
2. Passer l'utilisateur superuser (bypass RBAC pour la config modèles) :
   ```
   kubectl -n rag-bench-es exec sts/rag-bench-mariadb -- mysql -uragflow -p<pw> ragflow \
     -e "UPDATE user SET is_superuser=1 WHERE email='<email>';"
   ```
3. Provisionner les modèles vLLM (flow provider→instance→models, comme le
   e2e local — ⚠ `default` est un nom d'instance INTERDIT) :
   `PUT /api/v1/providers` `{provider_name: "VLLM"}` puis
   `POST /api/v1/providers/VLLM/instances` avec `model_info` inline, puis
   `PATCH /api/v1/models/default` (embedding bge-m3, chat Qwen, rerank).
4. Clé API : `INSERT INTO api_token` (voir e2e) ou `/api/v1/api_keys` avec
   un workspace provisionné.
5. Driver CRAG : `poc/crag/drive.py` pointé sur `http://127.0.0.1:9380`.

## Protocole (memory project_crag_benchmark + project_engine_strategy)

- **Config A (référence)** : prod rag-new2 Infinity v0.7.3 — chiffres déjà en main.
- **Config B (drop-in)** : cette release, mapping/code tels quels.
- **Config C (VectorDB mode)** : après B, recréer la KB avec le mode
  auto-calibré ES 9.5 (`index.mode: vectordb` dans les settings d'index —
  nécessite un essai manuel, non câblé dans le code).
- Mesures : ingestion 960 docs CRAG (durée, erreurs), latences requête
  p50/p95 hybride ±reranker, RAM/CPU des pods moteur, score CRAG (mêmes
  260 questions, même juge), churn (re-parse), et TCO qualitatif
  (l'exosquelette Infinity — sémaphores, no-replay, fenêtre+restart —
  devient supprimable sur ES).
- L'ingestion sert AUSSI de validation parent-child v2 (CHILD_MIN_CHARS,
  consolidation) — sans risque FastPFor sur ES.
- Caveat à noter au rapport : executors bench = CPU (les 8 slots MIG sont
  pris par la prod) — sans impact sur le corpus CRAG (HTML/texte, pas
  d'OCR DeepDoc) ni sur les latences de requête, mais la vitesse brute
  d'ingestion n'est pas comparable à la référence GPU.

## Teardown

```
helm -n rag-bench-es uninstall rag-bench
kubectl delete namespace rag-bench-es   # supprime PVC/data du bench
```
