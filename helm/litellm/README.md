# LiteLLM — data plane du produit Code (alterai prod)

Chart **officiel** BerriAI (`helm/litellm-helm` sur `main`, publié en OCI) — mono-déploiement.
**Ne pas** utiliser le chart de `litellm_internal_staging` (split gateway/backend/ui, non releasé).

## Installation

```bash
NS=rag-new2

# 1. Secrets (AVANT l'install — jamais de mot de passe dans les values)
kubectl -n $NS create secret generic litellm-master-key-secret \
  --from-literal=master-key="sk-$(openssl rand -hex 32)"
kubectl -n $NS create secret generic litellm-postgres-secret \
  --from-literal=password="$(openssl rand -hex 24)" \
  --from-literal=postgres-password="$(openssl rand -hex 24)"

# 2. Compléter les TODO(deploy)/TODO(pricing) de values-alterai.yaml
#    (api_base du vLLM H200, nom du modèle servi, €/token)

# 3. Chart
helm install litellm oci://ghcr.io/berriai/litellm-helm \
  -n $NS -f values-alterai.yaml

# 4. Exposition publique /v1 uniquement
kubectl apply -f httproute.yaml
```

## Env à câbler sur le mgmt-backend (subchart ragflow-management-backend)

| Var | Valeur |
|---|---|
| `LITELLM_BASE_URL` | `http://litellm.rag-new2.svc.cluster.local:4000` |
| `LITELLM_MASTER_KEY` | depuis le Secret `litellm-master-key-secret` (envFrom/secretKeyRef) |
| `ADMIN_CODE_GATEWAY_PUBLIC_URL` | `https://litellm.cyllene.cloud/v1` |

(+ les prérequis SMTP/claim de `management/DEPLOYMENT_CHECKLIST.md` §5.)

## Règles de vie

- **Bump d'image = suite d'intégration d'abord** : repointer le tag dans
  `docker/litellm-test/docker-compose.yml`, puis
  `LITELLM_TEST_URL=http://localhost:4000 pytest test/multitenant/test_code_litellm_integration.py`.
  Validé : `main-v1.74.0-stable`, `v1.91.1` (drift connu : budget /v1 400→429).
- **`replicaCount > 1` ⇒ activer Redis** (cohérence budgets inter-pods) — et
  le rate-limiter in-process du mgmt-backend a la même contrainte (checklist §5).
- La grille tarifaire vit dans `proxy_config.model_list.model_info` — toute
  modification de prix passe par une PR, pas par l'UI LiteLLM (non exposée).
- Ajouter un modèle (ex. Gemma) = une entrée `model_list` de plus ; même
  `model_name` sur deux entrées = load-balancing, `model_name` différent =
  catalogue (contrôlé par team via `code_team.model_access`).
