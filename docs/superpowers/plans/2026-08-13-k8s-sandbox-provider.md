# K8s Sandbox Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provider sandbox `k8s` (Jobs Kubernetes éphémères durcis) déployé et fonctionnel en prod sur le cluster `rag-new2` — le tool `code_exec` des agents RAGFlow tourne sans socket Docker ni pod privilégié.

**Architecture:** Nouveau `K8sProvider` implémentant le contrat `SandboxProvider` existant : AST scan (module partagé) → wrapper `result_protocol` → Job K8s durci dans le namespace dédié `rag-sandbox` → logs du pod = JSON structuré (résultat + artefacts base64) → `ExecutionResult` compatible avec la chaîne artefacts→MinIO→chat existante. Helm : 5 objets (Namespace, Role, RoleBinding, NetworkPolicy, ResourceQuota) + automount du token SA. CI : job kaniko → Harbor.

**Tech Stack:** Python (`kubernetes` client officiel, import lazy), Helm (chart umbrella existant), GitLab CI kaniko, kind pour l'intégration locale.

## Global Constraints

- **Aucun secret dans les fichiers commités** (credentials Harbor/MinIO/DB : variables CI et secrets K8s existants uniquement).
- **Fichiers upstream modifiés** → marqueur greppable `CUSTOM B2B SaaS — provider sandbox k8s` + ligne dans le tableau « Custom files to watch » de CLAUDE.md. Fichiers upstream à NE PAS toucher : `agent/sandbox/executor_manager/**` (sauf rien — le module partagé ne le modifie pas).
- **Commits sans trailer `Co-Authored-By`.**
- **Les tests unitaires passent SANS cluster ni lib kubernetes installée par accident** : `uv run --with kubernetes` pour les tests provider ; le module `k8s.py` doit s'importer sans la lib (imports lazy).
- Contrat inchangé côté canvas : `def main()`, `arguments`, `artifacts/` — les recettes FAMAT tournent sans modification.
- Nom d'image prod : `harbor.cylndata.cyllene.pro/data/ragflow-sandbox-python:<tag>` (projet Harbor `data`, cf. `.gitlab-ci.yml` variables).
- Environnement : machine de dev = Podman derrière la CLI docker (`--load` requis sur les builds buildx) ; kind disponible ; PAS de contexte kubectl vers la prod depuis ce poste (la Task 8 est collaborative avec l'utilisateur).
- Valeurs par défaut du provider (spec) : namespace `rag-sandbox`, memory_limit `1Gi`, cpu_limit `1`, timeout max `120` s, ttl_seconds_after_finished `300`, logs cap 10 Mo, erreur AST = exit_code `-999`, message « Code is unsafe ».

---

### Task 1: Module AST partagé `security_shared.py` + garde anti-dérive

**Files:**
- Create: `agent/sandbox/security_shared.py`
- Test: `agent/sandbox/tests/test_security_shared.py` (créer le dossier avec `__init__.py` vide si absent)

**Interfaces:**
- Produces: `analyze_python_code(code: str) -> tuple[bool, list[str]]` — `(is_safe, violations)` ; violations = lignes lisibles type `"Line 2: Import: socket"`. Utilisé par la Task 3.
- Contexte : `agent/sandbox/executor_manager/services/security.py::SecurePythonAnalyzer` n'est PAS importable depuis le process API (il importe `core.logger` et `models.enums`, packages internes au runtime executor-manager) et vit hors du contexte de build de l'image executor-manager → on crée un module autonome (stdlib uniquement) SANS toucher au fichier upstream, avec un test qui compare les deux listes noires par parsing AST du fichier upstream (dérive détectée à la CI, pas en prod).

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# agent/sandbox/tests/test_security_shared.py
import ast
import os

import pytest

from agent.sandbox.security_shared import DANGEROUS_IMPORTS, analyze_python_code

UPSTREAM = os.path.join(
    os.path.dirname(__file__), "..", "executor_manager", "services", "security.py"
)


def test_rejects_dangerous_import():
    ok, violations = analyze_python_code("import socket\ndef main():\n    return 1")
    assert not ok
    assert any("socket" in v for v in violations)


def test_rejects_dangerous_attribute_call():
    ok, violations = analyze_python_code("def main():\n    __import__('os')")
    assert not ok


def test_accepts_famat_recipe_shape():
    code = (
        "import famat_recipes as fr, json\n"
        "def main():\n"
        "    con = fr.load_url('http://minio:9000/x.csv')\n"
        "    return json.dumps(fr.drift(con, 'CORRECTION_X', 10), default=str)\n"
    )
    ok, violations = analyze_python_code(code)
    assert ok, violations


def test_syntax_error_is_unsafe():
    ok, violations = analyze_python_code("def main(:\n    pass")
    assert not ok


def test_dangerous_imports_match_upstream_executor_manager():
    """Garde anti-dérive : si upstream change sa liste noire, ce test casse."""
    tree = ast.parse(open(UPSTREAM, encoding="utf-8").read())
    upstream_sets = [
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "DANGEROUS_IMPORTS" for t in node.targets)
    ]
    assert upstream_sets, "DANGEROUS_IMPORTS introuvable dans le fichier upstream"
    assert DANGEROUS_IMPORTS == upstream_sets[0]
