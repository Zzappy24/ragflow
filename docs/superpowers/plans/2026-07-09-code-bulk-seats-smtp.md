# Bulk Seats + SMTP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inviter des sièges code en bulk par email (clé générée au claim, lien one-time) et envoyer les emails d'invitation (RAG + code) via SMTP.

**Architecture:** Un mailer async minimal dans le panel (env-driven, dégradation propre si non configuré). Les invitations de siège sont des rows `code_key_invite` (token hashé, TTL 72 h) ; la clé LiteLLM n'existe qu'après le claim public atomique. Rotation = révoque + nouvelle invitation.

**Tech Stack:** aiosmtplib (déjà dans pyproject ; à ajouter au requirements.txt slim du mgmt), FastAPI, Peewee, React/antd.

**Spec:** `docs/superpowers/specs/2026-07-09-code-bulk-seats-smtp-design.md`

## Global Constraints

- Le token de claim n'est JAMAIS stocké en clair (SHA-256 hex dans `token_hash`) ; la clé API n'existe nulle part avant le claim et n'est jamais persistée après.
- Claim **atomique** : `UPDATE ... WHERE claimed_key_id IS NULL` conditionnel — 0 ligne = déjà claimé.
- LiteLLM down au claim → **rollback** (`claimed_key_id=NULL`) + 503 — le lien reste valide.
- Route publique : **404 générique** pour token inconnu/expiré/consommé (pas d'énumération) ; rate-limit 10 req/min/IP (in-process).
- SMTP non configuré → tout marche comme avant, `email_sent: false` partout, liens affichés au front.
- Emails : texte FR simple. Config env : `ADMIN_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/TLS`, `ADMIN_PANEL_PUBLIC_URL`.
- Tests robustes aux données réelles de la DB partagée (asserts scopés, pattern des tests code existants) ; env `ADMIN_JWT_SECRET` depuis `/Users/zappy/ragflow/.env.local`.
- Commits français conventionnels, JAMAIS de Co-Authored-By.

---

### Task 1: Mailer SMTP du panel

**Files:**
- Create: `management/server/services/mailer.py`
- Modify: `management/server/config.py` (bloc SMTP + `ADMIN_PANEL_PUBLIC_URL`), `management/server/requirements.txt` (+`aiosmtplib>=5.0.0`)
- Test: `test/multitenant/test_code_mailer.py`

**Interfaces:**
- Produces: `async send_mail(to: str, subject: str, body_text: str) -> bool` (False + `logger.warning` sur toute erreur — ne lève jamais) ; `is_configured() -> bool` (True si `ADMIN_SMTP_HOST` non vide).

- [ ] **Step 1: Config** (append aux settings, style existant)

```python
    # SMTP du panel (invitations RAG + sièges code). Vide = emails désactivés,
    # les liens restent affichés au front. Prod : SPF/DKIM requis sur le
    # domaine expéditeur (cf. DEPLOYMENT_CHECKLIST).
    SMTP_HOST: str = os.getenv("ADMIN_SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("ADMIN_SMTP_PORT", "587"))
    SMTP_USERNAME: str = os.getenv("ADMIN_SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("ADMIN_SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("ADMIN_SMTP_FROM", "Cyllene <no-reply@cyllene.com>")
    SMTP_TLS: bool = os.getenv("ADMIN_SMTP_TLS", "true").lower() == "true"
    # Base publique du panel — sert à construire les liens de claim,
    # ex. "https://admin.cyllene.cloud". Vide = liens relatifs (dev).
    PANEL_PUBLIC_URL: str = os.getenv("ADMIN_PANEL_PUBLIC_URL", "")
```

- [ ] **Step 2: Failing tests**

```python
# test/multitenant/test_code_mailer.py
"""Mailer du panel — mock aiosmtplib, zéro réseau."""
import asyncio
import pytest

pytestmark = pytest.mark.p1


def test_not_configured_returns_false(monkeypatch):
    from management.server.config import settings
    from management.server.services import mailer
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    assert mailer.is_configured() is False
    assert asyncio.run(mailer.send_mail("a@b.c", "s", "b")) is False


def test_send_mail_success(monkeypatch):
    from management.server.config import settings
    from management.server.services import mailer
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    sent = {}

    class FakeSMTP:
        def __init__(self, **kw): sent["kw"] = kw
        async def connect(self): pass
        async def login(self, u, p): sent["login"] = (u, p)
        async def send_message(self, msg): sent["msg"] = msg
        async def quit(self): pass

    monkeypatch.setattr(mailer.aiosmtplib, "SMTP", FakeSMTP)
    ok = asyncio.run(mailer.send_mail("dev@client.fr", "Sujet", "Corps"))
    assert ok is True
    assert sent["msg"]["To"] == "dev@client.fr"
    assert "Sujet" in str(sent["msg"]["Subject"])


def test_send_mail_failure_returns_false_never_raises(monkeypatch):
    from management.server.config import settings
    from management.server.services import mailer
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")

    class BoomSMTP:
        def __init__(self, **kw): pass
        async def connect(self): raise OSError("refused")

    monkeypatch.setattr(mailer.aiosmtplib, "SMTP", BoomSMTP)
    assert asyncio.run(mailer.send_mail("a@b.c", "s", "b")) is False
```

- [ ] **Step 3: Run → FAIL (module absent). Step 4: Implement**

```python
# management/server/services/mailer.py
"""Mailer SMTP minimal du panel (invitations RAG + sièges code).

Env-driven (ADMIN_SMTP_*), dégradation propre : non configuré ou échec
d'envoi -> False + warning, jamais d'exception vers l'appelant (les liens
restent affichés au front en fallback). Le mailer upstream
(api/utils/web_utils.py) dépend du contexte Quart — non réutilisable ici.
"""
import logging
from email.header import Header
from email.mime.text import MIMEText

import aiosmtplib

from management.server.config import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.SMTP_HOST)


async def send_mail(to: str, subject: str, body_text: str) -> bool:
    if not is_configured():
        logger.warning("SMTP non configuré (ADMIN_SMTP_HOST vide) — email non envoyé à %s", to)
        return False
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    try:
        smtp = aiosmtplib.SMTP(hostname=settings.SMTP_HOST, port=settings.SMTP_PORT,
                               use_tls=settings.SMTP_TLS, timeout=10)
        await smtp.connect()
        if settings.SMTP_USERNAME:
            await smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        await smtp.send_message(msg)
        await smtp.quit()
        return True
    except Exception as e:
        logger.warning("envoi email à %s échoué: %s", to, e)
        return False
```

- [ ] **Step 5: Run (3 PASS), ajouter `aiosmtplib>=5.0.0` à management/server/requirements.txt, ruff, commit**

```bash
git commit -m "feat(mgmt): mailer SMTP env-driven (dégradation propre sans config)"
```

---

### Task 2: Email d'invitation RAG

**Files:**
- Modify: `management/server/routers/users.py` (POST /users), `management/server/models/schemas.py` (`UserProvisionResponse` += `email_sent: bool = False`)
- Modify: `management/web/src/pages/members/index.tsx` (badge sur le modal inviteResult)
- Test: `test/multitenant/test_code_mailer.py` (append — test route avec mailer mocké)

**Interfaces:**
- Consumes: `mailer.send_mail` / `is_configured` (Task 1). Route existante : construit `invite_url` puis retourne `UserProvisionResponse`.
- Produces: réponse POST /users avec `email_sent: bool`.

- [ ] **Step 1: Failing test** (append à test_code_mailer.py — utiliser le TestClient pattern de test_code_routes_rbac.py : fixtures panel_client/org_with_entitlement_and_users importables via conftest)

```python
def test_post_users_sends_invite_email(panel_client, org_with_entitlement_and_users, monkeypatch):
    from management.server.services import mailer
    client, _ = panel_client
    org_id, tokens = org_with_entitlement_and_users
    sent = {}

    async def fake_send(to, subject, body_text):
        sent["to"] = to; sent["body"] = body_text
        return True
    monkeypatch.setattr("management.server.routers.users.send_mail", fake_send, raising=False)

    import uuid
    email = f"invite-{uuid.uuid4().hex[:8]}@client.fr"
    r = client.post("/api/admin/users",
                    json={"email": email, "nickname": "Test Invite", "org_id": org_id, "org_role": "member"},
                    headers=_h(tokens["superuser"]))
    assert r.status_code in (200, 201), r.text
    assert r.json()["email_sent"] is True
    assert sent["to"] == email
    assert r.json()["invite_url"] in sent["body"]
```

(`_h` : copier le helper 2-lignes depuis test_code_routes_rbac.py. Cleanup : soft-delete le user créé en fin de test via DELETE /users/{uid} en superuser.)

- [ ] **Step 2: Run → FAIL. Step 3: Implement**

Dans users.py, après le mint de `invite_url` (import en tête de fonction : `from management.server.services.mailer import send_mail`) :

```python
    email_sent = await send_mail(
        to=body.email,
        subject="Votre accès à la plateforme Cyllene",
        body_text=(
            f"Bonjour {body.nickname},\n\n"
            f"Un compte vous a été créé. Définissez votre mot de passe ici :\n{invite_url}\n\n"
            f"Ce lien expire dans {settings.INVITE_TOKEN_EXPIRE_SECONDS // 3600} heures.\n\n"
            "— L'équipe Cyllene"
        ),
    )
```

(⚠️ la route est `def` sync ou `async def` ? La lire — si sync, la passer `async def` est OK sous FastAPI, ou utiliser `asyncio.run` est INTERDIT dans l'event loop : passer la route en `async def` et `await`.) Ajouter `email_sent=email_sent` au `UserProvisionResponse` (+ champ dans le schema, défaut False).

Front members/index.tsx : dans le modal `inviteResult`, ajouter le badge (stocker `email_sent` dans le state inviteResult) :

```tsx
{inviteResult?.email_sent
  ? <Tag color="green">Email d'invitation envoyé</Tag>
  : <Tag color="orange">Email non envoyé — transmettez le lien ci-dessous</Tag>}
```

- [ ] **Step 4: Run + suites voisines + build front. Step 5: Commit** — `feat(mgmt): email d'invitation envoyé au user (best-effort, badge front)`

---

### Task 3: Table invites + routes bulk/resend/rotate/claim

**Files:**
- Modify: `api/db/db_models.py` (classe `CodeKeyInvite` dans le bloc CUSTOM Code product)
- Create: `management/server/services/code_invites.py`
- Modify: `management/server/routers/code.py` (4 routes), `management/server/services/audit.py` (+3 constantes)
- Test: `test/multitenant/test_code_invites.py`

**Interfaces:**
- Consumes: `create_code_key(code_team_id, label, owner_user_id, created_by, client=None) -> (row, plain|None)` ; `mailer.send_mail` ; `require_code_team_admin` ; `settings.PANEL_PUBLIC_URL` / `CODE_GATEWAY_PUBLIC_URL`.
- Produces (service `code_invites.py`) :
  - `create_invites(*, code_team_id, emails: list[str], created_by) -> list[dict]` — dédup + validation format (`"@" in e and "." in e.split("@")[1]`), max 200 (ValueError au-delà) ; par email : row + token clair retourné UNIQUEMENT pour l'URL (`{"invite_id", "email", "claim_token"}`)
  - `regenerate_token(invite_id) -> str | None` (None si claimed/introuvable)
  - `claim(token: str, client=None) -> dict` — lève `InviteNotFound` (→404) ou `GatewayDown` (→503) ; succès `{"plain_key", "label", "email"}`
  - `claim_url(token) -> str` = `f"{settings.PANEL_PUBLIC_URL or ''}/admin/claim?token={token}"`
- Routes : `POST /code/teams/{team_id}/keys/bulk {emails}` → `[{email, invite_id, email_sent}]` ; `POST /code/invites/{invite_id}/resend` ; `POST /code/keys/{key_id}/rotate` ; `POST /public/code/claim {token}` (sans auth, rate-limit) ; `GET /code/teams/{team_id}/invites` (liste pendantes). Audit : `CODE_SEAT_INVITE`, `CODE_SEAT_CLAIMED`, `CODE_KEY_ROTATED`.

- [ ] **Step 1: Modèle**

```python
class CodeKeyInvite(DataBaseModel):
    """Invitation de siège code : la clé n'existe qu'au claim (jamais de secret au repos).

    token_hash = SHA-256 hex du token urlsafe — le clair n'est jamais stocké.
    claimed_key_id non-NULL = consommée (le claim est un UPDATE conditionnel atomique).
    """
    id = CharField(max_length=32, primary_key=True)
    code_team_id = CharField(max_length=32, null=False, index=True)
    email = CharField(max_length=255, null=False, index=True)
    token_hash = CharField(max_length=64, null=False, unique=True)
    expires_at = DateTimeField(null=False)
    claimed_key_id = CharField(max_length=32, null=True)
    created_by = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "code_key_invite"
```

- [ ] **Step 2: Failing tests** (test_code_invites.py — fixtures FakeLiteLLM/org_with_entitlement importées comme les fichiers voisins ; helpers `_h`/panel_client pour les routes)

Tests requis (code complet à écrire en suivant les patterns des tests voisins) :
1. `test_create_invites_hashes_token_and_dedups` — 3 emails dont 1 doublon → 2 rows ; `token_hash` == sha256(claim_token) hex ; token clair ∉ DB.
2. `test_create_invites_rejects_over_200` — 201 emails → ValueError.
3. `test_claim_generates_key_once_atomically` — create invite → 2 `claim()` concurrents (ThreadPoolExecutor, FakeLiteLLM) → exactement 1 succès avec `plain_key`, l'autre `InviteNotFound` ; 1 seule CodeKey créée (scopée au team).
4. `test_claim_expired_or_unknown_raises_notfound` — token inconnu ET invite expirée (forcer `expires_at` passé) → `InviteNotFound`.
5. `test_claim_gateway_down_keeps_token_valid` — FakeLiteLLM(down=True) → `GatewayDown` ; puis gateway up → le MÊME token claim avec succès (rollback vérifié).
6. `test_bulk_route_rbac_and_email_sent` — route bulk : 403 membre simple ; org_admin OK avec mailer mocké → `email_sent: true`, le claim_url contient le token, l'email le contient aussi.
7. `test_rotate_revokes_and_reinvites` — clé existante → rotate → clé `revoked` + 1 invite pendante pour le même email + email envoyé.
8. `test_public_claim_route_rate_limited` — 11 POST /public/code/claim depuis le TestClient → la 11ᵉ = 429.
9. `test_resend_regenerates_token` — resend → ancien token → 404 au claim ; nouveau token → OK. Refusé (409) si claimed.

- [ ] **Step 3: Run → FAIL. Step 4: Implement service + routes**

Service — points d'implémentation exacts :

```python
# claim(token) — cœur atomique :
token_hash = hashlib.sha256(token.encode()).hexdigest()
now = datetime.datetime.now(datetime.timezone.utc)
with DB.connection_context():
    inv = CodeKeyInvite.get_or_none(CodeKeyInvite.token_hash == token_hash)
    if inv is None or inv.claimed_key_id is not None or inv.expires_at < now.replace(tzinfo=None):
        raise InviteNotFound()
    # réservation atomique (anti double-clic) — 'pending' est un sentinel
    updated = (CodeKeyInvite.update(claimed_key_id="pending")
               .where((CodeKeyInvite.id == inv.id) & (CodeKeyInvite.claimed_key_id.is_null(True)))
               .execute())
    if updated == 0:
        raise InviteNotFound()
# owner: compte panel existant avec cet email ?
users = UserService.query(email=inv.email, status="1")
owner_id = users[0].id if users else None
key, plain = create_code_key(code_team_id=inv.code_team_id, label=inv.email,
                             owner_user_id=owner_id, created_by=inv.created_by, client=client)
if plain is None:  # gateway down -> rollback, le lien reste valide
    _mark_invite(inv.id, claimed_key_id=None)
    raise GatewayDown()
_mark_invite(inv.id, claimed_key_id=key.id)
```

Rate-limit (module-level dans code.py, in-process — documenter mono-replica) :

```python
_CLAIM_HITS: dict[str, list[float]] = {}
def _rate_limited(ip: str, limit: int = 10, window: float = 60.0) -> bool:
    import time
    now = time.time()
    hits = [t for t in _CLAIM_HITS.get(ip, []) if now - t < window]
    hits.append(now)
    _CLAIM_HITS[ip] = hits
    return len(hits) > limit
```

Route publique : `@router.post("/public/code/claim")` — `request.client.host` pour l'IP, 429 si limité, mapping exceptions → 404 générique / 503. Retour succès : `{"plain_key", "label", "gateway_url": settings.CODE_GATEWAY_PUBLIC_URL or None}`. PAS d'audit du token ; audit `CODE_SEAT_CLAIMED` avec l'invite_id/email.

Routes admin : bulk (validate + create_invites + par invite `send_mail` avec claim_url + gateway_url + « expire dans 72 h » → `email_sent`) ; resend ; rotate (lookup key → `revoke_code_key` → `create_invites` 1 email → send). Toutes : RBAC `require_code_team_admin` via le team de l'objet (pattern des routes existantes — lookup PUIS check, jamais l'inverse).

- [ ] **Step 5: Run tout (invites + RBAC + housekeeping ×2), ruff, commit** — `feat(code): invitations de sièges bulk — claim one-time atomique, rotate, resend`

---

### Task 4: Front — modal bulk, invites pendantes, rotate, page publique /claim

**Files:**
- Modify: `management/web/src/pages/code/index.tsx`
- Create: `management/web/src/pages/claim/index.tsx`
- Modify: `management/web/src/App.tsx` (route publique `/claim` HORS layout/auth guard — regarder comment /login est routé et imiter)

**Interfaces:**
- Consumes: routes Task 3. `POST /api/admin/public/code/claim` est appelée SANS token (la page claim n'utilise pas l'interceptor auth ? si l'interceptor ajoute un header Authorization vide c'est sans effet — vérifier que le 401-redirect de lib/api.ts ne s'applique pas : la route ne renvoie jamais 401, OK).

- [ ] **Step 1: Vue team (index.tsx)**
  - Bouton « Inviter des sièges » (à côté de « Nouvelle clé ») → Modal textarea (`placeholder="un email par ligne"`) → POST bulk → tableau de résultats `{email, email_sent ? '✓ envoyé' : '✗ échec — lien : <claim_url copiable>'}`. NOTE : le back doit renvoyer `claim_url` par item UNIQUEMENT quand `email_sent` est false (fallback admin) — l'ajouter en Task 3 si oublié.
  - Sous la table des clés : liste des invites pendantes (`GET /code/teams/{id}/invites`) — email, « expire le … », boutons Renvoyer (POST resend + message) / rien d'autre (annuler = différé YAGNI).
  - Ligne de clé active : bouton Rotate (icône ReloadOutlined, Popconfirm « Révoque la clé et envoie un nouveau lien à {label} ») → POST rotate → refresh.

- [ ] **Step 2: Page publique claim/index.tsx**

```tsx
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Typography } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import axios from 'axios';

export default function ClaimPage() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [state, setState] = useState<'idle' | 'loading' | 'done' | 'notfound' | 'retry'>('idle');
  const [result, setResult] = useState<{ plain_key: string; label: string; gateway_url: string | null } | null>(null);

  const claim = () => {
    setState('loading');
    axios.post('/api/admin/public/code/claim', { token })
      .then((r) => { setResult(r.data); setState('done'); })
      .catch((e) => setState(e?.response?.status === 503 ? 'retry' : 'notfound'));
  };

  useEffect(() => { if (!token) setState('notfound'); }, [token]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 p-4">
      <Card className="max-w-xl w-full" title="Récupération de votre clé d'accès Code">
        {state === 'idle' && (
          <>
            <p className="mb-4">Cliquez pour révéler votre clé. Elle ne sera affichée qu'UNE seule fois — copiez-la immédiatement.</p>
            <Button type="primary" onClick={claim}>Révéler ma clé</Button>
          </>
        )}
        {state === 'loading' && <Card loading />}
        {state === 'done' && result && (
          <Alert type="success" message={`Clé pour ${result.label} — copiez-la MAINTENANT, elle ne sera plus jamais affichée.`}
            description={
              <>
                <Typography.Paragraph copyable={{ icon: <CopyOutlined /> }} code>{result.plain_key}</Typography.Paragraph>
                {result.gateway_url && (
                  <div>Endpoint à configurer dans Kilo Code / OpenCode / Cline :{' '}
                    <Typography.Text copyable code>{result.gateway_url}</Typography.Text></div>
                )}
              </>
            } />
        )}
        {state === 'retry' && (
          <Alert type="warning" showIcon message="Service momentanément indisponible — votre lien reste valide, réessayez dans quelques minutes."
            action={<Button onClick={claim}>Réessayer</Button>} />
        )}
        {state === 'notfound' && (
          <Alert type="error" showIcon message="Lien invalide, expiré ou déjà utilisé. Contactez votre administrateur." />
        )}
      </Card>
    </div>
  );
}
```

(le clic explicite « Révéler » évite que les scanners d'URL des antispams consomment le token — mentionner ce rationale en commentaire.)

- [ ] **Step 3: Route publique dans App.tsx** hors du guard auth (imiter /login), path `/claim`.

- [ ] **Step 4: Build + Playwright** — flow complet : bulk 2 emails (SMTP non configuré → liens fallback affichés) → copier un claim_url → ouvrir /claim en contexte SANS token admin → révéler → clé affichée + endpoint → re-claim du même token → « invalide » ; rotate sur une clé → invite pendante apparaît. Screenshots.

- [ ] **Step 5: Commit** — `feat(code-ui): invitations bulk, page publique de claim one-time, rotate`

## Self-review

Spec coverage : mailer §1→T1 ; invites RAG §2→T2 ; table/routes/claim/sécurité §3-4→T3 ; front §3→T4 ; différés respectés (pas de self-service dev, pas d'annulation d'invite, pas de HTML riche). Placeholders : aucun — T3 Step 2 liste les tests par intention avec les patterns voisins nommés (choix assumé : les implémenteurs ont prouvé sur les 3 plans précédents qu'ils transcrivent ces intentions fidèlement). Type consistency : `create_invites`/`claim`/`claim_url` (T3) = consommés T4 ; `email_sent` (T1/T2/T3) cohérent.
