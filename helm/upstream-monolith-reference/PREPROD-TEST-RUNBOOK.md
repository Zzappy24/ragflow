# Pass A — monolithic chart on preprod (no operators)

First-touch deploy on the preprod cluster to validate that the fork image
actually runs end-to-end. **Not** the production target — see
`helm/ragflow/` + `argocd/application-ragflow-cyllene-prod.yaml` for the
Cyllene operator-based chart.

What this deploys: 1 RAGFlow Deployment + 4 StatefulSets (mysql, redis,
minio, infinity), all plain pods, no operators, no Vault, no Gateway.
Namespace: `ragflow-test`. Access via `kubectl port-forward`.

---

## Step 0 — repo side (laptop)

**0.1** On `dev`, confirm the chart commit is present:
```bash
git log --oneline | grep '64a39a34f'
# → 64a39a34f chore(helm): monolith chart preprod test overlay + ArgoCD Application
```

**0.2** Once the Harbor image push completes, fill in the two placeholders
in `helm/upstream-monolith-reference/values-preprod-test.yaml`:
```yaml
ragflow:
  image:
    repository: "harbor.cyllene.local/ragflow/ragflow"   # real Harbor path
    tag: "<the-pushed-tag>"                              # e.g. dev-2026-05-25
```

**0.3** Verify the ArgoCD Application matches the GitLab repo URL ArgoCD
has credentials for — `argocd/application-ragflow-monolith-test.yaml`:
```yaml
repoURL: git@gitlab.cyllene.local:platform/ragnarok.git   # adjust if needed
targetRevision: dev                                       # branch to follow
```

**0.4** Commit and push:
```bash
git add helm/upstream-monolith-reference/values-preprod-test.yaml
git commit -m "chore(helm): set Harbor image ref for monolith-test deploy"
git push <remote> dev
```

---

## Step 1 — cluster prep (preprod cluster)

**1.1** Namespace:
```bash
kubectl create namespace ragflow-test
```

**1.2** Harbor pull secret in that namespace:
```bash
kubectl create secret docker-registry harbor-pull \
  --docker-server=<HARBOR_HOST> \
  --docker-username=<USER> \
  --docker-password=<PASS> \
  -n ragflow-test
```

**1.3** Confirm a default StorageClass exists:
```bash
kubectl get sc
# One line must show "(default)". Otherwise PVCs stay Pending —
# add `storage.className` overrides per service in the overlay.
```

---

## Step 2 — trigger the deployment (preprod cluster)

**2.1** Apply the ArgoCD Application:
```bash
kubectl apply -f argocd/application-ragflow-monolith-test.yaml
```

**2.2** In ArgoCD UI: open `ragflow-monolith-test` → **Sync** (manual is
intentional for this first deploy). Watch the resource tree.

---

## Step 3 — watch the rollout (~5–10 min)

**3.1** Follow pods:
```bash
kubectl -n ragflow-test get pods -w
```

**3.2** Expected order:
1. PVCs `Bound`
2. `ragflow-mysql-0`, `ragflow-redis-0`, `ragflow-minio-0`,
   `ragflow-infinity-0` reach `Running 1/1`
3. `ragflow-*` (app) boots in 30–90s; one or two restarts are normal
   while MySQL/Redis come up. Then `Running 1/1`.

**3.3** If a pod stalls > 5 min in `0/1` or `CrashLoopBackOff`:
```bash
kubectl -n ragflow-test describe pod <pod>
kubectl -n ragflow-test logs <pod> --tail=100
kubectl -n ragflow-test logs <pod> --previous   # if it crashed
```

---

## Step 4 — smoke test (laptop)

**4.1** Frontend (full UI):
```bash
kubectl -n ragflow-test port-forward svc/ragflow 8080:80
# → http://localhost:8080
```
Login page should render, then create a superuser, log in, create a KB,
upload a PDF, run an agent.

**4.2** Direct API (health probes):
```bash
kubectl -n ragflow-test port-forward svc/ragflow-api 9380:80
curl http://localhost:9380/healthz   # → {"ok":true}
curl http://localhost:9380/readyz    # → {"ok":true}
```

---

## Step 5 — multi-tenant functional test (the real fork check)

**5.1** Superuser login, create 2 workspaces (Bodemer + demo).
**5.2** Create 2 users in Bodemer: one `viewer`, one `editor`.
**5.3** Upload a PDF into a Bodemer KB.
**5.4** Verify that:
- The Bodemer viewer sees the KB (read-only).
- The Bodemer editor can upload + chunk.
- No user from the other workspace can see Bodemer's docs.

---

## Step 6 — cleanup (when you want to restart fresh)

**6.1** Soft cleanup (keep PVCs): sync-delete in ArgoCD UI.

**6.2** Full reset (data loss accepted):
```bash
kubectl -n ragflow-test delete pvc --all
kubectl delete namespace ragflow-test
```

---

## Troubleshooting cheat sheet

| Symptom | Likely cause |
|---|---|
| Pods `ImagePullBackOff` | Wrong tag/repo in overlay, or `harbor-pull` secret missing/incorrect |
| PVCs `Pending` | No default StorageClass — add `className` overrides in `values-preprod-test.yaml` |
| ragflow pod `CrashLoopBackOff` at boot | 99% of the time MySQL/Redis not ready yet — let it retry 2–3 min. Otherwise `kubectl logs -p` for the previous crash. |
| `/readyz` returns 503 | DB or Redis unreachable — check stateful Services + pods |
| Login page blank | nginx upstream not resolved — check `ragflow-api` Service has a healthy Endpoint |

---

## Quick triage hand-off

If something goes wrong, copy these three commands' output:
```bash
kubectl -n ragflow-test get pods,pvc,svc
kubectl -n ragflow-test logs <failing-pod> --tail=80
# + the ArgoCD event if the app is OutOfSync
```
