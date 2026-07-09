# Checklist de déploiement — Cyllene Admin Panel

Ce document liste les actions à effectuer **une seule fois** lors d'un déploiement en production.

---

## 1. Base de données

### 1.1 Migration de la table d'audit

Lors du premier démarrage, `init_database_tables()` crée automatiquement la table `cyllene_audit_log`.
Si la table `audit_log` (ancienne version) existe encore, la supprimer manuellement :

```sql
-- Vérifier qu'elle est vide avant de supprimer
SELECT COUNT(*) FROM audit_log;
DROP TABLE IF EXISTS audit_log;
```

### 1.2 Immutabilité de l'audit log (CRITIQUE)

Par défaut, l'utilisateur MySQL applicatif a des droits trop larges sur la table d'audit.
Pour garantir qu'aucun code (ni bug, ni compromission) ne puisse modifier ou effacer un log d'audit,
**révoquer `UPDATE` et `DELETE` sur `cyllene_audit_log`** pour l'utilisateur applicatif.

**Recommandation : créer un utilisateur MySQL dédié** avec des droits limités (pas `root`) :

```sql
-- Créer un user applicatif dédié (si pas encore fait)
CREATE USER 'cyllene_app'@'%' IDENTIFIED BY '<mot_de_passe_fort>';

-- Droits généraux sur la base
GRANT SELECT, INSERT, UPDATE, DELETE ON rag_flow.* TO 'cyllene_app'@'%';

-- Révoquer UPDATE et DELETE sur la table d'audit → immutabilité garantie au niveau DB
REVOKE UPDATE, DELETE ON rag_flow.cyllene_audit_log FROM 'cyllene_app'@'%';

FLUSH PRIVILEGES;
```

Puis mettre à jour `conf/service_conf.yaml` avec le nouvel utilisateur :

```yaml
mysql:
  user: 'cyllene_app'
  password: '<mot_de_passe_fort>'
  ...
```

> **Pourquoi ?** Un attaquant qui compromet le code applicatif ne pourra pas effacer ses traces.
> Un admin qui voudrait falsifier un log d'audit en sera physiquement incapable via l'application.
> Seul un accès DBA direct à la DB peut modifier les logs — ce qui est traçable au niveau infra.

---

## 2. Variables d'environnement

| Variable | Description | Exemple |
|---|---|---|
| `ADMIN_JWT_SECRET` | Clé secrète pour les JWT du panel admin | `openssl rand -hex 32` |
| `INVITE_TOKEN_EXPIRE_SECONDS` | Durée de validité des tokens d'invitation | `86400` (24h) |
| `RAGFLOW_BASE_URL` | URL publique de l'instance RAGFlow | `https://ragflow.example.com` |
| `DOC_ENGINE` | Moteur de documents | `infinity` ou `elasticsearch` |

---

## 3. Commandes de démarrage (dev — en attendant Docker)

### RAGFlow backend (port 9380)
```bash
cd /path/to/ragflow
PYTHONPATH=$(pwd) DOC_ENGINE=infinity ADMIN_JWT_SECRET=<secret> \
  PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \
  uv run python api/ragflow_server.py >> /tmp/ragflow.log 2>&1 &
```

### Admin panel backend (port 9381)
```bash
cd /path/to/ragflow/management
PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \
  RSA_PASSPHRASE=Welcome ADMIN_JWT_SECRET=<from .env.local> uv run uvicorn server.main:app --host 0.0.0.0 --port 9381 --reload  # 9381 = cible du proxy vite
```
> `--reload` recharge automatiquement les fichiers `management/` mais **pas** les fichiers `api/`.
> Toute modification dans `api/` nécessite un redémarrage manuel du RAGFlow backend.

### Admin panel frontend (port 5173)
```bash
cd /path/to/ragflow/management/web
PATH=/opt/homebrew/bin:$PATH npm run dev
```

---

## 4. Sécurité production

- [ ] Changer tous les mots de passe par défaut (`MYSQL_PASSWORD`, `MINIO_PASSWORD`, etc.)
- [ ] `REGISTER_ENABLED=0` dans la config RAGFlow (self-service désactivé, invitations uniquement)
- [ ] Créer un user MySQL applicatif dédié (voir §1.2)
- [ ] Révoquer `UPDATE/DELETE` sur `cyllene_audit_log` (voir §1.2)
- [ ] Configurer HTTPS sur les deux backends
- [ ] Restreindre le port 8000 (admin panel) au réseau interne uniquement — **sauf** les deux routes
      publiques du produit Code, qui doivent rester accessibles depuis Internet : `/admin/claim`
      (page front) et `/api/admin/public/code/claim` (API). Les invités du produit Code n'ont pas
      de compte panel — c'est le lien d'invitation lui-même qui fait office de credential (cf. §5.d).
      Seule la surface authentifiée (tout le reste du panel) peut être restreinte au réseau interne.

---

## 5. Produit Code — prérequis prod

### a. Variables d'environnement SMTP + URLs publiques

