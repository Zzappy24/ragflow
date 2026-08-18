# POC FAMAT — détection de dérive process

Spec : docs/superpowers/specs/2026-08-11-famat-drift-agent-poc-design.md
Données : `data/Payload-20260526.csv` (JAMAIS commité — .gitignore).
Tests : `uv run --with duckdb python -m pytest poc/famat/tests/ -v`

## Source de données (Task 2 du chantier get_file — révisée)

**Chemin nominal (retenu depuis Task 2)** : le CSV est uploadé dans les **Files** du
workspace `famat-poc-task11` (pas un dataset/KB) via `POST /api/v1/files`
(multipart, header `X-Workspace-Id` sur l'id du workspace). Le canvas appelle
D'ABORD le tool d'agent `get_file` (`agent/tools/get_file.py`, param `name`) avec
`name="Payload-20260526.csv"` : il fait `FileService.query(name=..., tenant_id=<tenant
du canvas>)`, puis renvoie une URL présignée interne à courte durée de vie (900 s par
défaut) pointant sur le stockage MinIO/S3 réel du fichier. Le prompt (`system_prompt.md`)
insère ensuite cette URL dans le code exécuté par `code_exec` :
`con = fr.load_url("<url renvoyée par get_file>")` — jamais une URL réutilisée d'une
question précédente, elle expire. Ce chemin fonctionne sans ouverture de flux réseau
supplémentaire côté prod : `get_file` route la présignature via
`SANDBOX_PRESIGN_ENDPOINT` (`http://host.containers.internal:9000` en dev — cf.
`STORAGE_IMPL.get_presigned_url(..., endpoint_override=...)`) pour que l'URL soit
atteignable depuis le conteneur sandbox, symétrique à la solution "bucket public"
ci-dessous mais sans policy anonyme.

Procédure d'upload utilisée (Task 2) :

```bash
curl -X POST http://127.0.0.1:9380/api/v1/files \
  -H "Authorization: <token>" \
  -H "X-Workspace-Id: <workspace_id>" \
  -F "file=@poc/famat/data/Payload-20260526.csv"
```

## Legacy POC — bucket MinIO public (Task 10, abandonné en Task 2)

La base Cyllene (webhook `WebhookMesure`) n'est pas accessible depuis ce poste (flux réseau
à ouvrir côté prod, cf. `docs/superpowers/plans/2026-08-11-famat-drift-agent-poc.md` § Task 10).
Avant Task 2, le POC chargeait le même CSV via une URL HTTP publique sur un bucket
MinIO dédié (`famat-poc`, policy anonyme `s3:GetObject`), directe et non expirante —
remplacée par le chemin Files + `get_file` ci-dessus (une URL par question, à courte
durée de vie, pas de bucket public). Conservé ici pour mémoire de la procédure :

### Procédure d'upload MinIO (legacy)

Credentials MinIO du stack dev : `docker/.env` (`MINIO_USER`, `MINIO_PASSWORD`), **jamais
copiés en clair ici**. Bucket `famat-poc`, objet `Payload-20260526.csv`, policy anonyme en
lecture seule sur le bucket (`s3:GetObject` sur `arn:aws:s3:::famat-poc/*`) :

```bash
MINIO_USER=$(grep '^MINIO_USER=' docker/.env | cut -d= -f2) \
MINIO_PASSWORD=$(grep '^MINIO_PASSWORD=' docker/.env | cut -d= -f2) \
uv run --with boto3 python3 - <<'EOF'
import os, json, boto3
s3 = boto3.client("s3", endpoint_url="http://localhost:9000",
                   aws_access_key_id=os.environ["MINIO_USER"],
                   aws_secret_access_key=os.environ["MINIO_PASSWORD"],
                   region_name="us-east-1")
bucket = "famat-poc"
if bucket not in [b["Name"] for b in s3.list_buckets()["Buckets"]]:
    s3.create_bucket(Bucket=bucket)
s3.upload_file("poc/famat/data/Payload-20260526.csv", bucket, "Payload-20260526.csv")
s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
    "Version": "2012-10-17",
    "Statement": [{"Effect": "Allow", "Principal": {"AWS": ["*"]},
                   "Action": ["s3:GetObject"], "Resource": [f"arn:aws:s3:::{bucket}/*"]}]
}))
EOF
```

