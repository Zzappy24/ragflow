# Provider sandbox `k8s` — Jobs Kubernetes éphémères durcis

**Date** : 2026-08-13
**Statut** : validé pour plan d'implémentation
**Objectif business** : rendre le tool `code_exec` (agents RAGFlow) opérationnel sur le cluster de prod `rag-new2` — prérequis au déploiement de l'agent FAMAT (et de tout futur agent data analyst) sur la plateforme. Le sandbox par défaut de RAGFlow repose sur un socket Docker inexistant sur un cluster containerd ; le DinD privilégié est écarté (anti-pattern sécurité indéfendable en audit client aéro).

## Contexte

- Le POC FAMAT (spec `2026-08-11-famat-drift-agent-poc-design.md`, branche `famat-poc`) a validé la chaîne complète agent → `code_exec` → sandbox → artefacts → chat, en local via le provider `self_managed` (executor-manager + Docker/Podman).
- L'architecture provider de RAGFlow (`agent/sandbox/providers/`) est une abstraction propre : contrat `SandboxProvider` (`initialize` / `create_instance` / `execute_code` / `health` / `get_config_schema`), registre = dict dans `agent/sandbox/client.py:83-88`, config globale en base (`system_settings` : `sandbox.provider_type` + `sandbox.<type>`), formulaire auto-généré dans l'admin UI Sandbox settings à partir de `get_config_schema()`.
- Le protocole d'exécution est réutilisable tel quel : `result_protocol.build_python_wrapper()` enveloppe le code utilisateur (`def main()`, `arguments`, collecte de `artifacts/`) et émet un JSON structuré ; `self_managed` remonte les artefacts en base64 dans `metadata["artifacts"]`, que `agent/tools/code_exec.py::_upload_artifacts` pousse vers MinIO → pièces jointes chat.
- L'analyseur AST (`agent/sandbox/executor_manager/services/security.py::SecurePythonAnalyzer`, bannit `socket`, `os`, `subprocess`…) est conservé en défense de profondeur.
- Le chart Helm (`helm/ragflow/`) n'a aucun composant sandbox. CI GitLab existante : jobs kaniko par image → `harbor.cylndata.cyllene.pro/${HARBOR_PROJECT}/<image>:${IMAGE_TAG}`.

## Architecture

```
code_exec (API server / task executor, ns rag-new2)
   │  agent.sandbox.client → provider_type=k8s (system_settings)
   ▼
K8sProvider (agent/sandbox/providers/k8s.py)
   │ 1. SecurePythonAnalyzer (AST) — rejet -999 "Code is unsafe"
   │ 2. build_python_wrapper(code, args) — contrat def main() inchangé
   │ 3. create Job (ns rag-sandbox, pod durci, code via env var b64)
   │ 4. watch Job → complétion | activeDeadlineSeconds
   │ 5. read pod logs → JSON structuré (result + artifacts b64)
   │ 6. delete Job (foreground) ; ttlSecondsAfterFinished en ceinture
   ▼
ExecutionResult(stdout, stderr, exit_code, metadata={"artifacts": [...]})
   │
   ▼ (chaîne existante, inchangée)
code_exec._upload_artifacts → MinIO → pièces jointes chat
```

### Composant 1 — `agent/sandbox/providers/k8s.py` (nouveau, ~350 lignes)

