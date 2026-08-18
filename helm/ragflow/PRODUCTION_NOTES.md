# Production deployment notes — Cyllene RKE2 cluster

This doc captures everything that bit us during the kind validation pass and
that is going to bite again in prod if we forget. Read before doing the first
prod sync, **especially** the "Pre-flight" and "Known traps" sections.

---

## Pre-flight (must be true before ArgoCD sync)

### 1. Vault — secrets must exist BEFORE the chart syncs

Our `ExternalSecret` CRs resolve from `kv/ragflow/*` via the `vault-cyllene`
ClusterSecretStore. If those paths don't exist, pods stay stuck in init
container forever (no auto-retry — the ExternalSecret status is broken but the
chart pretends it's fine).

Required paths (the `vault-init.sh` script provisions equivalents in the kind
test; replicate in prod):

```
kv/ragflow/db          → mariadb-root-password, mariadb-app-password
kv/ragflow/redis       → redis-password
kv/ragflow/minio       → access-key, secret-key
kv/ragflow/app         → secret-key, jwt-secret, llm-default-key
kv/ragflow/oauth       → google-client-secret, github-client-secret (optional)
```

Verify before sync:

```bash
kubectl -n ragflow get externalsecret -o wide
# All STATUS columns must be "SecretSynced". If "SecretSyncedError",
# the resolved Secret will not be created and the chart will hang.
```

### 2. CRDs first, operators second, CRs third (ArgoCD sync-waves)

The `mariadb-operator` Helm chart ships `crds.enabled: false` by default.
Installing it without CRDs causes a CrashLoop with `no matches for kind
MariaDB`. Wave layout (already encoded in `argocd/applicationset-operators.yaml`):

| Wave | Resource |
|------|----------|
| 0 | `mariadb-operator-crds` (separate chart!), other CRD-only charts |
| 1 | All operators (mariadb-operator, redis-operator OT, minio-operator, KEDA, external-secrets) |
| 2 | Operator-managed CRs (MariaDB cluster, RedisFailover, Tenant) — these come from the umbrella chart |
| 3 | App workloads (ragflow-api, ragflow-task-executor, ragflow-frontend) |

If you ever change ArgoCD app order, verify a clean install on staging first.

### 3. Image must be in Harbor with the exact tag

`values-cyllene-prod.yaml` references `harbor.cyllene.local/ragflow/ragflow:cyllene-1.0.0`.

```bash
# Quick check from within cluster:
kubectl run -it --rm imgcheck --image=alpine --restart=Never -- sh -c \
  "wget -qO- http://harbor.cyllene.local/v2/ragflow/ragflow/tags/list"
```

GitLab CI (`.gitlab-ci.yml`) uses kaniko to build and push; the tag is the
appVersion from `helm/ragflow/Chart.yaml`. **Bump appVersion + commit before
syncing ArgoCD**, otherwise nothing happens (Helm doesn't see new image).

### 4. Storage class — rook-ceph-block

Confirm: `kubectl get sc | grep rook-ceph-block`. The umbrella values default
`global.storageClass: rook-ceph-block`. Infinity needs RWO (it's not
multi-master). If you switch to CephFS later, also flip Infinity to clustered
mode (3 replicas + raft) — not yet implemented, see comment in
`templates/infinity-statefulset.yaml`.

### 5. NetworkPolicies (NEVER tested under load)

`networkPolicy.enabled: true` in prod values. The default-deny + explicit-allow
rules in `templates/networkpolicy-*.yaml` were **not** exercised in kind
(disabled there for simplicity). On first prod sync, smoke-test:

- api → mariadb (`mysql -h ragflow-mariadb -u root -p`)
- api → redis (`redis-cli -h ragflow-redis ping`)
- api → infinity (`curl http://ragflow-infinity:23820/`)
- task-executor → minio + infinity
- ingress → api via Gateway

If anything 503s with no app log, NetworkPolicy is the first suspect.

---

## Known traps (encountered during kind validation)

### CAP_DAC_OVERRIDE — don't chown `/ragflow` in the Dockerfile

Pods run with `securityContext.capabilities.drop: ["ALL"]`. When `/ragflow` is
owned by a non-root uid, root **loses the ability to write there** (Linux
removes the DAC override capability). This breaks:

- `tiktoken.get_encoding("cl100k_base")` writes its cache at
  `/ragflow/<sha256>.tmp` on first import → `PermissionError` → Hypercorn
  worker boot crash → pod CrashLoopBackOff at startup.
- Any module that writes to its own install dir (sentence-transformers caches,
  NLTK data, peewee compiled queries).

The Dockerfile creates uid 10001 and chowns **only** the nginx writable paths
(`/var/log/nginx`, `/var/cache/nginx`, `/var/lib/nginx`, `/var/run/nginx`).
**Never** add `chown -R /ragflow` back, even if Phase 2 hardening adds
`USER 10001` — instead, mount emptyDirs at the cache locations.

Phase 2 plan: `runAsUser: 10001` + emptyDirs at `/root/.cache`,
`/ragflow/.cache` (or wherever the actual writes land — verify with `strace`
on a Phase 1 pod first).

### `service_conf.yaml` overlay — RAGFlow does NOT read env vars

This is the single biggest gotcha. `MYSQL_HOST`, `REDIS_HOST`, etc. are **not**
read directly. The Python config layer (`api/db/db_models.py`,
`rag/utils/redis_conn.py`) reads `conf/service_conf.yaml`, with an optional
overlay at `conf/local.service_conf.yaml`.

The chart works around this with an init container:

1. ConfigMap `<release>-app-config` ships a `service_conf.yaml.template`
   with `${REDIS_PASSWORD}`-style placeholders (see `templates/configmap-app.yaml`).
2. Init container `render-service-conf` runs `envsubst` with envFrom the
   `ragflow-app-secrets` Secret and writes the rendered file to a shared
   emptyDir.
3. Main container mounts the rendered file at
   `/ragflow/conf/local.service_conf.yaml` via subPath.

If you change `service_conf.yaml` in upstream, **diff the template** and update
both the main file and our template ConfigMap together.

### Redis config quirks (operator OT, not Bitnami)

Three traps in a row when wiring Redis:

1. **Service name** — operator OT publishes the Service as `<release>-redis`
   (NOT `<release>-redis-master` Bitnami-style). Check with
   `kubectl get svc | grep redis` and update the ConfigMap if the operator
   ever renames its conventions.
2. **`additionalRedisConfig` is a ConfigMap NAME, not inline yaml** —
   we ship a separate `<release>-redis-tuning` ConfigMap in
   `templates/redis-failover.yaml` and reference it by name.
3. **Image tag pinning** — Opstree deletes old tags from quay.io. We pin
   `quay.io/opstree/redis:v8.6.2` (v7.4.0 was pulled). Re-check before each
   upstream operator bump.
4. **`host:port` format** — RAGFlow does
   `self.config["host"].split(":")[1]`. Pass the full `host:port` in one
   string (`ragflow-redis.ragflow.svc.cluster.local:6379`), NOT split into
   `host:` + `port:`. Without the colon, IndexError at boot.

### `RedisSentinel` `clusterSize: 0` is invalid

The chart conditionally renders `RedisSentinel` (`{{- if gt (int
.Values.redis.sentinelReplicas) 0 }}`). In prod set `sentinelReplicas: 3`. In
dev/staging where you don't need HA, leave it at 0 — the conditional skips the
CR entirely.

### nginx `ragflow.conf` (NOT `default.conf`)

The upstream RAGFlow image ships `/etc/nginx/nginx.conf` with a hardcoded
`include /etc/nginx/conf.d/ragflow.conf;` — NOT a wildcard `*.conf`, NOT
`default.conf`. The frontend ConfigMap key MUST be named `ragflow.conf` and
the deployment subPath mount MUST target `/etc/nginx/conf.d/ragflow.conf`.

### Probes — httpGet on `/healthz` + `/readyz`, not on auth-gated routes

`/api/v1/system/version` returns 401 without auth. Kubelet treats 401 as
"alive" (not 5xx) so it works as a liveness probe — but it's sloppy. We added
proper public endpoints in `api/asgi.py`:

- `/healthz` — 200 always, no DB/Redis hit. Liveness probe target.
- `/readyz` — 200 if Redis + MariaDB reachable, 503 otherwise with
  `{"reason": "redis"|"mysql:..."}`. Readiness probe target.

**Don't** front these with auth or middleware that hits the data plane.

### Probe timing — startupProbe long, liveness/readiness short

Cold boot is slow (~30–90s on Phase 1, longer with full ML imports). Pattern:

```yaml
startupProbe:
  httpGet: { path: /healthz, port: http }
  periodSeconds: 10
  failureThreshold: 60        # 600s budget for cold boot
livenessProbe:
  periodSeconds: 15
  failureThreshold: 4         # 60s tolerance after startup
readinessProbe:
  periodSeconds: 10
  failureThreshold: 3
```

A startupProbe gates the other two — they don't fire until startup succeeds.
This avoids liveness killing a slow-but-healthy boot.

### Memory — limit ≥ 2Gi per api pod (without LIGHTEN)

Cold-boot import of common.settings → rag.utils.es_conn → rag.nlp pulls in
sentence-transformers, tiktoken, etc. Resident set ≈ 600MB before serving
first request. A 1Gi limit OOMKills the pod during `init_database_tables`.
Set `LIGHTEN=1` in dev to skip ML model loads (drops to ~400MB).

### Hypercorn — 1 worker per pod, scale via replicas

Already documented in `charts/ragflow-api/values.yaml`. Multi-worker per pod
is the gunicorn-on-VM pattern, anti-Kubernetes:

- A leaky worker takes the whole pod down.
- `kubectl logs` is per-pod, not per-worker.
- HPA can't see per-worker CPU.

Keep `hypercornWorkers: 1`.

### Infinity init chmod 0777 — keep it

The init container in `templates/infinity-statefulset.yaml` does
`mkdir -p /var/infinity/{log,data,wal,...}` + `chmod -R 0777`. On
local-path-provisioner (kind) the freshly-bound PVC mounts in a state where
even root can't write `/var/infinity/log/infinity.log` first time — a quirk of
the kindnet loopback driver. On rook-ceph this is a no-op (volume is empty
and root-writable), but we keep the init for portability and future-proofing
when we move to a non-Ceph PV.

### Limites d'upload (CIA-10)

**Valeurs**

| Paramètre | Où | Valeur |
|---|---|---|
| `global.maxContentLength` | `helm/ragflow/values.yaml` → injecté dans `ragflow-api` et `ragflow-task-executor` (`MAX_CONTENT_LENGTH`) | `"1073741824"` (1 GiB) |
| `gateway.apiTimeout` | `helm/ragflow/values.yaml` → `templates/gateway-httproute.yaml`, route `/api/` + `/v1/` | `"900s"` (était 600s codé en dur, pour couvrir l'upload + le SSE chat) |
| `docker/.env` (Docker Compose only) | `MAX_CONTENT_LENGTH=1073741824` | même valeur, décommentée |

Le front borne aussi à 1 Go **avant** le POST (évite d'attendre la fin d'un
transfert que le serveur va rejeter) : `web/src/constants/upload.ts` →
`MAX_UPLOAD_FILE_SIZE_BYTES = 1024 * 1024 * 1024`. **Les deux valeurs (Helm +
constante front) doivent être changées ensemble** — rien ne les garde en
synchro automatiquement.

**Chunking adaptatif two-pass** (`rag/svr/adaptive_chunk.py`, consommé dans
`build_chunks` de `rag/svr/task_executor.py`) — évite qu'un très gros document
avec un `chunk_token_num` petit ne génère un nombre de chunks ingérable :

| Env var | Défaut | Rôle |
|---|---|---|
| `ADAPTIVE_CHUNK_SIZE` | `"1"` (activé) | `"0"`/`"false"`/`"False"`/`""` désactive |
| `MAX_CHUNKS_PER_DOC` | `16384` | seuil au-delà duquel le two-pass se déclenche (révision qualité-d'abord 2026-08-14 : à 512 tokens/chunk ≈ >130 Mo de texte pur — une spec client dense garde sa granularité intégrale ; 16k chunks ≈ 64 Mo de vecteurs, négligeable) |
| `ADAPTIVE_CHUNK_TOKEN_MAX` | `1024` | plafond dur de la taille de chunk recalculée (zone de bonne qualité bge-m3 — la dilution sémantique se paie à chaque requête, le volume seulement à l'ingestion), en plus du plafond dérivé de `embd_max_tokens * 0.9` |

**Overrides par KB** (clés du `parser_config` de la dataset, prioritaires sur
les env — politique par workspace) : `adaptive_enabled` (bool),
`adaptive_max_chunks` (int > 0), `adaptive_token_max` (int > 0). Une KB
qualité-critique désactive ou relève son plafond ; une KB d'ingestion de masse
serre. **Qualité RAG — hiérarchie des recommandations pour les documents à
texte massif** : (1) découper le fichier source à l'ingestion (100-200 Mo par
morceau) — granularité optimale, l'adaptatif ne se déclenche jamais ; (2) mode
parent-child de la KB (enfants courts pour le matching précis, parents longs
pour le contexte LLM) — le message d'adaptation le recommande explicitement ;
(3) laisser l'adaptatif faire (filet de sécurité). L'`overlapped_percent` de la
KB est préservé par l'adaptation — 10-15 % d'overlap atténue les pertes aux
frontières des chunks agrandis.

**Piège local dev — `MAX_CONTENT_LENGTH` n'est PAS repris par
`scripts/dev_up.sh` / `dev_simple.sh`.** Ces scripts exportent `PYTHONPATH`,
`DOC_ENGINE`, `ADMIN_JWT_SECRET`, `RSA_PASSPHRASE` puis sourcent `.env.local`
— **jamais `docker/.env`**. La valeur `MAX_CONTENT_LENGTH=1073741824` de
`docker/.env` n'est donc lue que par les conteneurs Docker Compose complets,
pas par un serveur/task-executor lancé en local via ces scripts. Constaté en
conditions réelles le 2026-08-14 : un stack dev démarré avant le merge de
CIA-10 tournait toujours sans `MAX_CONTENT_LENGTH` dans son environnement
(`ps eww <pid> | grep MAX_CONTENT_LENGTH` vide) — tout upload > 128 Mo (le
défaut Quart codé dans `api/apps/__init__.py`) aurait été rejeté malgré le
changement livré. Pour tester en local : `export
MAX_CONTENT_LENGTH=1073741824` **avant** d'invoquer `dev_up.sh`/`dev_simple.sh`
(la variable exportée dans le shell parent survit au `source .env.local`, qui
ne la redéfinit pas). En K8s ce piège n'existe pas : la valeur vient de
`global.maxContentLength` injectée directement dans les deployments.

**Validation réelle (2026-08-14, stack dev relancée avec `MAX_CONTENT_LENGTH`
exporté)** :
- Upload d'un fichier de ~900 Mo (943 718 400 octets) via `curl -F` → `HTTP
  200`, `code: 0`, document créé. ~17 s en local.
- Upload d'un fichier de ~1,1 Go (1 153 433 600 octets) → rejet propre,
  quasi instantané (< 0.1 s, coupé avant lecture complète du corps). **Le
  rejet arrive en `HTTP 200` avec `{"code":100,"message":"<RequestEntityTooLarge
  '413: Request Entity Too Large'>"}`**, pas en statut HTTP 413 brut — l'app
  Quart encapsule systématiquement les erreurs dans une enveloppe JSON 200.
  Le message contient bien la classe d'exception Werkzeug attendue, donc
  détectable côté client/monitoring par pattern-matching sur `message`, pas
  sur le status code HTTP.
- Chunking adaptatif, e2e réel (KB `chunk_token_num=32`, doc texte varié
  ~2 Mo, 21 232 chunks en 1re passe) : le message adaptatif apparaît bien
  dans `progress_msg` ET dans les logs du task executor (`document volumineux
  : 21232 chunks à 32 tokens → taille portée à 166`), le document se parse
  avec succès (`run: DONE`). **Nuance constatée** : le nombre de chunks final
  observé était de 4496, soit ~10% au-dessus du `MAX_CHUNKS_PER_DOC` en
  vigueur lors de ce test (4096 ; défaut porté à 16384 depuis) —
  la formule `needed = ceil(configured * first_pass_chunks / max_chunks)` de
  `effective_chunk_token_num` suppose un nombre de chunks proportionnel
  linéairement à `1/chunk_token_num`, ce qui n'est qu'une approximation : le
  chunker respecte les frontières de ligne et ne découpe jamais au milieu,
  donc le ratio réel dépend de la distribution des longueurs de ligne du
  document. Le two-pass reste très efficace (21 232 → 4496, réduction ~79%)
  mais **n'est pas une garantie stricte du plafond** — à garder en tête pour
  du monitoring/alerting basé sur `MAX_CHUNKS_PER_DOC` (prévoir une marge,
  pas une égalité stricte).

---

## Smoke test sequence (post first sync)

Run these in order. Each one validates a layer.

```bash
# 0. ExternalSecrets resolved
kubectl -n ragflow get externalsecret -o wide
# Expect: all "SecretSynced" / "True"

# 1. Operators ready
kubectl -n ragflow get mariadb,redisfailover,tenant
# Expect: all "Ready: True"

# 2. PVCs bound
kubectl -n ragflow get pvc
# Expect: all "Bound"

# 3. App pods Running 1/1
kubectl -n ragflow get pods
# If any 0/1 Running, check `kubectl describe pod` and `kubectl logs`

# 4. /healthz + /readyz
kubectl -n ragflow port-forward svc/ragflow-api 9381:80 &
curl -sS http://localhost:9381/healthz   # → {"ok":true}
curl -sS http://localhost:9381/readyz    # → {"ok":true}

# 5. Frontend
kubectl -n ragflow port-forward svc/ragflow-frontend 8080:80 &
curl -sI http://localhost:8080/          # → HTTP 200

# 6. Gateway / external (only after DNS + TLS are wired)
curl -v https://ragflow.cyllene.local/healthz
```

---

## Phased rollout (don't big-bang)

The kind validation closed ~80% of the infra risk. The remaining 20% is
real-cluster-only (Vault, NetworkPolicies, Gateway TLS, MariaDB Galera 3-node,
KEDA scaling under load, vLLM connectivity). Do **two passes**:

### Pass A — staging namespace (`ragflow-staging`)

- Same cluster, isolated namespace.
- `replicas: 1` everywhere, `sentinelReplicas: 0`, `mariadb.replicas: 1`.
- `networkPolicy.enabled: true` — this is what we want to flush out.
- No Gateway, access via port-forward.
- Validate the smoke sequence above + a real upload + retrieval flow.

### Pass B — prod namespace (`ragflow`)

After Pass A is green, bump to:

- `replicas: 4` (api), HPA min 4 max 30
- `mariadb.replicas: 3` (Galera cluster)
- `redis.sentinelReplicas: 3`
- Gateway HTTPRoute + TLS via cert-manager + DNS

Pass A → B is ~30 min if A is clean. Direct prod is "see you Monday".

---

## Watchpoints (post-launch monitoring)

Things to alert on / dashboard once we're live:

| Signal | Why |
|--------|-----|
| `ragflow-api` pod restart rate > 0/h | Any restart is a real bug — there's no leak we know of |
| `/readyz` 503 sustained > 30s | DB or Redis blip; pages oncall |
| KEDA ScaledObject backlog (`pendingEntriesCount`) > 100 sustained | Workers can't keep up; check task-executor pod count + GPU saturation |
| Infinity disk > 80% | We're at single-replica, no compaction headroom |
| MariaDB Galera state ≠ `Synced` on any node | Split-brain risk |
| Cross-namespace traffic to `ragflow` from anywhere except `envoy-gateway` | NetworkPolicy bypass |

---

## Sandbox k8s — prod rollout runbook (`rag-new2`)

Rollout of the k8s sandbox provider (`code_exec` tool runs as one-shot Jobs in
the isolated `rag-sandbox` namespace, see `helm/ragflow/templates/sandbox/*.yaml`)
onto the alterai prod cluster. This section is the runbook written **before**
the operation (task-8-brief.md Step 1). The actual run (Step 2, done live with
the user) records real command output in the task report, not here.

Prerequisite reading: `poc/famat/README.md` (FAMAT POC context/data),
`docs/roadmap-multitenant-saas.md` (why sandbox exists), and the "Known traps"
+ "Pre-flight" sections above — the sandbox namespace inherits the same
cluster (Vault, storage class, NetworkPolicy caveats).

### Step 1 — Image

Trigger the manual CI job that builds the sandbox runtime image:

```bash
# GitLab UI → CI/CD → Pipelines → run job `kaniko_build_sandbox_python_prd`
# (tag = the release tag you're rolling out, e.g. v0.9.7)
```

Verify the image landed in Harbor:

```bash
kubectl run -it --rm imgcheck --image=alpine --restart=Never -- sh -c \
  "wget -qO- http://harbor.cylndata.cyllene.pro/v2/data/ragflow-sandbox-python/tags/list"
# Expect the release tag in the "tags" array.
```

### Step 2 — Values (verification, not authoring)

`values-alterai.yaml` already has the sandbox block committed:

```yaml
global:
  sandboxTokenAutomount: true
networkPolicy:
  enabled: false
sandbox:
  enabled: true
  image:
    repository: "harbor.cylndata.cyllene.pro/data/ragflow-sandbox-python"
    tag: "v0.9.7"
  networkPolicy:
    minioNamespace: "rag-new2"
    minioPodSelector:
      app: null            # deep-merge fix — do NOT remove, see inline comment
      v1.min.io/tenant: minio
```

**Correction (found during final review, task-8):** `sandbox.image`/
`sandbox.limits` in this values block are **not consumed by any template**
under `helm/ragflow/templates/sandbox/` — grep confirms only
`namespace.yaml`, `rbac.yaml`, `resourcequota.yaml`, and `networkpolicy.yaml`
read `.Values.sandbox.*`, and none of them touch `sandbox.image` or
`sandbox.limits`. They are **documentary only** — a human-readable record of
"what image/limits we intend to run," not the source of truth. Bumping
`sandbox.image.tag` here and syncing ArgoCD does **not** deploy the new
image. The image that actually runs is whatever `image` key is stored in
`system_settings.sandbox.k8s` (Step 6) — a Kubernetes `Job` manifest is built
per-execution by `K8sProvider` from that config at request time, not from a
Helm-rendered Deployment. **To roll out a new sandbox image: re-run the
Step 6 SQL (or use the admin UI once the `k8s` provider is registered
there) with the new tag — a values-file bump alone is a no-op for the
running image.**

This step is still worth doing as a **verification + tag bump** so the
values file stays an accurate record, and because the pull-secret sub-step
below is a real prerequisite:

1. If the release tag differs from what's committed, bump
   `sandbox.image.tag` in `values-alterai.yaml` and commit, for
   documentation purposes — remember this alone does not change the running
   image (see correction above); you still need Step 6.
2. **Harbor pull access.** Check whether the `data` Harbor project allows
   anonymous pull. If it does not, the `rag-sandbox` namespace needs its own
   copy of the pull secret — `rag-sandbox` is a separate namespace from
   `rag-new2` and does not inherit `rag-new2`'s imagePullSecrets:

   ```bash
   kubectl get secret harbor-pull-secret -n rag-new2 -o yaml \
     | sed 's/namespace: rag-new2/namespace: rag-sandbox/' \
     | kubectl apply -f -
   ```

   Then set `"image_pull_secret":"harbor-pull-secret"` in the Step 6 JSON
   payload so `K8sProvider` attaches it to every Job's `imagePullSecrets`.
   Skipping this when anonymous pull is off produces `ImagePullBackOff` on
   every sandbox Job.
3. Confirm the MinIO pod selector actually matches real pods (this was
   deduced from the chart/Tenant CR, not observed live — confirm in prod
   before trusting the NetworkPolicy egress rule it feeds):

   ```bash
   kubectl -n rag-new2 get pods -l v1.min.io/tenant=minio --show-labels
   # Expect at least one Running pod; confirm the label key/value match
   # networkPolicy.minioPodSelector above exactly.
   ```

   If the label differs, fix `minioPodSelector` in `values-alterai.yaml`
   (keep the `app: null` line as-is — it un-sets a values.yaml default key
   so Helm's deep-merge doesn't AND it together with `v1.min.io/tenant`,
   which would make the selector match nothing).

### Step 3 — Déploiement

Sync ArgoCD app `rag-new2`, then verify:

```bash
# Namespace + RBAC objects created
kubectl -n rag-sandbox get role,rolebinding,networkpolicy,resourcequota

# api / task-executor pods restarted with the SA token mounted
kubectl -n rag-new2 get pod <api-pod-name> \
  -o jsonpath='{.spec.automountServiceAccountToken}'
# → true
```

### Step 4 — NetworkPolicy CNI enforcement check (CRITICAL — new, not in original design)

`values-alterai.yaml` sets `networkPolicy.enabled: false` at the **platform**
level (ragflow ↔ mariadb/redis/infinity policies are off). The sandbox's own
default-deny NetworkPolicy template is gated by the separate
`sandbox.networkPolicy.enabled` flag, which is `true` only because it
defaults to `true` in `values.yaml` and `values-alterai.yaml` never overrides
it — so it is rendered today, but it is one values change away from silently
not being rendered at all. Either way, a rendered NetworkPolicy is only as
good as the CNI actually enforcing NetworkPolicy objects. This was never
observed live on `rag-new2` (deduced from the chart, not confirmed in prod)
— confirm before trusting sandbox isolation:

```bash
# Pick a service that should be UNREACHABLE from a sandbox Job, e.g. the
# ragflow-api ClusterIP service in rag-new2.
kubectl run np-test -n rag-sandbox --rm -it --image=busybox --restart=Never -- \
  wget -T3 -qO- http://<a-forbidden-service>.rag-new2.svc.cluster.local
# MUST fail (timeout/refused). If it returns a response body, the CNI is
# not enforcing NetworkPolicy and the sandbox default-deny is a no-op —
# stop the rollout and escalate before proceeding to Step 5.
```

Residual limitation to document regardless of outcome: DNS (port 53,
restricted to `kube-system`) is deliberately left open for name resolution,
which means DNS tunneling remains a theoretical exfiltration path out of
`rag-sandbox` even with a correctly enforced default-deny. Not a blocker for
this rollout, but note it for the next hardening pass.

Second residual limitation (found during final review, task-8): the
NetworkPolicy egress rule opens the **entire** MinIO service on port 9000
(`networkpolicy.yaml`'s `minioPodSelector`/`minioPort`), not just the
specific bucket a sandboxed recipe is meant to read. Any code running in a
sandbox Job can reach any bucket on that MinIO instance, including buckets
with an anonymous/public-read policy belonging to a *different* tenant —
the famat-poc bucket (Step 7) is itself public-read. This is a cross-tenant
data-exposure gap, not just a theoretical one; not a blocker for this
rollout (the sandbox provider's audience is currently the platform's own
recipes, not adversarial tenant code), but it must be closed — narrower
per-bucket policies or a proxy in front of MinIO — before the sandbox is
exposed to less-trusted workloads.

### Step 5 — Test RBAC négatif/positif

```bash
SA=system:serviceaccount:rag-new2:<nom-SA-api-rendu>
kubectl auth can-i create jobs -n rag-sandbox --as=$SA     # yes
kubectl auth can-i create pods -n rag-new2 --as=$SA        # no
kubectl auth can-i delete jobs -n rag-new2 --as=$SA        # no
```

### Step 6 — Config provider

Via the admin page (Sandbox settings) if the mgmt server is deployed;
otherwise direct SQL through the ephemeral MariaDB client pod (secret
`ragflow-mariadb-app`, db `ragflow`, user `ragflow` — see cluster access
notes):

```sql
-- Check the real schema first — columns and any create/update date columns
-- can differ from what's assumed below.
DESCRIBE system_settings;

-- `source` and `data_type` are NOT NULL with no default (see SystemSettings
-- in api/db/db_models.py) — omitting them fails with ERROR 1364 in strict
-- mode. Values follow the existing pattern in conf/system_settings.json
-- (sandbox.provider_type: source "variable"/data_type "string";
-- sandbox.self_managed: source "variable"/data_type "json" — sandbox.k8s
-- follows the same "json" pattern as the other sandbox.* provider configs).
INSERT INTO system_settings(name, source, data_type, value)
  VALUES ('sandbox.provider_type', 'variable', 'string', 'k8s')
  ON DUPLICATE KEY UPDATE value='k8s';
INSERT INTO system_settings(name, source, data_type, value)
  VALUES ('sandbox.k8s', 'variable', 'json',
  '{"namespace":"rag-sandbox","kubeconfig_path":"","image":"harbor.cylndata.cyllene.pro/data/ragflow-sandbox-python:<tag>","image_pull_secret":"harbor-pull-secret","memory_limit":"1Gi","cpu_limit":"1","timeout":120}')
  ON DUPLICATE KEY UPDATE value=VALUES(value);
```

`kubeconfig_path` is intentionally empty string — the provider runs
in-cluster and uses the mounted SA token (Step 3), not an external
kubeconfig. `timeout` is in seconds and must be ≥ the smoke-test latency
measured in Step 9 (~7s warm) plus margin for a cold image pull.
`image_pull_secret` must match the secret copied into `rag-sandbox` in
Step 2 — omit the key entirely (rather than leave it an empty string) if
the Harbor project allows anonymous pull and no secret was copied. **This
is also the payload to re-run whenever the sandbox image is bumped** — see
the Step 2 correction above; `values-alterai.yaml`'s `sandbox.image.tag`
does not drive the running image.

Debug note (no operational impact): pod log retrieval uses a raw HTTP call
against the kubelet/API server rather than the k8s client SDK's streaming
helper (fix from an earlier task in this chantier). Mentioned here only so
it's not mistaken for a bug if you're reading logs code while debugging a
stuck Job.

### Step 7 — Données FAMAT

Upload the CSV into `rag-new2`'s MinIO (bucket `famat-poc`, port-forward +
the boto3 script from the POC — see `poc/famat/README.md`). Note the
internal URL:

```
http://<svc-minio>.rag-new2.svc:9000/famat-poc/Payload-20260526.csv
```

### Step 8 — Canvas

Import `poc/famat/famat_agent_canvas.json` into the target tenant, replace
the prompt's `DATA_URL` with the internal MinIO URL from Step 7, and
configure the workspace LLM (qwen-code via LiteLLM, `is_tools: true` — now
settable through the admin API).

### Step 9 — Smoke final

Run the 3 recipes in chat from the platform. While a run executes:

```bash
kubectl -n rag-sandbox get jobs -w
# Jobs should appear and disappear as each recipe executes.
```

Expected latency (measured in kind during an earlier task): **~7s per
execution once the image is warm** on the node. On the very first run after
rollout (or after a node reschedule), add the time for the sandbox image to
pull — it's ~600MB, so budget extra time before declaring a hung Job.

Confirm the SPC cards render correctly in the chat for all 3 recipes.

### Step 10 — Rollback

```bash
# values-alterai.yaml
sandbox:
  enabled: false
global:
  sandboxTokenAutomount: false
# → commit, sync ArgoCD app `rag-new2`.
```

Returns to the pre-rollout state with no residue. The `rag-sandbox`
namespace itself can be left in place — leftover RBAC/NetworkPolicy/quota
objects in an empty namespace are inert and cheap to keep around for the
next attempt.

---

## Tool get_file (Files → sandbox)

The `get_file` tool enables sandboxed workflows to download files from the
platform's file storage via presigned URLs. The flow:

1. **Operator uploads** a file in Files UI → lands in MinIO under the workspace tenant.
2. **Agent requests** the file via the `get_file` tool (e.g., in a code recipe).
3. **get_file presigns** a 15-minute temporary URL pointing to the file's MinIO location.
4. **Sandbox fetches** the URL and processes the file locally (no streaming back to the API pod).

**Configuration:**

- `SANDBOX_PRESIGN_ENDPOINT` (env var): Controls the hostname:port in the presigned URL.
  - **In production (K8s)**: Leave **empty** (`""` or unset). The internal MinIO endpoint
    configured in `service_conf.yaml` (e.g., `http://ragflow-minio.rag-new2.svc:9000`) is
    already reachable from the sandbox namespace.
  - **In local dev** (native API pod + containerized sandbox): Set to the **machine's LAN IP**
    (e.g., `192.168.1.100:9000`), NOT `host.containers.internal`. The sandbox container cannot
    resolve the Docker host's virtual interface name; it needs a real routable IP.
  - **Format**: `host:port` **without** `http://` or `https://` scheme. MinIO SDK (minio-py)
    prepends the scheme based on TLS settings.

**TTL:** 15 minutes (hardcoded in `agent/tools/get_file.py`). If a recipe needs longer
(e.g., a large file download + slow analysis), increase `TTL_SECONDS` in the tool source
and rebuild the sandbox image.

**Implementation notes:**

- URL presigning happens in `rag/utils/minio_conn.py:RAGFlowMinio.get_presigned_url()`,
  after bucket/path remapping by the `@use_default_bucket` and `@use_prefix_path` decorators.
  The `endpoint_override` logic stays **inside** those decorators so single-bucket/prefix-path
  deployments sign the correct physical address.
- Do **not** move the override logic outside the decorators or create a standalone `Minio()` client
  in `get_file.py`. Upstream merges may nudge this pattern; resist them.

---

## Upstream merge — check this file

When we merge from upstream and any of these change, this doc lies:

- `Dockerfile` — capability and chown logic
- `api/asgi.py` — `/healthz` / `/readyz` definitions
- `rag/utils/redis_conn.py` — `host:port` parsing
- `api/db/services/task_service.py` — Redis lock keys, consumer groups
- `conf/service_conf.yaml.template` (root, not in repo) — overlay schema

Grep for these on every merge. If anything moved, update this doc and the
chart in the same commit.