| Variable | Description | Exemple |
|---|---|---|
| `ADMIN_SMTP_HOST` | Hôte SMTP sortant. **Vide = mode dégradé** : les emails d'invitation ne partent pas, le front affiche le lien de claim directement à l'admin (dev only, jamais souhaitable en prod). | `smtp.sendgrid.net` |
| `ADMIN_SMTP_PORT` | Port SMTP. `587` = STARTTLS (recommandé), `465` = TLS implicite. | `587` |
| `ADMIN_SMTP_USERNAME` | Identifiant SMTP. | `apikey` |
| `ADMIN_SMTP_PASSWORD` | Mot de passe / clé API SMTP — à stocker en secret (K8s Secret), jamais en clair dans les manifests. | `<secret>` |
| `ADMIN_SMTP_FROM` | Adresse expéditeur affichée. | `Cyllene <no-reply@cyllene.com>` |
| `ADMIN_SMTP_TLS` | `true` pour STARTTLS sur le port 587 ; à adapter si le port 465 (TLS implicite) est utilisé. | `true` |
| `ADMIN_PANEL_PUBLIC_URL` | Base publique du panel, sert à construire les liens de claim envoyés par email. **Vide = liens relatifs (dev only)** — en prod, un lien relatif dans un email n'a pas de sens (pas de contexte d'origine), donc cette variable est **obligatoire** dès que `ADMIN_SMTP_HOST` est renseigné. | `https://admin.cyllene.cloud` |
| `ADMIN_CODE_GATEWAY_PUBLIC_URL` | URL publique de la gateway LiteLLM affichée aux utilisateurs (à configurer dans Kilo Code / OpenCode / Cline). Vide = masqué dans l'UI. | `https://code.cyllene.cloud/v1` |

### b. SPF/DKIM/DMARC — À FAIRE AVANT d'activer le SMTP

Avant de renseigner `ADMIN_SMTP_HOST` en prod, configurer sur le domaine expéditeur (celui
utilisé dans `ADMIN_SMTP_FROM`) :
- **SPF** : autoriser l'IP/serveur du fournisseur SMTP à émettre pour ce domaine.
- **DKIM** : signature cryptographique des emails sortants (clé fournie par le fournisseur SMTP).
- **DMARC** : politique d'alignement SPF/DKIM (`p=quarantine` ou `p=reject` recommandé).

Sans ces trois enregistrements DNS, les emails d'invitation atterrissent en spam ou sont
purement et simplement rejetés par les fournisseurs (Gmail, Outlook, etc.) — les invités ne
recevront jamais leur lien de claim.

### c. Chaîne X-Forwarded-For / X-Real-IP

Le rate-limiter de `/api/admin/public/code/claim` (voir `management/server/routers/code.py::_client_ip`)
préfère désormais **X-Real-IP** à X-Forwarded-For : notre nginx pose `X-Real-IP` via `$remote_addr`
(overwrite, non falsifiable) alors qu'il **append** à `X-Forwarded-For` via `proxy_add_x_forwarded_for`
(un client peut préfixer XFF avec une IP arbitraire → contournement du rate limit ou DoS 429 ciblé
sur l'IP d'une victime).

**Vérifier après tout changement d'ingress/proxy devant le panel** :
- [ ] Chaque hop devant le mgmt (ingress K8s, load balancer, nginx) **écrase** `X-Real-IP` avec
      l'IP réelle du client à ce hop — jamais un append.
- [ ] Si l'ingress ne pose pas `X-Real-IP`, le code retombe sur le premier hop de XFF puis sur
      le socket brut — vérifier dans ce cas que XFF est bien reconstruit (overwrite) par chaque
      proxy de la chaîne, pas juste le dernier.

### d. `/admin/claim` et `/api/admin/public/code/claim` doivent rester publics

Contrairement au reste du panel (routes authentifiées, restreignables au réseau interne — voir
§4), ces deux routes sont **volontairement non authentifiées** : le lien d'invitation (token à
usage unique) est lui-même le credential. Les invités du produit Code n'ont pas de compte sur le
panel admin — restreindre ces routes au réseau interne empêcherait tout claim de siège
fonctionnel. Seule la surface authentifiée doit être IP-restreinte.

### e. Rate-limiter en mémoire — mono-réplica

Le rate-limiter de `/api/admin/public/code/claim` est **in-process** (dict Python, pas de
backend partagé — voir `_CLAIM_HITS` dans `code.py`). Il fonctionne correctement tant que le
mgmt-backend tourne en **un seul replica**. Si le panel est scalé horizontalement (>1 replica),
chaque pod aura son propre compteur indépendant — le rate limit effectif devient `limite × nb_replicas`.
**Avant tout scale-out du mgmt-backend, migrer ce compteur vers Redis** (partagé entre replicas).

### f. Provisioning au boot — pas de SQL manuel requis

- La table `code_key_invite` (et les autres tables du produit Code) est créée automatiquement au
  premier démarrage du mgmt-backend via `init_database_tables()` — aucune migration SQL manuelle
  à exécuter.
- `aiosmtplib==5.0.0` est pinné dans `Dockerfile.management` — aucune installation manuelle requise
  dans l'image de production.