```

- [ ] **Step 2: Vérifier l'échec**

Run: `PYTHONPATH=. uv run python -m pytest agent/sandbox/tests/test_security_shared.py -v`
Expected: FAIL — `ModuleNotFoundError: agent.sandbox.security_shared`.

- [ ] **Step 3: Implémenter le module**

Lire d'abord `agent/sandbox/executor_manager/services/security.py` EN ENTIER, puis créer `agent/sandbox/security_shared.py` : reprendre la classe `SecurePythonAnalyzer` à l'identique (mêmes `DANGEROUS_IMPORTS`, `DANGEROUS_CALLS`/patterns, même logique de visite AST) avec DEUX changements uniquement : (a) `from core.logger import logger` → `import logging` + `logger = logging.getLogger(__name__)` ; (b) supprimer toute référence à `models.enums.SupportLanguage` (le module est python-only). Ajouter en tête :

```python
"""Analyse AST de sécurité — copie autonome de
executor_manager/services/security.py (non importable hors de son runtime :
dépendances core.logger / models.enums, et hors du contexte de build de son
image Docker). Le test test_dangerous_imports_match_upstream_executor_manager
casse si la copie dérive de l'upstream. Fichier upstream INTOUCHÉ.
"""
```

Et exposer l'API de la Task 3 :

```python
def analyze_python_code(code: str) -> tuple[bool, list[str]]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"SyntaxError: {exc}"]
    analyzer = SecurePythonAnalyzer()
    analyzer.visit(tree)
    return (not analyzer.violations, list(analyzer.violations))
```

(Adapter le nom exact de l'attribut de violations à ce que fait la classe upstream — le lire, pas le deviner. Si la classe upstream retourne des tuples `(line, msg)`, formater en `f"Line {line}: {msg}"` pour matcher le format observé en Task 8 du POC : `"Line 2: Import: socket"`.)

- [ ] **Step 4: Vérifier que les 5 tests passent**

Run: `PYTHONPATH=. uv run python -m pytest agent/sandbox/tests/test_security_shared.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/sandbox/security_shared.py agent/sandbox/tests/
git commit -m "feat(sandbox): module AST partagé security_shared + garde anti-dérive vs executor-manager"
```

---

### Task 2: Manifest du Job durci (fonction pure, testée champ par champ)

**Files:**
- Create: `agent/sandbox/providers/k8s.py` (début — la fonction pure)
- Test: `agent/sandbox/tests/test_k8s_manifest.py`

**Interfaces:**
- Produces: `build_job_manifest(job_name: str, image: str, wrapper_b64: str, timeout: int, memory_limit: str = "1Gi", cpu_limit: str = "1", ttl_seconds: int = 300, image_pull_secret: str = "", node_selector: dict | None = None) -> dict` — dict prêt pour `BatchV1Api.create_namespaced_job(body=...)`. Fonction PURE (aucun import kubernetes) — consommée par la Task 3.
- Le pod exécute : `python -c "import base64,os; exec(base64.b64decode(os.environ['SBX_CODE']).decode())"` où `SBX_CODE` = wrapper `result_protocol` encodé base64.

- [ ] **Step 1: Tests qui échouent**

```python
# agent/sandbox/tests/test_k8s_manifest.py
import pytest

from agent.sandbox.providers.k8s import build_job_manifest


@pytest.fixture
def manifest():
    return build_job_manifest("sbx-abc12345", "harbor.example/x:1", "QUJD", timeout=60)


def _container(m):
    return m["spec"]["template"]["spec"]["containers"][0]


def test_job_shape(manifest):
    assert manifest["apiVersion"] == "batch/v1" and manifest["kind"] == "Job"
    assert manifest["metadata"]["name"] == "sbx-abc12345"
    assert manifest["metadata"]["labels"]["app"] == "ragflow-sandbox"
    spec = manifest["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 60
    assert spec["ttlSecondsAfterFinished"] == 300


def test_pod_hardening(manifest):
    pod = manifest["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    sc = _container(manifest)["securityContext"]
    assert sc["runAsNonRoot"] is True
    assert sc["runAsUser"] == 65534 and sc["runAsGroup"] == 65534
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}


def test_volumes_are_memory_tmpfs(manifest):
    pod = manifest["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["workspace"]["emptyDir"]["medium"] == "Memory"
    assert vols["tmp"]["emptyDir"]["medium"] == "Memory"
    mounts = {m["name"]: m["mountPath"] for m in _container(manifest)["volumeMounts"]}
    assert mounts == {"workspace": "/workspace", "tmp": "/tmp"}
    assert _container(manifest)["workingDir"] == "/workspace"


def test_resources_and_code_env(manifest):
    c = _container(manifest)
    assert c["imagePullPolicy"] == "IfNotPresent"  # `latest` forcerait Always sur kind
    assert c["resources"]["limits"] == {"memory": "1Gi", "cpu": "1"}
    assert c["resources"]["requests"] == {"memory": "1Gi", "cpu": "1"}
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["SBX_CODE"] == "QUJD"
    assert c["command"][0] == "python" and "SBX_CODE" in c["command"][2]


def test_optional_pull_secret_and_node_selector():
    m = build_job_manifest("sbx-x", "img", "QQ==", 30,
                           image_pull_secret="harbor-pull-secret",
                           node_selector={"kubernetes.io/arch": "amd64"})
    pod = m["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "harbor-pull-secret"}]
    assert pod["nodeSelector"] == {"kubernetes.io/arch": "amd64"}
    # absents par défaut
    m2 = build_job_manifest("sbx-y", "img", "QQ==", 30)
    pod2 = m2["spec"]["template"]["spec"]
    assert "imagePullSecrets" not in pod2 and "nodeSelector" not in pod2
```

- [ ] **Step 2: Vérifier l'échec** — `PYTHONPATH=. uv run python -m pytest agent/sandbox/tests/test_k8s_manifest.py -v` → FAIL (module k8s absent).

- [ ] **Step 3: Implémenter la fonction pure**

```python
# agent/sandbox/providers/k8s.py  (en-tête + fonction pure ; la classe arrive en Task 3)
"""Provider sandbox k8s — Jobs Kubernetes éphémères durcis.

CUSTOM B2B SaaS — provider sandbox k8s (spec
docs/superpowers/specs/2026-08-13-k8s-sandbox-provider-design.md).
Aucun import kubernetes au niveau module : lazy dans initialize().
"""

_BOOTSTRAP = "import base64,os; exec(base64.b64decode(os.environ['SBX_CODE']).decode())"


def build_job_manifest(job_name, image, wrapper_b64, timeout,
                       memory_limit="1Gi", cpu_limit="1", ttl_seconds=300,
                       image_pull_secret="", node_selector=None):
    resources = {"memory": memory_limit, "cpu": cpu_limit}
    pod_spec = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "volumes": [
            {"name": "workspace", "emptyDir": {"medium": "Memory"}},
            {"name": "tmp", "emptyDir": {"medium": "Memory"}},
        ],
        "containers": [{
            "name": "sandbox",
            "image": image,
            "imagePullPolicy": "IfNotPresent",
            "command": ["python", "-c", _BOOTSTRAP],
            "env": [{"name": "SBX_CODE", "value": wrapper_b64},
                    {"name": "MPLCONFIGDIR", "value": "/tmp/matplotlib"}],
            "workingDir": "/workspace",
            "volumeMounts": [
                {"name": "workspace", "mountPath": "/workspace"},
                {"name": "tmp", "mountPath": "/tmp"},
            ],
            "resources": {"limits": dict(resources), "requests": dict(resources)},
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65534,
                "runAsGroup": 65534,
                "readOnlyRootFilesystem": True,
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        }],
    }
    if image_pull_secret:
        pod_spec["imagePullSecrets"] = [{"name": image_pull_secret}]
    if node_selector:
        pod_spec["nodeSelector"] = dict(node_selector)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_name,
                     "labels": {"app": "ragflow-sandbox",
                                "ragflow.io/component": "code-exec"}},
        "spec": {"backoffLimit": 0,
                 "activeDeadlineSeconds": timeout,
                 "ttlSecondsAfterFinished": ttl_seconds,
                 "template": {"metadata": {"labels": {"app": "ragflow-sandbox"}},
                              "spec": pod_spec}},
    }
