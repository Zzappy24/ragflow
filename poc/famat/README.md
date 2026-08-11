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

### Fix Podman appliqué — `core/container.py`

Root cause initiale : `agent/sandbox/executor_manager/core/container.py:93` montait
`--tmpfs /workspace:rw,exec,size=100M,uid=65534,gid=65534`. Le backend derrière la socket
`docker` de cette machine n'est pas un vrai Docker Engine mais un backend compatible Podman
(`docker version --format '{{.Server.Version}}'` → `5.2.3`, schéma de version Podman ; `docker
info` liste des runtimes typiques de Podman : `runc runj crun-vm krun kata ocijail runsc youki
crun crun-wasm`, runtime par défaut `crun`, VM `Fedora`/`localhost.localdomain`). Son parseur de
mount `--tmpfs` rejette les sous-options `uid=`/`gid=` (`unknown mount option "uid=65534": invalid
mount option`), reproduit à l'identique avec `--runtime=runc` **et** `--runtime=runsc` (donc
indépendant de gVisor) — conséquence : pool de conteneurs sandbox jamais initialisé (0/6
disponibles), toute requête `POST /run` échouait avec
`{"stderr":"Container pool is busy","exit_code":-10,"detail":"no_available_container"}`.

**Fix** (marqueur `CUSTOM B2B SaaS — Podman tmpfs compat (POC FAMAT)` dans le fichier) : le
chemin nominal Docker Engine (`uid=65534,gid=65534`) est conservé tel quel ; si la création du
conteneur échoue avec ce message précis, `create_container()` retente une fois avec
`mode=1777` à la place (world-writable + sticky bit) sur les deux `--tmpfs` (`/workspace` et
`/tmp`). Vérifié empiriquement **avant** d'écrire le patch, pas supposé : avec un mount
`mode=1777` (sans `uid=`/`gid=`), l'utilisateur `nobody` (65534) peut bien écrire dans
`/workspace` :

```
$ docker run -d --rm --tmpfs /workspace:rw,exec,size=100M,mode=1777 \
    --tmpfs /tmp:rw,exec,size=50M,mode=1777 --user nobody --workdir /workspace \
    sandbox-base-python:latest sleep 30
$ docker exec <id> sh -c 'touch /workspace/testfile && echo WRITE_OK'
drwxrwxrwt. 2 root root 40 ... /workspace
WRITE_OK
```
(Un test préalable **sans** aucune option — mount par défaut `0755 root:root` — donnait bien
`touch: cannot touch '/workspace/testfile': Permission denied`, confirmant que retirer purement
et simplement `uid=`/`gid=` sans compensation aurait cassé le sandbox. `mode=1777` a aussi été
vérifié sur l'image Node.js : `docker exec ... cp -a /app/node_modules /workspace/` → `COPY_OK`.)

Après rebuild (`docker build --load -t sandbox-executor-manager:latest ./executor_manager` — le
driver buildx `docker-container` de cette machine nécessite `--load`, cf. Task 7) et
`docker compose up -d --no-build` (recreate du conteneur manager), logs de démarrage :

```
WARNING:sandbox:⚠️ Container sandbox_nodejs_0 creation failed on uid=/gid= tmpfs mount option (...); retrying with Podman-compatible mode=1777 tmpfs
[... x6, une par conteneur ...]
INFO:sandbox:
📊 Container pool initialization complete: 6/6 available
```

Pool à 6/6. Sur un vrai Docker Engine, le chemin `uid=`/`gid=` réussirait dès le premier essai et
le fallback ne serait jamais déclenché — comportement nominal inchangé.

### Résultat des smoke tests

**Step 3 — import `famat_recipes` + `duckdb` : OK, `"recipes": true` obtenu.**

```
$ curl -s -X POST http://localhost:9385/run -H 'Content-Type: application/json' -d "$CODE"
{"status":"success","stdout":"","stderr":"/tmp/matplotlib is not a writable directory\n...",
 "exit_code":0,"detail":null,"time_used_ms":1138.85,
 "result":{"present":true,"value":"{\"duckdb\": \"1.5.5\", \"recipes\": true}","type":"json"}}
```

(Le `stderr` contient un warning bénin de matplotlib — `/tmp/matplotlib is not a writable
directory`, fallback automatique sur un dossier temporaire — sans incidence sur le résultat,
`exit_code` 0 et `status` `success`.)

**Step 4 (redessiné) — accès réseau sortant via `pymysql` (le `socket` brut du brief original est
banni par `SecurePythonAnalyzer.DANGEROUS_IMPORTS`, cf. `services/security.py` — testé et
confirmé : `import socket` → `exit_code -999 "Code is unsafe"`, indépendant du fix pool).**

`pymysql`, `duckdb`, `requests` ne sont **pas** dans `DANGEROUS_IMPORTS` (liste :
`os, subprocess, sys, shutil, socket, ctypes, pickle, threading, multiprocessing, asyncio,
http.client, ftplib, telnetlib, builtins`) — `pymysql.connect(...)` est le test le plus propre
disponible : il ouvre un vrai socket TCP en interne (le check AST ne voit que le code utilisateur
soumis, pas ce que font les librairies importées), et distingue nettement un blocage réseau
(`OperationalError (2003, "Can't connect to MySQL server ...")` — échec du handshake TCP lui-même,
timeout/connexion refusée/host injoignable) d'un hôte réellement atteint (tout autre type
d'erreur, ex. handshake MySQL invalide car ce n'est pas un vrai serveur MySQL).

```
$ curl -s -X POST http://localhost:9385/run -H 'Content-Type: application/json' -d "$CODE3"
{"status":"success","stdout":"","stderr":"","exit_code":0,"detail":null,
 "time_used_ms":10273.4,
 "result":{"present":true,"value":
   "{\"network\": true, \"error_type\": \"OperationalError\",
     \"error\": \"(2013, 'Lost connection to MySQL server during query')\"}",
   "type":"json"}}
```

`(2013, 'Lost connection to MySQL server during query')` — le TCP a bien été établi vers
`1.1.1.1:53` depuis le conteneur sandbox (SYN/ACK reçu), puis la connexion a été coupée pendant
l'échange du handshake initial MySQL (attendu : `1.1.1.1:53` n'est pas un serveur MySQL, il ne
répond pas avec un greeting packet valide). Ce n'est **pas** un `(2003, "Can't connect")`, qui
aurait signé un blocage réseau (host injoignable / connexion refusée / timeout).

**Verdict réseau : `network: true` — accès réseau sortant confirmé depuis le conteneur sandbox.**
Le sandbox peut atteindre une IP/port externe en TCP ; reste à valider en Task 10, avec une vraie
cible MySQL (base Cyllene), que `pymysql.connect(host=<cyllene>, port=3306, ...)` complète un
handshake MySQL normal (pas seulement un TCP SYN/ACK) — ce test-ci prouve la connectivité réseau
brute, pas encore l'atteignabilité applicative de Cyllene spécifiquement.

### État final

- `agent/sandbox/executor_manager/core/container.py` patché (fallback Podman), image
  `sandbox-executor-manager:latest` rebuild avec le patch, conteneur manager recréé et **laissé
  up** — pool 6/6, `/healthz` → `{"status":"ok"}`.
- Aucun conteneur de test/probe laissé après les vérifications manuelles (tous lancés avec
  `--rm`).
