#!/usr/bin/env bash
# Provision Vault dev mode with the secrets the chart's ExternalSecret CRs
# expect. Vault dev token is hardcoded as `root` (set in up.sh's helm install).
#
# In real prod, this is done by the infra team via Vault policies + AppRole;
# we never commit credentials. The secrets here are throwaway dev values.
set -euo pipefail

NS="vault"
VAULT_TOKEN="root"

# Wait for the vault-0 pod to be Ready.
echo "[vault-init] waiting for vault-0 to be Ready"
kubectl -n "$NS" wait --for=condition=ready pod/vault-0 --timeout=120s

# Convenience: run a Vault CLI command inside the pod with the dev token.
vault_cmd() {
  kubectl -n "$NS" exec -i vault-0 -- env VAULT_TOKEN="$VAULT_TOKEN" vault "$@"
}

echo "[vault-init] enabling kv-v2 at secret/ (idempotent)"
vault_cmd secrets enable -path=secret kv-v2 2>/dev/null || true

echo "[vault-init] writing test secrets at secret/ragflow/cyllene-prod/*"
vault_cmd kv put secret/ragflow/cyllene-prod/app \
  ADMIN_JWT_SECRET="dev-jwt-secret-not-for-prod" \
  RSA_PASSPHRASE="Welcome" \
  RSA_PRIVATE_KEY="$(cat <<'EOF'
-----BEGIN RSA PRIVATE KEY-----
MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu
KUpRKfFLfRYC9AIKjbJTWit+CqvjWYzvQwECAwEAAQJAIJLixBy2qpFoS4DSmoEm
o3qGy0t6z09AIJtH+5OeRV1be+N4cDYJKffGzDDlk4EBPK+8WEihrgbuvJ45XveJ
WQIhAJWPFY4wxa9k9pT6Z+NRQKlsqVNOqfUJIWlepCbSDvbDAiEA4sVplchqwqEk
4iOZ1rJkfACPEZ4j8N1/d0iXa3aXKJsCIBaPuTnLsx8OHsapkcU4k6pxiLkWPBEy
fkRAYwFy0w8DAiBKWgX5kEHLpJLaJIIbMJpzKUYzjJmJk9G2y2K6+5KXmwIhAJgQ
0BqMmDBWxMhk0ZZQjmHbqgjk5PDfSuxlkRIcYULv
-----END RSA PRIVATE KEY-----
EOF
)"

vault_cmd kv put secret/ragflow/cyllene-prod/mariadb-root \
  password="dev-mariadb-root-password"

vault_cmd kv put secret/ragflow/cyllene-prod/mariadb-app \
  password="dev-mariadb-app-password"

vault_cmd kv put secret/ragflow/cyllene-prod/redis-auth \
  REDIS_PASSWORD="dev-redis-password"

vault_cmd kv put secret/ragflow/cyllene-prod/minio-root \
  accesskey="dev-minio-access-key" \
  secretkey="dev-minio-secret-key"

# Harbor pull is unused locally (we kind-load images), but the
# ExternalSecret references it — provide a no-op dockerconfig so ESO
# doesn't loop in error.
vault_cmd kv put secret/ragflow/cyllene-prod/harbor-pull \
  .dockerconfigjson='{"auths":{}}'

# -----------------------------------------------------------------------------
# Configure External Secrets Operator to read from this Vault dev mode.
# This creates the ClusterSecretStore that the chart references.
# -----------------------------------------------------------------------------
echo "[vault-init] creating Vault token Secret + ClusterSecretStore for ESO"
kubectl create namespace external-secrets 2>/dev/null || true

# Inline Vault token (dev only — in prod this is an AppRole approle Secret).
kubectl -n external-secrets delete secret vault-token 2>/dev/null || true
kubectl -n external-secrets create secret generic vault-token \
  --from-literal=token="$VAULT_TOKEN"

# ClusterSecretStore that the chart's ExternalSecret CRs reference.
cat <<EOF | kubectl apply -f -
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: vault-cyllene
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "secret"
      version: v2
      auth:
        tokenSecretRef:
          name: vault-token
          namespace: external-secrets
          key: token
EOF

echo "[vault-init] done — secrets are at secret/ragflow/cyllene-prod/*"