```

Note : l'image de base a `MPLCONFIGDIR=/tmp/matplotlib` prévu — avec rootfs read-only, `/tmp` est le tmpfs monté, matplotlib fonctionne.

- [ ] **Step 4: Vérifier que les 6 tests passent** — même commande, 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/sandbox/providers/k8s.py agent/sandbox/tests/test_k8s_manifest.py
git commit -m "feat(sandbox): manifest Job k8s durci — fonction pure testée champ par champ"
```

---

### Task 3: `K8sProvider` — cycle de vie complet (client kubernetes mocké)

**Files:**
- Modify: `agent/sandbox/providers/k8s.py`
- Test: `agent/sandbox/tests/test_k8s_provider.py`

**Interfaces:**
- Consumes: `build_job_manifest` (Task 2), `analyze_python_code` (Task 1), `agent.sandbox.result_protocol.build_python_wrapper(code, args_json) -> str` et `extract_structured_result(stdout) -> tuple[str, dict]` (existants — LIRE `result_protocol.py` et `providers/self_managed.py` avant d'écrire, pour répliquer exactement la structure `metadata` de l'`ExecutionResult` de `self_managed`, notamment `metadata["artifacts"]`).
- Produces: classe `K8sProvider(SandboxProvider)` avec `initialize(config) -> bool`, `create_instance(template="python") -> SandboxInstance`, `execute_code(instance_id, code, language, timeout=10, arguments=None) -> ExecutionResult`, `health() -> bool` (aligné sur la signature réelle de la base — la lire), `get_config_schema() -> dict` (mêmes conventions que `SelfManagedProvider.get_config_schema`), `get_supported_templates() -> ["python"]`.
- Config (défauts) : `namespace="rag-sandbox"`, `image=""` (obligatoire), `memory_limit="1Gi"`, `cpu_limit="1"`, `timeout=120` (max), `kubeconfig_path=""` (vide = in-cluster), `image_pull_secret=""`, `node_selector={}`, `ttl_seconds_after_finished=300`.

- [ ] **Step 1: Tests qui échouent** (mock du client kubernetes injecté)

Concevoir `K8sProvider` pour l'injectabilité : les clients API sont stockés sur `self._batch` / `self._core` (créés dans `initialize`), et les tests les remplacent par des mocks — AUCUN import kubernetes dans les tests.

