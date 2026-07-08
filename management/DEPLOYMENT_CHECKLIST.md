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
- [ ] Restreindre le port 8000 (admin panel) au réseau interne uniquement
