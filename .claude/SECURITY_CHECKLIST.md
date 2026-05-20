# Checklist Sécurité — Multi-Tenant SaaS RAGFlow
> Audit du 2026-04-21 — branche `dev`

Statut : ` ` = à faire · `[x]` = corrigé · `[~]` = en cours · `[!]` = accepté/risque conscient

---

## 🔴 CRITIQUE — Bloquer la mise en production

### AUTH-01 · RSA passphrase "Welcome" hardcodée + clés dans le repo
- **Fichier** : `api/utils/crypt.py:28` — `_RSA_PASSPHRASE = os.environ.get("RSA_PASSPHRASE", "Welcome")`
- **Fichier** : `.gitignore:7-8` — les clés `conf/private.pem` et `conf/public.pem` sont intentionnellement commitées
- **Risque** : N'importe qui ayant accès au repo peut déchiffrer tous les mots de passe envoyés par le front.
- **Fix** : En production, forcer `RSA_PASSPHRASE` (sans fallback), régénérer les clés avec `scripts/init_rsa_keys.sh`, ne **jamais** committer les clés de prod.
- [x] Fix appliqué — `RuntimeError` si `RSA_PASSPHRASE` absent ; dev: `RSA_PASSPHRASE=Welcome`

### AUTH-02 · PKCS1_v1_5 (vulnérable à Bleichenbacher)
- **Fichier** : `api/utils/crypt.py:22,47` — `Cipher_pkcs1_v1_5`
- **Risque** : Attaque oracle de déchiffrement possible ; standard abandonné depuis TLS 1.3.
- **Fix** : Migrer vers PKCS1_OAEP (`from Cryptodome.Cipher import PKCS1_OAEP`).
- [!] **Migration frontend requise** — JSEncrypt (frontend) ne supporte que PKCS1_v1_5. Migration = remplacer JSEncrypt par `node-forge` ou Web Crypto API (OAEP). À planifier comme tâche dédiée frontend+backend.

### CORS-01 · `allow_origin="*"` en production
- **Fichier** : `api/apps/__init__.py:69`
- **Risque** : Tout site peut faire des requêtes authentifiées depuis le navigateur d'un user connecté (CSRF-like).
- **Fix** : Lire `CORS_ORIGINS` env var (liste de domaines séparés par virgule).
- [x] Fix appliqué — warning si non défini ; prod : `CORS_ORIGINS=https://app.domain.com,https://admin.domain.com`

### RATELIMIT-01 · Aucun rate-limit sur les endpoints auth
- **Endpoints** : `POST /user/login`, `POST /user/register`, `POST /user/password/reset`
- **Risque** : Brute-force des mots de passe sans délai ni blocage.
- **Fix** : Rate-limit Redis par IP ou email.
- [x] Fix appliqué — login : 10/h par email (existait déjà) ; register : 10/h par IP ; reset-password : 5/h par email

### APIKEY-01 · `@apikey_required` bypasse les scopes `ApiKeyScope`
- **Fichier** : `api/utils/api_utils.py:261-278`
- **Risque** : Les routes legacy `@apikey_required` ignorent l'expiration, le statut et les permissions `ApiKeyScope`.
- **Fix** : Fusionner la logique scope dans `apikey_required`.
- [x] Fix appliqué — expiration + statut vérifiés, `touch_last_used` appelé

### SQLI-01 · SQL généré par LLM exécuté directement
- **Fichier** : `api/db/services/dialog_service.py:997-1004`
- **Risque** : Un user malveillant peut injecter via le prompt LLM un SQL arbitraire exécuté sur le doc-engine (Infinity/OceanBase).
- **Fix** : Blocklist DDL/DML dans `normalize_sql()`.
- [x] Fix appliqué — regex `_SQL_FORBIDDEN` bloque DROP/ALTER/CREATE/INSERT/UPDATE/DELETE/TRUNCATE/REPLACE/GRANT/REVOKE/EXEC/CALL/MERGE

---

## 🟡 MODÉRÉ — À corriger avant GA

### RBAC-01 · Fallback "no workspace → no filter" silencieux
- **Fichier** : `api/apps/extensions/rbac.py:169-170` et `rbac.py:138-139`
- **Risque** : Si un `tenant_id` n'a pas de workspace associé, `get_visible_dataset_ids()` retourne `None` (pas de filtre) et `has_permission()` log un warning mais **refus** — comportement incohérent selon le code path.
- **Fix** : Standardiser : absence de workspace = refus systématique.
- [x] Fix appliqué — `get_visible_dataset_ids` retourne `set()` vide (deny-all) au lieu de `None` ; log warning