```python
# agent/sandbox/tests/test_k8s_provider.py
import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.sandbox.providers.k8s import K8sProvider

WRAPPED_OK = json.dumps({
    "__sandbox_result__": True, "value": "42", "type": "str",
    "artifacts": [{"name": "spc.svg", "content_b64": base64.b64encode(b"<svg/>").decode(),
                   "mime": "image/svg+xml"}],
})


def _provider(job_status="succeeded", logs=WRAPPED_OK):
    p = K8sProvider()
    p.namespace = "rag-sandbox"
    p.image = "img:1"
    p.timeout_max = 120
    p._batch = MagicMock()
    p._core = MagicMock()
    p._initialized = True
    status = SimpleNamespace(succeeded=1 if job_status == "succeeded" else None,
                             failed=1 if job_status == "failed" else None,
                             conditions=None)
    p._batch.read_namespaced_job.return_value = SimpleNamespace(status=status)
    pod = SimpleNamespace(metadata=SimpleNamespace(name="sbx-x-pod"),
                          status=SimpleNamespace(phase="Succeeded",
                                                 container_statuses=None))
    p._core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])
    p._core.read_namespaced_pod_log.return_value = logs
    return p


def test_execute_success_parses_result_and_artifacts():
    p = _provider()
    r = p.execute_code("i", "def main():\n    return 42", "python", timeout=30)
    assert r.exit_code == 0
    assert r.metadata["artifacts"][0]["name"] == "spc.svg"
    p._batch.create_namespaced_job.assert_called_once()
    body = p._batch.create_namespaced_job.call_args
    assert (body.kwargs.get("namespace") or body.args[0]) == "rag-sandbox"
    p._batch.delete_namespaced_job.assert_called_once()  # cleanup


def test_ast_rejection_short_circuits():
    p = _provider()
    r = p.execute_code("i", "import socket\ndef main():\n    return 1", "python")
    assert r.exit_code == -999 and "unsafe" in r.stderr.lower()
    p._batch.create_namespaced_job.assert_not_called()


def test_nodejs_unsupported():
    p = _provider()
    r = p.execute_code("i", "function main(){}", "nodejs")
    assert r.exit_code != 0 and "python" in r.stderr.lower()
    p._batch.create_namespaced_job.assert_not_called()


def test_job_failure_returns_typed_error():
    p = _provider(job_status="failed", logs="Traceback ...")
    r = p.execute_code("i", "def main():\n    raise ValueError('x')", "python")
    assert r.exit_code != 0
    p._batch.delete_namespaced_job.assert_called_once()


def test_timeout_clamped_to_max():
    p = _provider()
    p.execute_code("i", "def main():\n    return 1", "python", timeout=9999)
    manifest = p._batch.create_namespaced_job.call_args.kwargs["body"]
    assert manifest["spec"]["activeDeadlineSeconds"] == 120


def test_oversized_logs_rejected():
    p = _provider(logs="x" * (10 * 1024 * 1024 + 1))
    r = p.execute_code("i", "def main():\n    return 1", "python")
    assert r.exit_code != 0 and "volum" in r.stderr.lower()


def test_initialize_without_lib_or_cluster_returns_false():
    p = K8sProvider()
    ok = p.initialize({"namespace": "rag-sandbox", "image": ""})  # image manquante
    assert ok is False
```

- [ ] **Step 2: Vérifier l'échec** — `PYTHONPATH=. uv run python -m pytest agent/sandbox/tests/test_k8s_provider.py -v` → FAIL (classe absente).

- [ ] **Step 3: Implémenter la classe**

Points imposés (le reste suit `self_managed.py` comme modèle de style) :
- `initialize` : valider `image` non vide ; import lazy `from kubernetes import client, config as k8s_config` dans un try/except `ImportError` → `return False` avec log ; `k8s_config.load_incluster_config()` si `kubeconfig_path` vide sinon `load_kube_config(config_file=...)` ; `self._batch = client.BatchV1Api()`, `self._core = client.CoreV1Api()` ; ping `self._batch.list_namespaced_job(self.namespace, limit=1)` (échec → log + False).
- `execute_code` : garde language ; AST via `analyze_python_code` (exit_code `-999`, stderr = violations jointes + « Code is unsafe ») ; `wrapper = build_python_wrapper(code, json.dumps(arguments or {}))` ; `wrapper_b64 = base64.b64encode(wrapper.encode()).decode()` ; taille > 900 Ko → erreur explicite ; `job_name = f"sbx-{uuid.uuid4().hex[:8]}"` ; create ; boucle poll 0,5 s jusqu'à `status.succeeded`/`status.failed` ou `time.monotonic()` > deadline+10 ; pod via `list_namespaced_pod(namespace, label_selector=f"job-name={job_name}")` ; si pod `Pending` > 60 s → lire `container_statuses[].state.waiting.reason` (ImagePullBackOff/ErrImagePull → « image inaccessible depuis le cluster ») ; logs via `read_namespaced_pod_log`, cap 10 Mo ; parsing `extract_structured_result` ; `ExecutionResult` avec `metadata` répliquant les clés de `self_managed` (dont `artifacts`) ; `finally:` delete Job `propagation_policy="Foreground"` (exceptions du delete loggées, jamais propagées).
- `health` : `list_namespaced_job(limit=1)` sous try/except → bool.
- `get_config_schema` : toutes les clés de config avec types/défauts/placeholders (modèle : `SelfManagedProvider.get_config_schema`).
- AUCUNE exception ne sort de `execute_code` : tout chemin retourne un `ExecutionResult` avec stderr actionnable.

- [ ] **Step 4: Vérifier que les 8 tests passent** — même commande, 8 PASS. Vérifier aussi que le module s'importe SANS kubernetes : `PYTHONPATH=. uv run python -c "import agent.sandbox.providers.k8s; print('import OK sans lib kubernetes')"`.

- [ ] **Step 5: Commit**

```bash
git add agent/sandbox/providers/k8s.py agent/sandbox/tests/test_k8s_provider.py
git commit -m "feat(sandbox): K8sProvider — cycle de vie Jobs éphémères, erreurs typées, artefacts"
```

---

### Task 4: Enregistrement du provider + dépendance + CLAUDE.md

**Files:**
- Modify: `agent/sandbox/client.py` (dict `provider_classes`, ~lignes 75-88)
- Modify: `pyproject.toml` (dépendance `kubernetes`)
- Modify: `CLAUDE.md` (tableau « Custom files to watch »)
- Test: `agent/sandbox/tests/test_k8s_registration.py`

