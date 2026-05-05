# Local testing — kind cluster on macOS / Podman

Validates the umbrella chart end-to-end on your laptop, using the same
operators as production (mariadb-operator, redis-operator OT, minio-operator,
KEDA, External Secrets). Sizing is minimal — read **What it WON'T test**
below before relying on the result.

## Prereqs

```bash
# 1. Install tools (one-shot, pinned versions are flexible)
brew install kind kubectl helm

# 2. Bump the Podman VM RAM — 8 GB minimum. Default 6 GB is too tight.
podman machine stop
podman machine set --memory 8192
podman machine start
```

## Bootstrap

```bash
bash scripts/k8s-local/up.sh
```

This does, in order:

1. Creates kind cluster `ragflow-local` (1 node, podman provider).
2. Builds the ragflow image **for linux/amd64** with podman, loads it
   into the cluster's containerd via `kind load`.
3. Installs operators sequentially:
   external-secrets → KEDA → mariadb-operator → ot-redis-operator → minio-operator
   (each `helm upgrade --install --wait`).
4. Installs Vault in dev mode (in-memory) and provisions the Vault paths
   the chart's ExternalSecret CRs expect (see `vault-init.sh`).
5. Installs the umbrella chart with `values-kind-dev.yaml` overrides.

**Cold start ~15-25 min** (image pulls dominate). Re-runs ~5-10 min.

## Watch progression

```bash
# Phase 1 — operators come up
watch -n 3 'kubectl get pods -A | grep -vE "Running|Completed"'

# Phase 2 — ragflow stack reconciles (CRs → operator-created pods)
kubectl -n ragflow get pods -w
```

Expected steady state in `ragflow` namespace (about 8-10 pods):

| Pod prefix | Source |
|---|---|
| `ragflow-api-…` | Deployment (1 replica) |
| `ragflow-task-executor-…` | Deployment (1 replica) |
| `ragflow-frontend-…` | Deployment (1 replica) |
| `ragflow-mariadb-0` | mariadb-operator → MariaDB CR |
| `ragflow-redis-…` | redis-operator → RedisReplication CR |
| `ragflow-redis-sentinel-…` | only if sentinelReplicas > 0 (off in kind-dev) |
| `minio-ss-0-0` | minio-operator → Tenant CR |
| `ragflow-infinity-0` | StatefulSet (custom, no operator) |

## Verify the app runs

```bash
# 1. The api should answer 401 (route reachable, auth required)
kubectl -n ragflow port-forward svc/ragflow-api 9081:80
curl -sw "\n%{http_code}\n" http://localhost:9081/api/v1/system/version
# expected: HTTP 401

# 2. Frontend over the kind-mapped NodePort (configured in kind-config.yaml)
open http://localhost:9080
# expected: ragflow login page
```

## What it WON'T test

| Cyllene prod feature | Reason it's skipped locally |
|---|---|
| Galera 3-replica failover | 1 replica only, the operator skips Galera bootstrap |
| Redis Sentinel failover | sentinelReplicas: 0 |
| MinIO erasure coding | 1 server / 1 volume (EC needs 4+) |
| Calico NetworkPolicy enforcement | kind uses kindnet, NPs aren't enforced |
| Envoy Gateway HTTPRoute | gatewayName "" disables HTTPRoute rendering |
| Rook-Ceph storage class behaviour | local-path-provisioner has different IO profile |
| vLLM chat / embed / rerank | No GPU locally — `vllm.*BaseUrl` points at port 0 |
| Vault production AppRole auth | Dev-mode root token (in-memory) |
| Cert-manager TLS issuance | No external hostname terminated |
| Prometheus ServiceMonitors | monitoring.enabled = false |
| HPA scale-up under real load | hpa.enabled = false (single replica) |
| KEDA Redis-stream scaling | keda.enabled = false |

## What it DOES test

✅ Helm chart renders + applies cleanly
✅ Image build, load, pull policy `Never` works
✅ Sub-charts wire env from ConfigMap + Secret correctly
✅ ExternalSecret CRs sync from Vault → ragflow-app-secrets
✅ MariaDB / Redis / MinIO operators reconcile their CRs into pods
✅ ragflow-api connects to MariaDB, Redis, MinIO, Infinity (probes pass)
✅ Frontend serves the static bundle, proxies /api correctly
✅ Pod restart on PodDisruption / `kubectl delete pod` (PDBs honored)
✅ Worker recycle (set `recycleAfterTasks: 5` to see it trip in seconds)

## Tear down

```bash
bash scripts/k8s-local/down.sh

# Free the Podman VM RAM too:
podman machine stop
```

## Common failures

**`Error: ImagePullBackOff` for `localhost/ragflow:kind-dev`**
The image didn't load into the kind node. Re-run:
```bash
kind load docker-image localhost/ragflow:kind-dev --name ragflow-local
```

**Infinity pod CrashLoopBackOff with `exec format error`**
The Infinity image is amd64-only. On Apple Silicon, kind on Podman uses
Rosetta to translate. If translation isn't enabled:
```bash
podman machine ssh -- 'rpm-ostree install rosetta || true'
```
Or temporarily set `infinity.enabled: false` in your kind-dev values to
test the rest of the stack first.

**Vault ExternalSecret stuck in `SecretSyncedError`**
Check ESO can reach Vault:
```bash
kubectl -n external-secrets logs deploy/external-secrets
kubectl -n vault logs vault-0
```
Re-run `bash scripts/k8s-local/vault-init.sh` to reseed.

**MariaDB stuck `bootstrap`**
The operator first-boot can be slow (30-60s). If it stays Pending > 5min,
inspect `kubectl -n ragflow describe pod ragflow-mariadb-0`. Most often:
PVC isn't bound (StorageClass mismatch — check `values-kind-dev.yaml`).
