#!/usr/bin/env bash
# =============================================================================
#  Bootstrap a local kind cluster running RAGFlow's Cyllene chart.
# -----------------------------------------------------------------------------
#  Cold path (~15-25 min): kind create + image pulls + operators + secrets +
#  ragflow chart. Re-runs (warm caches) ~5-10 min.
#
#  Constraints we accept locally:
#    - 1 replica everywhere (no HA tested)
#    - kindnet CNI (NetworkPolicies disabled)
#    - kind local-path-provisioner (RWO storage, not rook-ceph)
#    - Vault in dev mode (in-memory, secrets reset on Vault restart)
#    - No Envoy Gateway (use NodePort + kubectl port-forward instead)
#
#  See helm/ragflow/LOCAL-TESTING.md for the full step-by-step.
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"

CLUSTER_NAME="ragflow-local"
KIND_CONFIG="$REPO/scripts/k8s-local/kind-config.yaml"

# Friendly logger.
log() { echo -e "\n\033[1;36m[k8s-local] $*\033[0m"; }

# -----------------------------------------------------------------------------
# 1. Pre-flight — check tools + Podman VM RAM.
# -----------------------------------------------------------------------------
log "pre-flight checks"
for tool in kind kubectl helm podman; do
  command -v "$tool" >/dev/null || { echo "  missing: $tool"; exit 1; }
done

# kind via podman provider — required on Mac without Docker Desktop.
export KIND_EXPERIMENTAL_PROVIDER=podman

vm_mem=$(podman machine info --format '{{ "{{ .Host.Arch }}" }} {{ "{{ .Host.CurrentMachine.Resources.Memory }}" }}' 2>/dev/null | awk '{print $2}')
if [ -n "${vm_mem:-}" ] && [ "$vm_mem" -lt 6144 ]; then
  echo "  Podman VM has only ${vm_mem}MiB — recommend 8192+. Run:"
  echo "    podman machine stop && podman machine set --memory 8192 && podman machine start"
  exit 1
fi

# -----------------------------------------------------------------------------
# 2. Create the kind cluster (idempotent).
# -----------------------------------------------------------------------------
if kind get clusters | grep -qx "$CLUSTER_NAME"; then
  log "kind cluster '$CLUSTER_NAME' already exists — reusing"
else
  log "creating kind cluster '$CLUSTER_NAME'"
  kind create cluster --name "$CLUSTER_NAME" --config "$KIND_CONFIG"
fi

kubectl config use-context "kind-$CLUSTER_NAME"

# -----------------------------------------------------------------------------
# 3. Build + load the ragflow image into the kind cluster's containerd.
#    `kind load` short-circuits the Harbor registry — the cluster sees the
#    image as if it had been pulled.
# -----------------------------------------------------------------------------
RAGFLOW_IMAGE="${RAGFLOW_IMAGE:-localhost/ragflow:kind-dev}"

if ! podman image exists "$RAGFLOW_IMAGE" 2>/dev/null; then
  log "building ragflow image (cold ~5-10 min, hot ~30s)"
  podman build --platform linux/amd64 -t "$RAGFLOW_IMAGE" -f "$REPO/Dockerfile" "$REPO"
fi

log "loading ragflow image into kind"
# kind load docker-image works with podman when KIND_EXPERIMENTAL_PROVIDER=podman
kind load docker-image "$RAGFLOW_IMAGE" --name "$CLUSTER_NAME"

# -----------------------------------------------------------------------------
# 4. Install operators (Helm, sequential to keep RAM in check).
# -----------------------------------------------------------------------------
log "adding helm repos"
helm repo add external-secrets https://charts.external-secrets.io 2>/dev/null || true
helm repo add kedacore https://kedacore.github.io/charts 2>/dev/null || true
helm repo add mariadb-operator https://helm.mariadb.com/mariadb-operator 2>/dev/null || true
helm repo add ot-helm https://ot-container-kit.github.io/helm-charts 2>/dev/null || true
helm repo add minio-operator https://operator.min.io 2>/dev/null || true
helm repo add hashicorp https://helm.releases.hashicorp.com 2>/dev/null || true
helm repo update

log "installing external-secrets-operator"
helm upgrade --install external-secrets external-secrets/external-secrets \
  --namespace external-secrets --create-namespace \
  --version 0.10.7 --set installCRDs=true --wait --timeout 5m

log "installing KEDA"
helm upgrade --install keda kedacore/keda \
  --namespace keda --create-namespace \
  --version 2.16.1 --wait --timeout 5m

log "installing mariadb-operator CRDs (separate chart — must precede the operator)"
# Same pattern as ArgoCD applicationset-operators.yaml (sync-wave 0).
# Without this the operator main controller crash-loops on
#   "no matches for kind MariaDB in version k8s.mariadb.com/v1alpha1"
# because the upstream chart sets crds.enabled=false by default (deleting
# the operator would otherwise cascade-delete every MariaDB instance).
helm upgrade --install mariadb-operator-crds mariadb-operator/mariadb-operator-crds \
  --namespace mariadb-operator --create-namespace \
  --version 0.37.1 --wait --timeout 2m

log "installing mariadb-operator (no webhook for local — skip cert-manager dep)"
helm upgrade --install mariadb-operator mariadb-operator/mariadb-operator \
  --namespace mariadb-operator --create-namespace \
  --version 0.37.1 \
  --set webhook.cert.certManager.enabled=false \
  --set webhook.cert.certManager.disabled=true \
  --wait --timeout 5m

log "installing ot-redis-operator"
helm upgrade --install redis-operator ot-helm/redis-operator \
  --namespace redis-operator --create-namespace \
  --version 0.18.1 --wait --timeout 5m

log "installing minio-operator"
helm upgrade --install minio-operator minio-operator/operator \
  --namespace minio-operator --create-namespace \
  --version 6.0.4 --wait --timeout 5m

# -----------------------------------------------------------------------------
# 5. Vault dev mode + provision secrets + ClusterSecretStore.
# -----------------------------------------------------------------------------
log "installing Vault (dev mode, in-memory)"
helm upgrade --install vault hashicorp/vault \
  --namespace vault --create-namespace \
  --version 0.28.1 \
  --set "server.dev.enabled=true" \
  --set "server.dev.devRootToken=root" \
  --set "injector.enabled=false" \
  --wait --timeout 5m

bash "$REPO/scripts/k8s-local/vault-init.sh"

# -----------------------------------------------------------------------------
# 6. Install ragflow umbrella chart.
# -----------------------------------------------------------------------------
log "installing ragflow umbrella chart"
helm upgrade --install ragflow "$REPO/helm/ragflow" \
  --namespace ragflow --create-namespace \
  --values "$REPO/helm/ragflow/values.yaml" \
  --values "$REPO/helm/ragflow/values-kind-dev.yaml" \
  --set "global.imageRegistry=localhost" \
  --set "global.imageRepository=ragflow" \
  --set "global.imageTag=kind-dev" \
  --wait --timeout 10m

log "DONE — quick verification:"
echo
echo "  kubectl -n ragflow get pods"
echo "  kubectl -n ragflow port-forward svc/ragflow-frontend 9080:80"
echo "  open http://localhost:9080"
echo
echo "  Tear down with: scripts/k8s-local/down.sh"