- **`initialize(config)`** : import **lazy** de la lib `kubernetes` (les environnements sans elle ne cassent pas à l'import du module) ; config in-cluster par défaut, `kubeconfig_path` optionnel (debug local/kind) ; vérifie l'accès (list jobs dans le namespace) et retourne False avec log explicite sinon.
  Clés de config (defaults) : `namespace` (`rag-sandbox`), `image` (`harbor.cylndata.cyllene.pro/<projet>/ragflow-sandbox-python:<tag>`), `memory_limit` (`1Gi`), `cpu_limit` (`1`), `timeout` max (`120` s), `kubeconfig_path` (`""` = in-cluster), `image_pull_secret` (`""`), `node_selector` (`{}`), `ttl_seconds_after_finished` (`300`).
- **`execute_code(instance_id, code, language, timeout, arguments)`** :
  - `language != "python"` → `ExecutionResult` d'erreur explicite « language non supporté par le provider k8s (v1 : python uniquement) ».
  - Analyse AST (mêmes règles et même code d'erreur `-999` que l'executor-manager).
  - Job : nom `sbx-<uuid8>`, labels (`app=ragflow-sandbox`, `ragflow.io/component=code-exec`), pod : `restartPolicy: Never`, `backoffLimit: 0`, `activeDeadlineSeconds=min(timeout, timeout_max)`, `automountServiceAccountToken: false`, `securityContext` : `runAsNonRoot: true`, `runAsUser/runAsGroup: 65534`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault` ; volumes `emptyDir` (medium Memory) montés sur `/workspace` (workdir) et `/tmp` ; `resources.limits` = config ; le wrapper (code + arguments encodés base64) passe par une **env var**, la commande du conteneur le décode et l'exécute (`python -c 'import base64,os;exec(...)'` — détail exact au plan). Taille bornée (< 1 Mo, limite etcd) — largement suffisant (recettes ≈ 1 Ko).
  - Attente : watch (ou polling 500 ms) sur le Job ; états gérés : succès, échec, deadline dépassée, `ImagePullBackOff`/`ErrImagePull` (erreur explicite « image inaccessible depuis le cluster »), pod `Pending` > 60 s (erreur « scheduling impossible » avec les events du pod).
  - Logs du pod : cap à 10 Mo (au-delà → erreur explicite « sortie trop volumineuse ») ; parsing via `extract_structured_result` du `result_protocol` ; artefacts base64 placés dans `metadata["artifacts"]` (même clé/format que `self_managed`).
  - Nettoyage : delete Job propagation Foreground dans un `finally` ; `ttlSecondsAfterFinished` en filet si le process meurt avant.
- **`health()`** : list jobs (limit 1) dans le namespace — utilisé par le bouton « Test connection » de l'admin.
- **`get_config_schema()`** : expose les clés de config ci-dessus pour le formulaire admin.

### Composant 2 — enregistrement et dépendance

- `agent/sandbox/client.py` : `"k8s": K8sProvider` dans le dict + import — marqueur `CUSTOM B2B SaaS — provider sandbox k8s` + entrée dans le tableau « Custom files to watch » de CLAUDE.md (fichier upstream).
- `pyproject.toml` : dépendance `kubernetes` (client officiel). Import lazy dans le provider uniquement.
- Réutilisation de `SecurePythonAnalyzer` par import direct depuis `executor_manager/services/security.py` **si** le module s'importe sans tirer FastAPI ; sinon, extraction de la classe vers un module partagé `agent/sandbox/security.py` (l'executor-manager l'importe depuis là — un seul exemplaire, pas de copie).

### Composant 3 — Helm (`helm/ragflow/`)

Nouveaux templates dans le chart principal (pas de subchart — 4 objets), sous flag `sandbox.enabled` :

| Objet | Rôle |
|---|---|
| `Namespace rag-sandbox` | flag `sandbox.namespace.create` (défaut true) |
| `Role` (ns rag-sandbox) | `jobs`: create/get/list/watch/delete ; `pods`: get/list ; `pods/log`: get — rien d'autre |
| `RoleBinding` | lie le Role aux ServiceAccounts `ragflow-api` et `ragflow-task-executor` de `rag-new2` |
| `NetworkPolicy` | default deny ingress+egress ; egress autorisé : DNS (kube-dns) + service MinIO de `rag-new2` (port 9000) ; liste extensible par values (future base FAMAT) |
| `ResourceQuota` | borne globale du namespace (défaut : 8 Gi RAM, 8 CPU, 20 pods) |

Values : `sandbox.image.{repository,tag}`, `sandbox.limits.{memory,cpu}`, `sandbox.quota.*`, `sandbox.networkPolicy.extraEgress[]`. Convention existante du chart respectée (registry paramétré, jamais hardcodé).

### Composant 4 — CI GitLab

Job kaniko `build-sandbox-python` sur le modèle des jobs mgmt (avec cache) : contexte `agent/sandbox/sandbox_base_image/python/` → `harbor.cylndata.cyllene.pro/${HARBOR_PROJECT}/ragflow-sandbox-python:${IMAGE_TAG}`. L'image embarque déjà duckdb, pymysql, matplotlib, requests et `famat_recipes` (copie cuite, resync documenté dans `poc/famat/README.md`).

### Configuration runtime

Via l'admin UI Sandbox settings existante (routes `/api/v1/admin/sandbox/*`) : `provider_type=k8s` + config JSON — persistée en `system_settings`, aucun redémarrage requis (`reload_provider()` à chaque exécution).

## Gestion d'erreurs (contrat)

Toute défaillance produit un `ExecutionResult` avec `exit_code != 0` et un `stderr` actionnable (« image inaccessible », « RBAC insuffisant », « deadline dépassée », « sortie > 10 Mo », « language non supporté ») — jamais d'exception non typée qui remonte au canvas. `initialize` qui échoue → `SandboxProviderConfigError` (même sémantique que `local`/`ssh` : erreur nette côté tool, pas de fallback silencieux vers l'ancien chemin HTTP).

## Sécurité (résumé auditable)

- Pod : non-root (65534), rootfs read-only, zéro capability, seccomp RuntimeDefault, pas de token ServiceAccount, tmpfs mémoire uniquement, limites CPU/RAM, durée bornée.
- Namespace dédié : RBAC minimal scopé, NetworkPolicy default-deny (egress MinIO+DNS seulement), ResourceQuota global.
- Code : analyse AST avant lancement (imports dangereux bannis) — défense en profondeur, pas la barrière principale.
- Différé consciemment : RuntimeClass gVisor (nécessite installation `runsc` sur les nodes — le jour venu : une ligne `runtimeClassName` dans values), quotas de compute par tenant (pendant sandbox des quotas de tokens LLM — pertinent quand plusieurs clients utiliseront `code_exec` en prod), nodejs (aucun canvas ne l'utilise ; erreur explicite en attendant ; wrapper JS déjà présent dans `result_protocol`).

## Tests

1. **Unitaires** (client `kubernetes` mocké) : manifest du Job généré — assertions sur CHAQUE champ du securityContext, les volumes, les limites, l'activeDeadline ; parsing des logs (succès, artefacts, sortie tronquée, JSON invalide) ; rejets AST ; language nodejs ; erreurs typées (ImagePullBackOff simulé, RBAC refusé).
2. **Intégration kind** (tooling kind déjà en place — cf. validation Helm 2026-05-05) : cluster kind + namespace + RBAC via le chart, image chargée (`kind load docker-image`), exécution réelle d'une recette FAMAT avec artefact SVG — gated derrière `K8S_SANDBOX_TEST=1`.
3. **Smoke cluster** : « Test connection » admin + une exécution de recette sur `rag-new2` après déploiement ArgoCD.

## Critères de réussite

- Depuis un chat agent sur le cluster : les 3 recettes FAMAT s'exécutent avec cartes SPC jointes, sans aucun pod privilégié ni socket Docker nulle part.
- Latence d'une exécution ≤ 15 s hors calcul (scheduling + démarrage pod).
- `kubectl auth can-i` depuis le SA ragflow-api : create jobs dans `rag-sandbox` = yes ; create pods dans `rag-new2` = no (le RBAC ne fuit pas).
- Les tests unitaires passent sans cluster ; l'intégration kind passe avec.

## Hors scope v1

nodejs, pool de pods chauds (optimisation latence si le +3-8 s de scheduling gêne à l'usage), gVisor, quotas sandbox par tenant, exécution multi-fichiers/sessions persistantes.