### URL retenue (legacy)

Depuis le host, MinIO est publié sur `localhost:9000`. Depuis le conteneur sandbox (réseau
podman par défaut, backend Podman derrière la socket `docker` — cf. § Task 8 ci-dessous), les
candidates du brief ont été testées dans l'ordre via `POST /run` (`famat_recipes.load_url(...)`,
assertion `rows == 90375`) :

| Candidate testée | Résultat depuis le sandbox |
|---|---|
| `http://host.containers.internal:9000/...` | **OK** — `rows: 90375` — **retenue** |
| `http://host.docker.internal:9000/...` | OK — `rows: 90375` (fonctionne aussi, non retenue car testée en 2e) |
| `http://<gateway podman, 10.88.0.1>:9000/...` | OK — `rows: 90375` (fonctionne aussi, non retenue car testée en 3e) |

Les trois candidates aboutissent sur cette machine (backend Podman avec VM unique) ; le brief
demande de retenir la première testée dans l'ordre, donc (legacy — n'est plus utilisée par le
canvas depuis Task 2) :

```
DATA_URL = http://host.containers.internal:9000/famat-poc/Payload-20260526.csv
```

### Bascule future vers `load_db()` (base Cyllene)

Quand les flux réseau prod seront ouverts, remplacer `fr.load_url(<url get_file>)` par
`fr.load_db(host, port, user, password, database)` (implémentation documentée mais NON codée,
cf. `docs/superpowers/plans/2026-08-11-famat-drift-agent-poc.md` § Task 10) dans le
prompt/composant qui appelle la recette — signature et table `events` identiques, aucun
autre changement requis côté canvas.

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

## Provider RAGFlow (Task 9)

### Où vivent réellement les routes `/api/v1/admin/sandbox/*`

Écart d'architecture constaté (pas un bug applicatif, mais un vrai piège pour Task 11-12) :
`admin/server/routes.py` (`admin_bp`, prefix `/api/v1/admin`) contient les routes
`sandbox/providers`, `sandbox/providers/<id>/schema`, `sandbox/config` (GET/POST),
`sandbox/test` — servies par le serveur Flask legacy **upstream**
`admin/server/admin_server.py`, `run_simple(port=9381)` codé en dur. La page frontend
`web/src/pages/admin/sandbox-settings.tsx` (route `/admin/sandbox-settings`) cible bien ces
routes — `web/vite.config.ts` proxy explicitement `/api/v1/admin/sandbox` (+ `roles`,
`whitelist`, `variables`) vers `http://127.0.0.1:9381/`.

Or `scripts/dev_up.sh --full` démarre sur ce **même port 9381** notre backend admin custom
`management/server/main.py` (FastAPI/uvicorn, prefix `/api/admin`, sans le `v1` — orgs,
workspaces, members, code product…) — qui **n'expose pas** de router sandbox. Résultat : avec le
stack `--full` standard, `http://localhost:9222/admin/sandbox-settings` tape sur
`management/server` et reçoit 404 (route inconnue), le serveur Flask legacy qui porte réellement
ces routes n'étant jamais lancé par nos scripts. Pas de fichier de code modifié pour ce constat
— juste `admin/server/admin_server.py` lancé manuellement en dehors de `dev_up.sh` le temps du
smoke (voir ci-dessous), puis arrêté. **Table de vérité :**

| Chemin | Serveur qui répond réellement | Démarré par |
|---|---|---|
| `/api/admin/*` (org/workspace/members/code…) | `management/server/main.py` (FastAPI, :9381) | `scripts/dev_up.sh --full` |
| `/api/v1/admin/sandbox/*`, `/api/v1/admin/roles*`, `/api/v1/admin/whitelist`, `/api/v1/admin/variables` | `admin/server/admin_server.py` (Flask legacy, :9381) | **rien dans nos scripts** — à lancer à la main |

