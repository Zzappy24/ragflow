# Design — Bulk sièges Code par email + SMTP panel

- **Date** : 2026-07-09 · **Statut** : validé (brainstorming)
- **Demandes** : (1) ajouter des sièges code en bulk par adresses email ; (2) envoyer les emails (invitations RAG + sièges code) directement au user via SMTP au lieu du lien affiché au front uniquement.
- **Décisions actées** : sièges = **clé seule** (pas de compte panel — use cases séparés, porte ouverte pour plus tard) · livraison par **lien de récupération one-time** · **la clé n'est générée qu'au claim** (zéro secret au repos ou en transit) · rotation = **action admin** (org admin / code-team admin), self-service différé.

## 1. Mailer du panel — `management/server/services/mailer.py`

- `aiosmtplib` (déjà dans les deps du repo ; l'ajouter à `management/server/requirements.txt` si absent). Le mailer upstream (`api/utils/web_utils.py::send_email_html`) dépend du contexte Quart → non réutilisable ; le panel a le sien, minimal.
- Config env (self-contained, pattern des autres settings) : `ADMIN_SMTP_HOST`, `ADMIN_SMTP_PORT`, `ADMIN_SMTP_USERNAME`, `ADMIN_SMTP_PASSWORD`, `ADMIN_SMTP_FROM` (format `Nom <adresse>`), `ADMIN_SMTP_TLS` (défaut true).
- API : `send_mail(to: str, subject: str, body_text: str) -> bool` (async ; False + warning loggé en cas d'échec — jamais d'exception vers l'appelant). `is_configured() -> bool` (host non vide).
- **Dégradation propre** : SMTP non configuré → tout fonctionne comme aujourd'hui (liens affichés au front), les réponses API portent `email_sent: false`.
- **Runbook prod** : SPF/DKIM sur le domaine expéditeur obligatoires avant mise en service (sinon spam). À documenter dans DEPLOYMENT_CHECKLIST.

## 2. Invitations RAG par email

- `POST /users` (management/server/routers/users.py) : après le mint de l'`invite_url`, envoie l'email (template FR inline : bienvenue + lien + expiration) en best-effort.
- Réponse enrichie : `{"invite_url": ..., "email_sent": bool}`. Front (page users/members) : badge « email envoyé ✓ » / « email non envoyé — transmettez le lien » à côté du lien existant (le lien reste toujours affiché, c'est le fallback).

## 3. Bulk sièges code — invitations + claim

### Table `code_key_invite` (bloc marqueurs CUSTOM B2B SaaS, db_models.py)
| Champ | Note |
|---|---|
| `id` char(32) PK | |
| `code_team_id` char(32) index | |
| `email` varchar(255) index | |
| `token_hash` char(64) unique | SHA-256 du token — le token en clair n'est JAMAIS stocké |
| `expires_at` datetime | création + 72 h (UTC) |
| `claimed_key_id` char(32) null | rempli au claim → invitation consommée |
| `created_by` char(32) | |

### Routes
- `POST /code/teams/{team_id}/keys/bulk` (org admin **ou** code-team admin — `require_code_team_admin`) — body `{"emails": [str]}` (validation format basique, dédup, max 200/appel). Pour chaque email : crée l'invite (token = `secrets.token_urlsafe(32)`, hash stocké), envoie l'email (claim_url + endpoint gateway + expiration). Retour : `[{email, invite_id, email_sent}]`. Audit `CODE_SEAT_INVITE` par lot.
- `POST /code/invites/{invite_id}/resend` (même RBAC) — regénère un token (nouveau hash, nouvelle expiration ; l'ancien lien meurt), renvoie l'email. Refusé si déjà claimed.
- `POST /code/keys/{key_id}/rotate` (même RBAC) — révoque la clé + crée une invitation pour `label` (=email) du même siège + envoie l'email. Audit `CODE_KEY_ROTATED`.
- **`POST /api/admin/public/code/claim`** (SANS auth) — body `{"token": str}` :
  1. `token_hash = sha256(token)` → lookup ; introuvable / expiré / `claimed_key_id` non null → **404 générique** (ne révèle jamais si un email/invite existe).
  2. **Claim atomique** : `UPDATE code_key_invite SET claimed_key_id='pending' WHERE id=? AND claimed_key_id IS NULL` — 0 ligne affectée = déjà claimé (double-clic, deux onglets) → 404 générique.
  3. Génère la clé à cet instant (`create_code_key(label=email, owner_user_id=user si un compte panel existe avec cet email, created_by=invite.created_by)`).
  4. **LiteLLM down** (plain_key None) → **rollback du claim** (`claimed_key_id = NULL`) + 503 « réessayez dans quelques minutes » — le lien reste valide, aucun token consommé sans clé livrée.
  5. Succès → `claimed_key_id = key.id` ; retour `{plain_key, gateway_url, label}` — affiché UNE fois.
  6. **Rate-limit** : compteur en mémoire par IP (ex. 10 req/min) sur cette route — brute-force déjà irréaliste (token 256 bits), c'est la ceinture. Note : in-process (suffisant mono-replica mgmt ; si scale-out, passer le compteur en DB/Redis).
- Config : `ADMIN_PANEL_PUBLIC_URL` (base des liens de claim, ex. `https://admin.cyllene.cloud`) — même pattern que `ADMIN_CODE_GATEWAY_PUBLIC_URL`.

### Front
- Vue team : bouton « Inviter des sièges » → modal textarea (un email par ligne) → résultats par email (✓ envoyé / ✗ échec SMTP + lien de claim affiché en fallback copiable).
- Liste des invitations pendantes sous la table des clés de la team (email, expire le, boutons Renvoyer / Annuler) — statut `claimed` disparaît de la liste (la clé apparaît dans la table).
- Ligne de clé : bouton **Rotate** (Popconfirm : « révoque la clé et envoie un nouveau lien à {email} »).
- **Page publique `/claim`** (route SANS auth dans le router du front, hors Layout admin) : lit `?token=`, POST au claim, affiche la clé one-time (même composant copiable que le modal existant) + l'endpoint + « configurez Kilo/OpenCode ». Erreur → message générique + « contactez votre administrateur ».

## 4. Sécurité (résumé)
Token 256 bits urlsafe, stocké hashé (SHA-256), usage unique (UPDATE conditionnel atomique), TTL 72 h, rate-limit IP sur la route publique, 404 générique (pas d'énumération), la clé n'existe nulle part avant le claim et n'est jamais persistée après (mêmes garanties que le flow actuel), audit complet (invite/resend/claim/rotate).

## Différé explicitement
- **Self-service dev** : « Bloquer ma clé » (lien one-way dans l'email de claim) et self-rotate par email — ~1 j chacun le jour où un client le demande, sans migration.
- **Comptes panel pour les devs** (use case séparé, décision actée).
- Templates HTML riches / i18n des emails (texte FR simple en v1).
- Rate-limit distribué (Redis) — mono-replica mgmt aujourd'hui.

## Tests
- **Unit** : mailer (mock SMTP, is_configured, échec → False) · création d'invites (hash stocké ≠ token, dédup, max 200) · claim atomique (2 claims concurrents → 1 seule clé) · claim expiré/déjà-claimé/token inconnu → 404 · LiteLLM down au claim → 503 + token intact.
- **Routes RBAC** : bulk/resend/rotate refusés au membre simple et cross-org ; claim public sans auth ; rate-limit (11ᵉ requête → 429).
- **Front** : build + drive Playwright (modal bulk, liste invites, page /claim happy path + token invalide).
