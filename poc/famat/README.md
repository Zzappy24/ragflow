# POC FAMAT — détection de dérive process

Spec : docs/superpowers/specs/2026-08-11-famat-drift-agent-poc-design.md
Données : `data/Payload-20260526.csv` (JAMAIS commité — .gitignore).
Tests : `uv run --with duckdb python -m pytest poc/famat/tests/ -v`

## Resync de l'image sandbox custom

`agent/sandbox/sandbox_base_image/python/famat_recipes.py` est une **copie cuite** de
`poc/famat/famat_recipes.py`, embarquée dans l'image `sandbox-base-python:latest` pour que le
tool `code_exec` de RAGFlow puisse faire `import famat_recipes`. Ce n'est PAS un module partagé
par symlink — c'est un artefact de build régénérable, marqué en tête par le commentaire
`# GÉNÉRÉ depuis poc/famat/famat_recipes.py — ne pas éditer ici`.

À chaque modification de `poc/famat/famat_recipes.py`, refaire la copie puis rebuilder l'image :

```bash
cp poc/famat/famat_recipes.py agent/sandbox/sandbox_base_image/python/famat_recipes.py
# ré-ajouter le header si l'éditeur ne le préserve pas :
#   sed -i '1i # GÉNÉRÉ depuis poc/famat/famat_recipes.py — ne pas éditer ici' \
#     agent/sandbox/sandbox_base_image/python/famat_recipes.py
cd agent/sandbox
docker build --build-arg NEED_MIRROR=0 -t sandbox-base-python:latest ./sandbox_base_image/python
docker run --rm sandbox-base-python:latest python -c \
  "import duckdb, pymysql, matplotlib, famat_recipes; print('OK', duckdb.__version__)"
```

## Sandbox — executor-manager (Task 8)

### Démarrage

```bash
cd agent/sandbox
docker compose up -d --build
docker compose ps
curl -s http://localhost:9385/healthz   # {"status":"ok"}
```

Sur cette machine, `docker compose up -d --build` a suffi (pas besoin de `--load` : le service
`sandbox-executor-manager` a un `image:` explicite dans `docker-compose.yml`, donc buildx exporte
directement vers le daemon local même avec le driver `docker-container`). Le conteneur manager
passe `healthy` (`docker inspect ... .State.Health.Status`) — le serveur HTTP FastAPI démarre et
répond correctement sur `/healthz`.

### Format de `/run` constaté

Conforme au brief, **aucun écart** : le modèle Pydantic
`agent/sandbox/executor_manager/models/schemas.py::CodeExecutionRequest` attend exactement
`{"code_b64": "<base64>", "language": "python"|"nodejs", "arguments": {}}` (le code doit définir
`def main(): ...`, la valeur de retour est renvoyée telle quelle dans `stdout`/`result`).

### Résultat des smoke tests — BLOQUANT

**Step 3 (import `famat_recipes` + `duckdb`) et Step 4 (accès réseau sortant) n'ont pas pu être
validés** : toute requête `POST /run` échoue avant même d'exécuter le code utilisateur, pour deux
raisons indépendantes découvertes sur cette machine.

**1. Pool de conteneurs sandbox jamais initialisé (0/6 disponibles)**

Logs du manager au démarrage (`docker logs sandbox-sandbox-executor-manager-1`) :

```
ERROR:sandbox:❌ Container creation failed sandbox_python_0: docker: Error response from daemon:
container create: unknown mount option "uid=65534": invalid mount option
[... x6, une par conteneur du pool (3 python + 3 nodejs) ...]
INFO:sandbox:📊 Container pool initialization complete: 0/6 available
```

Cause : `agent/sandbox/executor_manager/core/container.py:93` monte
`--tmpfs /workspace:rw,exec,size=100M,uid=65534,gid=65534` — ce backend Docker n'accepte pas les
sous-options `uid=`/`gid=` sur un `--tmpfs`. Reproduit en dehors du manager, à l'identique avec
`--runtime=runsc` et `--runtime=runc` (même erreur dans les deux cas — donc indépendant de
gVisor) :

```
$ docker run -d --rm --runtime=runc --name test_runc_probe --read-only \
    --tmpfs /workspace:rw,exec,size=100M,uid=65534,gid=65534 \
    --tmpfs /tmp:rw,exec,size=50M --user nobody --workdir /workspace \
    sandbox-base-python:latest sleep 5
docker: Error response from daemon: container create: unknown mount option "uid=65534": invalid
mount option.
```

En retirant `uid=`/`gid=` de la ligne `--tmpfs`, le `docker run` réussit sans erreur. Root cause
probable : le daemon derrière la socket `docker` de cette machine n'est pas un vrai Docker Engine
mais un backend compatible Podman (`docker version --format '{{.Server.Version}}'` → `5.2.3`,
schéma de version Podman, pas Docker ; `docker info` liste des runtimes typiques de Podman : `runc
runj crun-vm krun kata ocijail runsc youki crun crun-wasm`, runtime par défaut `crun`, VM
`Fedora`/`localhost.localdomain`). Le parseur de mount `--tmpfs` de ce backend n'accepte pas les
sous-options `uid=`/`gid=` que Docker Engine accepte nativement. Conséquence : **toute requête
`POST /run` échoue systématiquement** avec :

```json
{"status":"program_runner_error","stdout":"","stderr":"Container pool is busy",
 "exit_code":-10,"detail":"no_available_container", ...}
```

**2. Politique de sécurité statique bloque le test réseau du brief indépendamment du point 1**

Même en supposant le pool disponible, le code du Step 4 du brief (`import socket` +
`socket.create_connection(...)`) est structurellement rejeté par l'analyseur AST embarqué
(`agent/sandbox/executor_manager/services/security.py::SecurePythonAnalyzer.DANGEROUS_IMPORTS`,
qui contient `"socket"` en dur). Réponse obtenue :

```json
{"status":"program_runner_error","stdout":"",
 "stderr":"Line 2: Import: socket\nLine 5: Attribute Access: socket.create_connection",
 "exit_code":-999,"detail":"Code is unsafe", ...}
```

Ce n'est pas un bug d'environnement : c'est une politique délibérée de l'executor-manager
upstream — aucun code utilisateur ne peut faire `import socket` directement, peu importe la
machine. Un vrai test d'accessibilité réseau vers la base Cyllene (Task 10) devra passer par une
librairie autorisée (ex. `pymysql.connect(host=..., ...)`, absente de `DANGEROUS_IMPORTS`) plutôt
que par un socket brut — mais ce test n'a pas pu être exécuté non plus tant que le point 1 n'est
pas résolu (pool à 0/6).

**Verdict réseau : NON DÉTERMINÉ (bloqué en amont par le point 1, pas testable tel quel à cause du
point 2).**

### Ce qui reste à faire (hors scope Task 8 — aucune modif de code hors README ici)

- Corriger `core/container.py:93` pour retirer `uid=65534,gid=65534` du `--tmpfs /workspace` (ou
  rendre l'option conditionnelle au backend), pour que le pool s'initialise sur ce type de
  machine.
- Adapter le test réseau de la Task 10 pour utiliser `pymysql`/`duckdb` (déjà autorisés) plutôt
  que `socket` brut, qui est banni par design par `SecurePythonAnalyzer`.
- Le conteneur `sandbox-sandbox-executor-manager-1` est laissé **up** (healthy côté HTTP) à la fin
  de cette tâche, tel que demandé — mais son pool de conteneurs d'exécution est vide tant que le
  point ci-dessus n'est pas corrigé.