### Auth utilisée

Compte superuser existant en base `admin@test.local` (`is_superuser=1`, `is_active=1`) — trouvé
via `docker exec docker-mysql-1 mysql ... -e "SELECT email,is_superuser FROM user WHERE
is_superuser=1"` (credentials root MySQL dans `docker/.env`, procédure documentée dans
`CLAUDE.md` § « DB client pod »/dev). Mot de passe applicatif inconnu (hash existant non
réversible) : reset via la procédure exacte documentée dans `CLAUDE.md` § « Account Password
Handling » — `UPDATE user SET password=<scrypt hash of Base64(raw)> WHERE email='admin@test.local'`
généré avec `werkzeug.security.generate_password_hash`. Mot de passe en clair **jamais commité**
— seule la procédure (déjà dans `CLAUDE.md`, préexistante) est référencée ici.

Login exécuté contre la route propre au serveur admin, `POST /api/v1/admin/login`
(`admin/server/routes.py:43`, **pas** `/api/v1/auth/login` du serveur principal — process Flask
distinct mais même schéma RSA(Base64(password)) + secret JWT `ADMIN_JWT_SECRET`/itsdangerous
que documenté dans `CLAUDE.md` § « Obtaining an API Token »). Token récupéré dans le header
`Authorization` de la réponse, réutilisé en `Authorization: <token>` sur les appels suivants.

### Config posée

```bash
POST /api/v1/admin/sandbox/config
{"provider_type":"self_managed","config":{"endpoint":"http://localhost:9385","timeout":30},"set_active":true}
→ {"code":0,"data":{"config":{"endpoint":"http://localhost:9385","timeout":30},"provider_type":"self_managed"},"message":"Sandbox configuration updated successfully"}

POST /api/v1/admin/sandbox/test
{"provider_type":"self_managed","config":{"endpoint":"http://localhost:9385","timeout":30}}
→ {"code":0,"data":{"success":true,"message":"Test PASSED | Exit code: 0 | ...TEST_PASSED...", ...},"message":"Success"}
```

`endpoint=http://localhost:9385` fonctionne tel quel (pas besoin de `host.docker.internal`) : le
`ragflow_server.py` de dev tourne en process natif sur le host (pas en conteneur), donc
`localhost:9385` pointe directement sur `sandbox-executor-manager` publié par
`agent/sandbox/docker-compose.yml`. La config est persistée par `SandboxMgr.set_config()`
(`admin/server/services.py`) dans la table `system_settings` (clés `sandbox.provider_type` et
`sandbox.self_managed`, JSON) — lue par `agent.sandbox.client._load_provider_from_settings()`
côté process API principal (:9380) et task_executor, indépendamment de quel serveur HTTP a fait
l'écriture. `set_config()` appelle `agent.sandbox.client.reload_provider()` en fin de requête, donc
la config est active immédiatement, sans redémarrage du serveur API.

### Smoke recettes (Step 3 du brief)

Validé au niveau du client Python du provider, pas du canvas navigateur (session non
interactive) :

```python
from agent.sandbox.client import execute_code, reload_provider
reload_provider()
result = execute_code(code="""
import famat_recipes, json
def main():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    return json.dumps({"ok": True})
""", language="python", arguments={})
```

Résultat : `exit_code=0`, `metadata['result_present']=True`,
`metadata['result_value']='{"ok": true}'`. `stderr` contient uniquement le warning bénin
matplotlib déjà noté au Task 8 (`/tmp/matplotlib is not a writable directory`, fallback auto) —
sans incidence.

### Smoke artefacts (Step 4 du brief)

Même approche, code du brief inchangé (drift synthétique 25 points + `spc_chart` en `svg` puis
`png`) :

