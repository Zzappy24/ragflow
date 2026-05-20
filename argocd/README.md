# ArgoCD manifests

ArgoCD watches this directory and applies the manifests. Apply once,
manually, then ArgoCD takes over (GitOps).

## Order of installation

1. **Operators first** (`applicationset-operators.yaml`)
   ApplicationSet that fans out to one Application per operator: KEDA,
   mariadb-operator, redis-operator (OT-Container-Kit), minio-operator,
   external-secrets-operator. Each is a vanilla upstream Helm chart;
   we don't fork them.

2. **Then RAGFlow itself** (`application-ragflow-cyllene-prod.yaml`)
   Points at `helm/ragflow/` in this repo, with `values-cyllene-prod.yaml`
   merged on top of `values.yaml`. Sync wave is 10 so it always lands AFTER
   the operators' wave 0.

## Bootstrap

```bash
# From the cluster-admin workstation:
kubectl apply -f argocd/applicationset-operators.yaml
# Wait for all operators to be Synced + Healthy in the ArgoCD UI, then:
kubectl apply -f argocd/application-ragflow-cyllene-prod.yaml
```

## Gotcha — ApplicationSet sync ordering

`sync-wave` annotations are honored within an Application but not across
Applications. We rely on Kubernetes itself: the RAGFlow Application's CRs
(MariaDB, RedisReplication, etc.) will sit in `OutOfSync` state until the
operators' CRDs land. ArgoCD retries until everything is reconciled —
expect 2-5 minutes of yellow status during a fresh install.