### RBAC-02 · Routes chat sans `@require_permission`
- **Fichier** : `api/apps/restful_apis/chat_api.py` lignes 819, 843, 908, 936, 994, 1012, 1050
- **Risque** : Ces routes ont `@login_required` mais aucune vérification de permission RBAC.
- **Fix** : Ajouter `@require_permission(Permission.CHAT_*)` adapté à chaque route.
- [x] Fix appliqué — `CHAT_DELETE` sur delete_session_message ; `CHAT_USE` sur feedback, tts, transcriptions, mindmap, related_questions, session_completion

### COOKIE-01 · `SESSION_COOKIE_SECURE=false` par défaut
- **Fichier** : `api/apps/__init__.py:90`
- **Risque** : Cookie de session transmis en clair si HTTPS non forcé.
- **Fix** : Valeur par défaut à `"true"`.
- [x] Fix appliqué — default `"true"` ; dev local : `COOKIE_SECURE=false`

### SECRET-01 · `SECRET_KEY` stockée dans Redis (pas env var)
- **Fichier** : `common/settings.py:141-156` — code env var commenté
- **Risque** : Flush Redis = invalidation de tous les tokens JWT actifs ; Redis compromis = forge de tokens.
- **Fix** : Priorité à `RAGFLOW_SECRET_KEY` env var ≥ 32 chars.
- [x] Fix appliqué — env var prioritaire avec warning si absente, fallback Redis conservé

### CSP-01 · Absence de Content-Security-Policy
- **Fichier** : `api/apps/__init__.py:106-114`
- **Risque** : XSS côté front non mitigé au niveau HTTP.
- **Fix** : Ajouter header CSP dans `set_security_headers`.
- [x] Fix appliqué — CSP permissif (unsafe-inline/eval conservés pour React) ; à durcir progressivement

### DOS-01 · `MAX_CONTENT_LENGTH` = 1 Go par défaut
- **Fichier** : `api/apps/__init__.py:91-93`
- **Risque** : Un user peut uploader 1 Go par requête → DoS facile.
- **Fix** : Ramener à `128 * 1024 * 1024`.
- [x] Fix appliqué — 128 Mo par défaut, surchargeable via `MAX_CONTENT_LENGTH` env var

### PWD-01 · Aucune politique de complexité de mot de passe
- **Fichier** : `api/apps/user_app.py` — `POST /user/register` + changement mot de passe
- **Risque** : "a" est un mot de passe valide.
- **Fix** : `_validate_password_strength()` — ≥ 8 chars, 1 majuscule, 1 chiffre.
- [x] Fix appliqué — validé à l'enregistrement et au changement de mot de passe

---

## 🟢 FAIBLE — Bonnes pratiques / durcissement

### AUDIT-01 · `WsGroupMember` et `WsGroupDataset` sans soft-delete
- **Fichier** : `api/db/db_models.py:1438-1453`
- **Risque** : Impossible d'auditer l'historique des accès aux groupes (requis RGPD + forensics).
- **Fix** : Ajouter colonne `status` + timestamps sur ces deux tables.
- [ ] Fix appliqué

### LOG-01 · Pas d'effacement des logs de tentatives d'auth échouées
- **Risque** : Les IPs des attaquants ne sont pas bannies ni alertées.
- **Fix** : Intégrer alerting sur N échecs consécutifs (Redis counter par IP).
- [ ] Fix appliqué

### HSTS-01 · HSTS conditionnel seulement
- **Fichier** : `api/apps/__init__.py:112-113`
- **Info** : HSTS activé uniquement si `COOKIE_SECURE=true`. OK si cette env var est bien positionnée en prod.
- [ ] Vérifié en prod

### MCP-01 · Timeout MCP à 10s — pas de sandbox
- **Fichier** : `api/utils/api_utils.py:771-800`
- **Info** : Les outils MCP externes s'exécutent sans isolation. Un MCP malveillant peut faire des requêtes réseau arbitraires.
- **Fix** : Documenter que seuls les MCPs de confiance doivent être configurés ; envisager un proxy réseau restrictif.
- [ ] Décision documentée

---

## Ordre de traitement recommandé

| Priorité | ID | Effort | Impact |
|----------|----|--------|--------|
| 1 | AUTH-01 | 30 min | Critique |
| 2 | AUTH-02 | 1h | Critique |
| 3 | CORS-01 | 30 min | Critique |
| 4 | RATELIMIT-01 | 2h | Critique |
| 5 | APIKEY-01 | 1h | Critique |
| 6 | SQLI-01 | 2h | Critique (audit) |
| 7 | COOKIE-01 | 15 min | Modéré |
| 8 | SECRET-01 | 30 min | Modéré |
| 9 | CSP-01 | 1h | Modéré |
| 10 | DOS-01 | 5 min | Modéré |
| 11 | PWD-01 | 30 min | Modéré |
| 12 | RBAC-01 | 1h | Modéré |
| 13 | RBAC-02 | 1h | Modéré |
| 14 | AUDIT-01 | 2h | Faible |
| 15 | LOG-01 | 2h | Faible |