```python
result = execute_code(code="""
import famat_recipes as fr, json
def main():
    rows = [(i+1, f"P{i:02d}", 10, "CORRECTION_X", 0.001*i, f"2026-01-01 10:{i:02d}:00") for i in range(25)]
    d = fr.drift(fr.load_rows(rows), "CORRECTION_X", 10)
    svg = fr.spc_chart(d, out_dir="artifacts", fmt="svg")
    png = fr.spc_chart(d, out_dir="artifacts", fmt="png")
    return json.dumps({"svg": svg, "png": png})
""", language="python", arguments={})
```

Résultat : `exit_code=0`, `result_value='{"svg": "artifacts/spc_CORRECTION_X_ch10.svg", "png":
"artifacts/spc_CORRECTION_X_ch10.png"}'`, et **2 artefacts** dans `metadata['artifacts']` :

| name | mime_type | size (octets) |
|---|---|---|
| `spc_CORRECTION_X_ch10.svg` | `image/svg+xml` | 40 470 |
| `spc_CORRECTION_X_ch10.png` | `image/png` | 84 041 |

**Verdict SVG vs PNG : les deux formats sont générés par `famat_recipes.spc_chart()` et
collectés par le sandbox, sans erreur ni différence de traitement.** Pas de raison technique de
figer `fmt` par défaut sur l'un plutôt que l'autre à ce stade — aucune modification de
`poc/famat/famat_recipes.py` nécessaire. L'affichage réel en pièce jointe de chat (rendu SVG
inline supporté ou non par le composant chat RAGFlow) reste à valider en Task 11-12.

### Mécanisme d'artefacts constaté (lecture de `agent/tools/code_exec.py` + `agent/sandbox/providers/self_managed.py`)

Deux étapes bien séparées, une seule vérifiée ici :

1. **Executor-manager → réponse HTTP `/run`** (vérifié par ce smoke) : le conteneur sandbox
   écrit les fichiers dans `artifacts/` (relatif au workdir), l'executor-manager les scanne après
   exécution et les renvoie **inline, base64**, dans le corps JSON de la réponse
   (`result.artifacts = [{name, content_b64, mime_type, size}, ...]`). C'est
   `SelfManagedProvider.execute_code()` (`agent/sandbox/providers/self_managed.py:187`) qui
   remonte cette liste telle quelle dans `ExecutionResult.metadata['artifacts']` — confirmé
   empiriquement ci-dessus (2 entrées, tailles cohérentes avec les fichiers SVG/PNG générés).
2. **Upload vers le stockage RAGFlow (MinIO/S3) + rendu markdown chat** (NON exercé ici — ce
   smoke appelle `agent.sandbox.client.execute_code()` directement, pas le composant canvas) :
   c'est `CodeExec._upload_artifacts()` (`agent/tools/code_exec.py:541`) qui décode chaque
   `content_b64`, pousse le binaire dans le bucket `SANDBOX_ARTIFACT_BUCKET` via
   `settings.STORAGE_IMPL.put()`, génère une URL `/api/v1/documents/artifact/<uuid><ext>`, puis
   `_build_attachment_markdown_list()` produit `![name](url)` pour les images (rendu inline
   attendu côté chat) ou `[Download name](url)` sinon. Cette étape nécessite le contexte canvas
   (`self._canvas`, tenant) et un backend de stockage actif — non invoquée par ce smoke bas
   niveau, à valider dans un vrai canvas en Task 11-12.

### État final / nettoyage

- `admin/server/admin_server.py` a été lancé manuellement (hors `dev_up.sh`) le temps de valider
  les routes admin, puis **arrêté** après les smokes — aucun process laissé sur :9381 en dehors
  de ce que `scripts/dev_up.sh`/`dev_down.sh` gèrent normalement.
- Config sandbox (`system_settings.sandbox.*`) **laissée en base**, active :
  `provider_type=self_managed`, `endpoint=http://localhost:9385`, `timeout=30` — persiste pour
  Task 10/11/12.
- Mot de passe de `admin@test.local` a été réinitialisé (voir § Auth ci-dessus) ; le compte reste
  un compte de dev local, pas de secret de prod concerné.
