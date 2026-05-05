#!/usr/bin/env bash
# Tear down the local kind cluster — full destroy.
# Storage is gone (kind PVCs are emptyDir on the node), Vault dev creds gone,
# everything respawns clean on the next `up.sh`.
set -euo pipefail

CLUSTER_NAME="ragflow-local"
export KIND_EXPERIMENTAL_PROVIDER=podman

if kind get clusters | grep -qx "$CLUSTER_NAME"; then
  echo "[k8s-local] deleting kind cluster '$CLUSTER_NAME'"
  kind delete cluster --name "$CLUSTER_NAME"
else
  echo "[k8s-local] cluster '$CLUSTER_NAME' not found — nothing to do"
fi

# The Podman VM stays up so the next `up.sh` doesn't pay the VM-boot cost
# again. To shut it down too:
echo "[k8s-local] cluster gone. To stop the Podman VM (frees ~6 GB RAM):"
echo "  podman machine stop"