**Interfaces:**
- Consumes: `K8sProvider` (Task 3).
- Produces: `provider_type="k8s"` résolvable par `agent.sandbox.client` (donc par l'admin UI et `code_exec`).

- [ ] **Step 1: Test qui échoue**

```python
# agent/sandbox/tests/test_k8s_registration.py
def test_k8s_in_provider_classes():
    import inspect

    from agent.sandbox import client as sandbox_client
    src = inspect.getsource(sandbox_client)
    assert '"k8s": K8sProvider' in src, "provider k8s absent du registre client.py"
```

- [ ] **Step 2: Vérifier l'échec** — `PYTHONPATH=. uv run python -m pytest agent/sandbox/tests/test_k8s_registration.py -v` → FAIL.

- [ ] **Step 3: Enregistrer**

Dans `agent/sandbox/client.py`, à l'endroit du dict `provider_classes` (imports locaux existants juste au-dessus) :

```python
        from agent.sandbox.providers.k8s import K8sProvider  # CUSTOM B2B SaaS — provider sandbox k8s

        provider_classes = {
            "self_managed": SelfManagedProvider,
            "aliyun_codeinterpreter": AliyunCodeInterpreterProvider,
            "e2b": E2BProvider,
            "local": LocalProvider,
            "ssh": SSHProvider,
            "k8s": K8sProvider,  # CUSTOM B2B SaaS — provider sandbox k8s
        }
```

Dans `pyproject.toml`, ajouter `kubernetes` aux dépendances principales (version : la dernière majeure stable, contrainte souple type `>=31.0`), puis `uv sync` — vérifier qu'aucune autre dépendance ne bouge (lockfile diff minimal).

Dans `CLAUDE.md`, tableau « Custom files to watch on upstream merges », ajouter :

```
| `agent/sandbox/client.py` | Provider `k8s` enregistré dans `provider_classes` + import. Grep `CUSTOM B2B SaaS — provider sandbox k8s` |
| `agent/sandbox/providers/k8s.py` + `agent/sandbox/security_shared.py` + `agent/sandbox/tests/` | Fichiers entièrement custom (provider Jobs K8s durcis + AST partagé + tests) — pas de conflit attendu, listés pour visibilité |
```

- [ ] **Step 4: Vérifier** — test PASS + `PYTHONPATH=. uv run python -c "from agent.sandbox.client import get_provider_info; print('client importable')"` + suite complète sandbox : `PYTHONPATH=. uv run python -m pytest agent/sandbox/tests/ -v` (19 tests attendus : 5+6+8 ; le test d'enregistrement fait 19+1=20 au total — ajuster le compte constaté).

- [ ] **Step 5: Commit**

```bash
git add agent/sandbox/client.py agent/sandbox/tests/test_k8s_registration.py pyproject.toml uv.lock CLAUDE.md
git commit -m "feat(sandbox): enregistrement provider k8s + dépendance kubernetes + CLAUDE.md"
```

---

### Task 5: Helm — namespace `rag-sandbox`, RBAC, NetworkPolicy, ResourceQuota, automount token

**Files:**
- Create: `helm/ragflow/templates/sandbox/namespace.yaml`, `rbac.yaml`, `networkpolicy.yaml`, `resourcequota.yaml`
- Modify: `helm/ragflow/values.yaml` (bloc `sandbox:` + `global.sandboxTokenAutomount`)
- Modify: `helm/ragflow/values-alterai.yaml` (overrides prod)
- Modify: `helm/ragflow/charts/ragflow-api/templates/deployment.yaml` et `helm/ragflow/charts/ragflow-task-executor/templates/deployment.yaml` (automount du token)
- Test: assertions `helm template` (commandes ci-dessous)

**Interfaces:**
- Consomme les noms de ServiceAccounts rendus par `{{ include "ragflow-api.fullname" . }}` / `{{ include "ragflow-task-executor.fullname" . }}` — les résoudre en RENDANT le chart (`helm template`), ne pas les deviner.
- **Piège critique** : les deux SAs ont `automountServiceAccountToken: false` — sans token monté, le client in-cluster du provider ne peut PAS s'authentifier. Fix : dans les deux `deployment.yaml`, au niveau du pod spec, ajouter `automountServiceAccountToken: {{ .Values.global.sandboxTokenAutomount | default false }}` (le champ pod-level prime sur le champ SA-level). Défaut `false` = comportement actuel inchangé tant que le sandbox n'est pas activé.

- [ ] **Step 1: Écrire le bloc values**

```yaml
# values.yaml — à la racine (nouveau bloc, à côté des blocs de sous-charts)
sandbox:
  enabled: false          # rien n'est rendu tant que false
  namespace:
    name: "rag-sandbox"
    create: true
  image:
    repository: ""        # ex prod: harbor.cylndata.cyllene.pro/data/ragflow-sandbox-python
    tag: ""
  limits:
    memory: "1Gi"
    cpu: "1"
  quota:
    memory: "8Gi"
    cpu: "8"
    pods: "20"
  networkPolicy:
    enabled: true
    # Namespace + sélecteur du service MinIO de la plateforme (egress autorisé).
    minioNamespace: "rag-new2"
    minioPodSelector:
      app: minio
    minioPort: 9000
    extraEgress: []       # blocs egress additionnels (future base FAMAT)
```

Et dans `global:` : `sandboxTokenAutomount: false` (commentaire : requis à `true` quand `sandbox.enabled` — le provider k8s a besoin du token SA).
Dans `values-alterai.yaml` : `sandbox.enabled: true`, `image.repository: harbor.cylndata.cyllene.pro/data/ragflow-sandbox-python`, `image.tag:` aligné sur le tag de release courant, `global.sandboxTokenAutomount: true`. VÉRIFIER le sélecteur réel des pods MinIO dans le chart (subchart minio-operator — lire les templates/valeurs pour trouver les labels effectifs des pods MinIO et ajuster `minioPodSelector`).

- [ ] **Step 2: Écrire les 4 templates** (tous intégralement gardés par `{{- if .Values.sandbox.enabled }}`)

`namespace.yaml` :
```yaml
{{- if and .Values.sandbox.enabled .Values.sandbox.namespace.create }}
apiVersion: v1
kind: Namespace
metadata:
  name: {{ .Values.sandbox.namespace.name }}
  labels:
    app.kubernetes.io/part-of: ragflow
    ragflow.io/component: sandbox
{{- end }}
```

`rbac.yaml` (Role dans le ns sandbox + un RoleBinding par SA) :
```yaml
{{- if .Values.sandbox.enabled }}
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ragflow-sandbox-runner
  namespace: {{ .Values.sandbox.namespace.name }}
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ragflow-sandbox-runner
  namespace: {{ .Values.sandbox.namespace.name }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: ragflow-sandbox-runner
subjects:
  - kind: ServiceAccount
    name: {{ include "ragflow-api.fullname" (dict "Values" (index .Values "ragflow-api") "Chart" .Chart "Release" .Release) }}
    namespace: {{ .Release.Namespace }}
  - kind: ServiceAccount
    name: {{ include "ragflow-task-executor.fullname" (dict "Values" (index .Values "ragflow-task-executor") "Chart" .Chart "Release" .Release) }}
    namespace: {{ .Release.Namespace }}
{{- end }}
```
ATTENTION : l'appel des helpers de sous-chart depuis le chart umbrella peut différer selon leur définition — RENDRE le chart et ajuster (au besoin, dupliquer la logique de nommage en commentaire justifié, ou utiliser les noms rendus constatés — la règle : le RoleBinding DOIT référencer les noms exacts que `helm template` produit pour les deux ServiceAccounts).

`networkpolicy.yaml` (default-deny + DNS + MinIO) :
```yaml
{{- if and .Values.sandbox.enabled .Values.sandbox.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ragflow-sandbox-default-deny
  namespace: {{ .Values.sandbox.namespace.name }}
spec:
  podSelector: {}
  policyTypes: ["Ingress", "Egress"]
  egress:
    - to:
        - namespaceSelector: {}
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - { port: 53, protocol: UDP }
        - { port: 53, protocol: TCP }
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.sandbox.networkPolicy.minioNamespace }}
          podSelector:
            matchLabels:
{{ toYaml .Values.sandbox.networkPolicy.minioPodSelector | indent 14 }}
      ports:
        - { port: {{ .Values.sandbox.networkPolicy.minioPort }}, protocol: TCP }
{{- with .Values.sandbox.networkPolicy.extraEgress }}
{{ toYaml . | indent 4 }}
{{- end }}
{{- end }}
```

`resourcequota.yaml` :
```yaml
{{- if .Values.sandbox.enabled }}
apiVersion: v1
kind: ResourceQuota
metadata:
  name: ragflow-sandbox-quota
  namespace: {{ .Values.sandbox.namespace.name }}
spec:
  hard:
    limits.memory: {{ .Values.sandbox.quota.memory }}
    limits.cpu: {{ .Values.sandbox.quota.cpu | quote }}
    pods: {{ .Values.sandbox.quota.pods | quote }}
{{- end }}
```

- [ ] **Step 3: Patch automount dans les deux deployments**

Dans le pod spec de chaque `deployment.yaml` (même niveau que `serviceAccountName:`) :
```yaml
      automountServiceAccountToken: {{ .Values.global.sandboxTokenAutomount | default false }}
```

- [ ] **Step 4: Vérifier par rendu**

```bash
helm lint helm/ragflow
# sandbox désactivé (défaut) : AUCUN objet sandbox rendu, automount false
helm template rag helm/ragflow -f helm/ragflow/values-kind-dev.yaml | grep -c "ragflow-sandbox" # attendu: 0
helm template rag helm/ragflow -f helm/ragflow/values-kind-dev.yaml | grep "automountServiceAccountToken" # attendu: false partout
# sandbox activé : les 5 objets + automount true + subjects corrects
helm template rag helm/ragflow -f helm/ragflow/values-kind-dev.yaml \
  --set sandbox.enabled=true --set global.sandboxTokenAutomount=true \
  --set sandbox.image.repository=test --set sandbox.image.tag=t > /tmp/sbx.yaml
grep -c "kind: Namespace" /tmp/sbx.yaml   # >= 1 avec name: rag-sandbox
grep -A3 "kind: Role$" /tmp/sbx.yaml       # verbs jobs/pods/log
grep -B2 -A8 "kind: RoleBinding" /tmp/sbx.yaml  # subjects = noms EXACTS des 2 SAs rendus
grep -A20 "kind: NetworkPolicy" /tmp/sbx.yaml   # default-deny + 2 blocs egress
grep -A6 "kind: ResourceQuota" /tmp/sbx.yaml
```
Comparer les `subjects` du RoleBinding aux noms des `ServiceAccount` rendus dans le même output — ils doivent être identiques.

- [ ] **Step 5: Commit**

```bash
git add helm/ragflow
git commit -m "feat(helm): namespace rag-sandbox — RBAC minimal, NetworkPolicy default-deny, ResourceQuota, automount token conditionnel"
```

---

### Task 6: CI GitLab — image `ragflow-sandbox-python` vers Harbor

**Files:**
- Modify: `.gitlab-ci.yml`

**Interfaces:**
- Produit l'image `harbor.cylndata.cyllene.pro/data/ragflow-sandbox-python:${IMAGE_TAG}` consommée par les values Helm (Task 5) et la prod (Task 8).

- [ ] **Step 1: Ajouter le job** (modèle : `kaniko_build_mgmt_prd` — image petite → cache activé)

```yaml
# =============================================================================
#  kaniko_build_sandbox_python_prd
#  -----------------------------------------------------------------------------
#  Image d'exécution du provider sandbox k8s (Jobs éphémères dans rag-sandbox).
#  python:3.11-slim + numpy/pandas/matplotlib/requests/duckdb/pymysql
#  + famat_recipes (copie cuite — resync: poc/famat/README.md). ~600 MB.
# =============================================================================
kaniko_build_sandbox_python_prd:
  stage: build_prd
  tags:
    - alterai-prod
  script:
      - |
        if [ -n "$CI_COMMIT_TAG" ]; then
          IMAGE_TAG="$CI_COMMIT_TAG"
        else
          IMAGE_TAG="$CI_COMMIT_SHORT_SHA"
        fi
      - set -x
      - echo "{\"auths\":{\"harbor.cylndata.cyllene.pro\":{\"username\":\"$HARBOR_CYLNDATA_ROBOT_PRD\",\"password\":\"$HARBOR_CYLNDATA_ROBOT_PWD_PRD\"}}}" > /kaniko/.docker/config.json
      - |
        /kaniko/executor \
          --cache=true \
          --cache-repo "harbor.cylndata.cyllene.pro/${HARBOR_PROJECT}/ragflow-sandbox-python/cache" \
          --compressed-caching=false \
          --insecure=true \
          --skip-tls-verify=true \
          --snapshot-mode=redo \
          --use-new-run \
          --cleanup \
          --push-retry=3 \
          --build-arg NEED_MIRROR=0 \
          --context "${CI_PROJECT_DIR}/agent/sandbox/sandbox_base_image/python" \
          --dockerfile "${CI_PROJECT_DIR}/agent/sandbox/sandbox_base_image/python/Dockerfile" \
          --destination "harbor.cylndata.cyllene.pro/${HARBOR_PROJECT}/ragflow-sandbox-python:${IMAGE_TAG}"
  when: manual
```

- [ ] **Step 2: Valider le YAML**

Run: `PYTHONPATH=. uv run python -c "import yaml; yaml.safe_load(open('.gitlab-ci.yml')); print('YAML OK')"`
Et vérifier que le contexte contient tout ce que le Dockerfile COPY (requirements.txt, matplotlibrc, famat_recipes.py) : `ls agent/sandbox/sandbox_base_image/python/`.

- [ ] **Step 3: Commit**

```bash
git add .gitlab-ci.yml
git commit -m "ci: job kaniko ragflow-sandbox-python vers Harbor (image sandbox k8s)"
```

---

### Task 7: Intégration kind — le provider tourne en vrai

**Files:**
- Create: `scripts/sandbox_kind_test.sh`
- Test: exécution du script (gated `K8S_SANDBOX_TEST=1`)

**Interfaces:**
- Consumes: image locale `sandbox-base-python:latest` (déjà buildée, Tasks 7/10 du POC), `K8sProvider` avec `kubeconfig_path`, templates Helm (Task 5).
- Produces: preuve d'exécution réelle bout-en-bout hors prod.

- [ ] **Step 1: Écrire le script**

```bash
#!/usr/bin/env bash
# Test d'intégration du provider sandbox k8s sur kind.
# Usage: K8S_SANDBOX_TEST=1 bash scripts/sandbox_kind_test.sh
set -euo pipefail
[ "${K8S_SANDBOX_TEST:-}" = "1" ] || { echo "K8S_SANDBOX_TEST != 1 — skip"; exit 0; }

CLUSTER=ragflow-sbx-test
kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s
kind load docker-image sandbox-base-python:latest --name "$CLUSTER"

# Namespace + RBAC rendus par le chart (subjects sans importance ici : on
# teste la MÉCANIQUE provider avec le kubeconfig kind admin ; le RBAC réel
# est vérifié en prod Task 8 via kubectl auth can-i).
helm template rag helm/ragflow -f helm/ragflow/values-kind-dev.yaml \
  --set sandbox.enabled=true \
  --set sandbox.networkPolicy.enabled=false \
  --set sandbox.image.repository=sandbox-base-python --set sandbox.image.tag=latest \
  --show-only templates/sandbox/namespace.yaml \
  --show-only templates/sandbox/resourcequota.yaml | kubectl --context "kind-$CLUSTER" apply -f -

PYTHONPATH=. uv run --with kubernetes python - <<'PY'
import json
from agent.sandbox.providers.k8s import K8sProvider

p = K8sProvider()
assert p.initialize({
    "namespace": "rag-sandbox",
    "image": "sandbox-base-python:latest",
    "kubeconfig_path": "KIND_KUBECONFIG",   # remplacé ci-dessous
}), "initialize failed"
code = (
    "import famat_recipes as fr, json\n"
    "def main():\n"
    "    rows = [(i+1, f'P{i:02d}', 10, 'CORRECTION_X', 0.001*i, f'2026-01-01 10:{i:02d}:00') for i in range(25)]\n"
    "    d = fr.drift(fr.load_rows(rows), 'CORRECTION_X', 10)\n"
    "    fr.spc_chart(d, out_dir='artifacts')\n"
    "    return json.dumps({'n_parts': d['n_parts'], 'segments': len(d['segments'])})\n"
)
r = p.execute_code("it", code, "python", timeout=90)
print("exit_code:", r.exit_code)
print("stderr:", r.stderr[:500])
assert r.exit_code == 0, r.stderr
arts = r.metadata.get("artifacts", [])
assert any(a.get("name", "").endswith(".svg") for a in arts), f"pas d'artefact SVG: {[a.get('name') for a in arts]}"
print("OK — recette exécutée dans un Job kind, artefact SVG collecté")
PY
echo "=== SUCCÈS intégration kind ==="
```
Adapter la ligne `KIND_KUBECONFIG` : exporter `kind get kubeconfig --name "$CLUSTER"` vers un fichier temp et injecter son chemin (via env var lue par le heredoc — `os.environ["SBX_KUBECONFIG"]`). L'image `sandbox-base-python:latest` chargée dans kind est locale → `imagePullPolicy` par défaut `IfNotPresent` du manifest suffit (le tag `latest` force `Always` chez K8s : passer `imagePullPolicy: Never`... → AJUSTEMENT REQUIS : `build_job_manifest` doit poser `"imagePullPolicy": "IfNotPresent"` explicitement sur le conteneur ; ajouter l'assertion correspondante au test de la Task 2 si absent).

- [ ] **Step 2: Exécuter**

Run: `K8S_SANDBOX_TEST=1 bash scripts/sandbox_kind_test.sh`
Expected: `OK — recette exécutée dans un Job kind, artefact SVG collecté`. Notes Podman : kind sur Podman fonctionne (déjà validé pour le chart) ; si `kind load` échoue sur Podman, utiliser `kind load image-archive <(docker save sandbox-base-python:latest)`.

- [ ] **Step 3: Nettoyage optionnel + commit**

```bash
# le cluster kind reste up pour itérer ; suppression manuelle: kind delete cluster --name ragflow-sbx-test
git add scripts/sandbox_kind_test.sh
git commit -m "test(sandbox): intégration kind — Job réel, recette FAMAT, artefact SVG"
```

---

### Task 8: Rollout prod `rag-new2` (collaborative — nécessite l'utilisateur : GitLab, ArgoCD, kubectl)

**Files:**
- Modify: `helm/ragflow/PRODUCTION_NOTES.md` (section « Sandbox k8s »)
- Aucun autre fichier — le reste est de l'opération.

**Interfaces:**
- Consumes: tout ce qui précède, mergé et poussé sur la branche que la CI build.
- Produces: `code_exec` fonctionnel en prod ; agent FAMAT opérationnel sur la plateforme.

- [ ] **Step 1: Documenter le runbook dans PRODUCTION_NOTES.md** — écrire la section complète AVANT l'opération, avec exactement ces étapes :

1. **Image** : lancer le job CI manuel `kaniko_build_sandbox_python_prd` (tag = release) ; vérifier dans Harbor que `data/ragflow-sandbox-python:<tag>` existe.
2. **Values** : `values-alterai.yaml` — `sandbox.enabled: true`, `global.sandboxTokenAutomount: true`, `sandbox.image.{repository,tag}`, `minioPodSelector` vérifié contre les labels réels (`kubectl -n rag-new2 get pods -l ... --show-labels` sur les pods MinIO).
3. **Déploiement** : sync ArgoCD (app `rag-new2`) ; vérifier : namespace `rag-sandbox` créé, `kubectl -n rag-sandbox get role,rolebinding,networkpolicy,resourcequota`, pods api/task-executor redémarrés avec token monté (`kubectl -n rag-new2 get pod <api> -o jsonpath='{.spec.automountServiceAccountToken}'` → true).
4. **Test RBAC négatif/positif** :
   ```bash
   SA=system:serviceaccount:rag-new2:<nom-SA-api-rendu>
   kubectl auth can-i create jobs -n rag-sandbox --as=$SA     # yes
   kubectl auth can-i create pods -n rag-new2 --as=$SA        # no
   kubectl auth can-i delete jobs -n rag-new2 --as=$SA        # no
   ```
5. **Config provider** : ta page admin Sandbox settings si le serveur admin est déployé ; sinon SQL direct via le pod client MariaDB (commande exacte du repo — voir memory/notes `kubectl run mariadb-client`, secret `ragflow-mariadb-app`, db `ragflow`, user `ragflow`) :
   ```sql
   INSERT INTO system_settings(name, value) VALUES ('sandbox.provider_type', 'k8s')
     ON DUPLICATE KEY UPDATE value='k8s';
   INSERT INTO system_settings(name, value) VALUES ('sandbox.k8s',
     '{"namespace":"rag-sandbox","image":"harbor.cylndata.cyllene.pro/data/ragflow-sandbox-python:<tag>","memory_limit":"1Gi","cpu_limit":"1","timeout":120}')
     ON DUPLICATE KEY UPDATE value=VALUES(value);
   ```
   (Vérifier d'abord le schéma réel de `system_settings` — colonnes name/value + create/update dates éventuelles — via `DESCRIBE system_settings;` et adapter l'INSERT.)
6. **Données FAMAT** : upload du CSV dans le MinIO de `rag-new2` (bucket `famat-poc`, port-forward + script boto3 du POC — cf. `poc/famat/README.md`) ; noter l'URL interne (`http://<svc-minio>.rag-new2.svc:9000/famat-poc/Payload-20260526.csv`).
7. **Canvas** : importer `poc/famat/famat_agent_canvas.json` dans le tenant cible, remplacer la `DATA_URL` du prompt par l'URL MinIO interne, configurer le LLM du workspace (qwen-code via LiteLLM, `is_tools: true` — désormais réglable par l'API admin).
8. **Smoke final** : les 3 recettes en chat depuis la plateforme ; pendant l'exécution, `kubectl -n rag-sandbox get jobs -w` montre les Jobs naître et disparaître ; cartes SPC jointes dans le chat.
9. **Rollback** : `sandbox.enabled: false` + `global.sandboxTokenAutomount: false` + sync ArgoCD (retour à l'état antérieur, aucun résidu — le namespace peut rester).

- [ ] **Step 2: Dérouler le runbook AVEC l'utilisateur** (chaque étape cochée avec sa sortie réelle collée dans la section du runbook ou le rapport de tâche). Les étapes 1-3 sont déclenchées par l'utilisateur (GitLab UI, ArgoCD) ; les étapes 4-8 se font ensemble.

- [ ] **Step 3: Commit final**

```bash
git add helm/ragflow/PRODUCTION_NOTES.md
git commit -m "docs(helm): runbook sandbox k8s prod — rollout rag-new2, RBAC checks, rollback"
```
