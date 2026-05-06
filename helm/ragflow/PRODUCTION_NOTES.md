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

## Upstream merge — check this file

When we merge from upstream and any of these change, this doc lies:

- `Dockerfile` — capability and chown logic
- `api/asgi.py` — `/healthz` / `/readyz` definitions
- `rag/utils/redis_conn.py` — `host:port` parsing
- `api/db/services/task_service.py` — Redis lock keys, consumer groups
- `conf/service_conf.yaml.template` (root, not in repo) — overlay schema

Grep for these on every merge. If anything moved, update this doc and the
chart in the same commit.
