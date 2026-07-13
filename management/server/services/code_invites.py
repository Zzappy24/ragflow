"""Code product seat invites — bulk email invitations, one-time atomic claim.

The seat's CodeKey row is created ONLY at claim time (spec Task 3): a
CodeKeyInvite carries no secret at rest (token_hash = sha256(token) hex, the
plaintext token is returned exactly once, at creation/resend, for the email
link). claimed_key_id non-NULL marks the invite consumed; the reservation is
a conditional UPDATE (compare-and-swap on claimed_key_id IS NULL) so two
concurrent claims of the same link can never both succeed.
"""
import hashlib
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from common.misc_utils import get_uuid
from management.server.config import settings
from management.server.services.code_provisioning import create_code_key

logger = logging.getLogger(__name__)

INVITE_TTL_HOURS = 72
MAX_BULK_EMAILS = 200

# Reservation sentinel older than this is considered abandoned (the process
# that reserved it crashed/was killed between the CAS reservation and the
# final claimed_key_id write) and can be reclaimed by a later claim().
STALE_PENDING_SECONDS = 120

# Rejects any whitespace/control char inside the address (header-injection
# guard: "a@x.com\nBcc: evil@x.com" must not pass). fullmatch, so ^/$ are
# redundant but kept for clarity.
_EMAIL_RE = re.compile(r"^\S+@\S+\.\S+$")


class InviteNotFound(Exception):
    """Unknown token, already-claimed invite, or expired invite — all map to a
    generic 404 at the route level (never distinguish, to avoid token enumeration)."""


class GatewayDown(Exception):
    """LiteLLM unreachable at claim time — the invite is rolled back to
    unclaimed so the SAME link stays valid and can be retried."""


def _now() -> datetime:
    """Naive UTC datetime — MySQL DATETIME columns store naive values, so all
    comparisons/writes on expires_at must be naive to be consistent."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _mark_invite(invite_id: str, **fields) -> None:
    from api.db.db_models import DB, CodeKeyInvite
    with DB.connection_context():
        CodeKeyInvite.update(**fields).where(CodeKeyInvite.id == invite_id).execute()


def claim_url(token: str) -> str:
    return f"{settings.PANEL_PUBLIC_URL or ''}/admin/claim?token={token}"


def validate_email(email: str) -> None:
    """Shared email-shape check — used both for bulk invite emails
    (create_invites) and for re-validating an existing CodeKey.label before
    rotate_key destroys it (see routers/code.py:rotate_key)."""
    if not _EMAIL_RE.fullmatch(email or ""):
        raise ValueError(f"invalid email address: {email}")


def _pending_sentinel(now_epoch: float) -> str:
    return f"pending:{int(now_epoch)}"


def _is_reclaimable_pending(claimed_key_id: str | None, now_epoch: float) -> bool:
    """True if claimed_key_id is a "pending:<epoch>" reservation sentinel
    older than STALE_PENDING_SECONDS — i.e. a claim() that crashed between
    reserving the invite and completing (or rolling back) it."""
    if not claimed_key_id or not claimed_key_id.startswith("pending:"):
        return False
    try:
        ts = int(claimed_key_id.split(":", 1)[1])
    except ValueError:
        return False
    return (now_epoch - ts) > STALE_PENDING_SECONDS


def create_invites(*, code_team_id: str, emails: list[str], created_by: str,
                   max_budget: float | None = None,
                   rpm_limit: int | None = None) -> list[dict]:
    """Dedup + validate emails, cap at MAX_BULK_EMAILS, one CodeKeyInvite row
    per email. Returns [{"invite_id", "email", "claim_token"}] — claim_token
    is the ONLY place the plaintext token is ever surfaced.

    max_budget/rpm_limit : limites par siège appliquées à CHAQUE invitation,
    reportées sur la CodeKey créée au claim (regenerate_token les préserve,
    la row est conservée)."""
    if max_budget is not None and max_budget <= 0:
        raise ValueError("max_budget must be > 0")
    if rpm_limit is not None and rpm_limit <= 0:
        raise ValueError("rpm_limit must be > 0")
    from api.db.db_models import DB, CodeKeyInvite

    seen: set[str] = set()
    deduped: list[str] = []
    for raw in emails:
        e = (raw or "").strip()
        if not e or e in seen:
            continue
        validate_email(e)
        seen.add(e)
        deduped.append(e)
    if len(deduped) > MAX_BULK_EMAILS:
        raise ValueError(f"too many emails: max {MAX_BULK_EMAILS} per bulk invite")

    expires = _now() + timedelta(hours=INVITE_TTL_HOURS)
    out = []
    with DB.connection_context():
        for email in deduped:
            token = secrets.token_urlsafe(32)
            invite_id = get_uuid()
            CodeKeyInvite.create(id=invite_id, code_team_id=code_team_id, email=email,
                                 token_hash=_hash_token(token), expires_at=expires,
                                 max_budget=max_budget, rpm_limit=rpm_limit,
                                 created_by=created_by)
            out.append({"invite_id": invite_id, "email": email, "claim_token": token})
    return out


def regenerate_token(invite_id: str) -> str | None:
    """New plaintext token + refreshed expiry for a still-pending invite.
    Returns None if the invite doesn't exist or is already claimed."""
    from api.db.db_models import DB, CodeKeyInvite

    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=INVITE_TTL_HOURS)
    with DB.connection_context():
        inv = CodeKeyInvite.get_or_none(CodeKeyInvite.id == invite_id)
        if inv is None or inv.claimed_key_id is not None:
            return None
        # Guard the UPDATE itself against a claim racing in between the
        # lookup above and here (TOCTOU): only flip the token if the invite
        # is STILL unclaimed at UPDATE time, otherwise treat it like the
        # already-claimed path above.
        updated = (CodeKeyInvite.update(token_hash=_hash_token(token), expires_at=expires)
                   .where((CodeKeyInvite.id == invite_id) & (CodeKeyInvite.claimed_key_id.is_null(True)))
                   .execute())
        if updated == 0:
            return None
    return token


def claim(token: str, client=None) -> dict:
    """One-time atomic claim: reserve the invite (conditional UPDATE), then
    provision the CodeKey outside the reservation lock.

    Any exception raised after the reservation (LiteLLM down, team/org gone
    inactive, unexpected error) rolls the reservation back to unclaimed so
    the SAME link stays valid — the invite is never burned by a transient
    failure, and the public route never leaks *why* it failed (always 404,
    except the deliberate 503 for GatewayDown).

    As a second line of defense — e.g. the process is killed between the
    reservation and the rollback — the reservation sentinel itself carries
    an epoch ("pending:<epoch>") and becomes reclaimable by a later claim()
    once it goes stale (see _is_reclaimable_pending)."""
    from api.db.db_models import DB, CodeKeyInvite

    token_hash = _hash_token(token)
    now = _now()
    now_epoch = time.time()
    with DB.connection_context():
        inv = CodeKeyInvite.get_or_none(CodeKeyInvite.token_hash == token_hash)
        if inv is None or inv.expires_at < now:
            raise InviteNotFound()
        prior = inv.claimed_key_id
        if prior is not None and not _is_reclaimable_pending(prior, now_epoch):
            raise InviteNotFound()
        # Atomic reservation (anti double-click / crash recovery): only one
        # concurrent UPDATE can flip claimed_key_id away from its last-read
        # value (NULL, or a stale "pending:<epoch>" sentinel) — any other
        # racing UPDATE affects 0 rows.
        cond = (CodeKeyInvite.claimed_key_id.is_null(True) if prior is None
                else CodeKeyInvite.claimed_key_id == prior)
        updated = (CodeKeyInvite.update(claimed_key_id=_pending_sentinel(now_epoch))
                   .where((CodeKeyInvite.id == inv.id) & cond)
                   .execute())
        if updated == 0:
            raise InviteNotFound()

    try:
        from api.db.services.user_service import UserService
        users = UserService.query(email=inv.email, status="1")
        owner_id = users[0].id if users else None

        key, plain = create_code_key(code_team_id=inv.code_team_id, label=inv.email,
                                     owner_user_id=owner_id, created_by=inv.created_by,
                                     max_budget=inv.max_budget, rpm_limit=inv.rpm_limit,
                                     client=client)
        if plain is None:
            logger.warning("invite %s claim: LiteLLM down, rolling back reservation", inv.id)
            # I3: the CodeKey row was already created (desired-state-first)
            # but never reached LiteLLM — no external object exists, no
            # plaintext was ever issued, so deleting it here is safe and
            # correct. Deliberate exception to the no-delete discipline
            # followed elsewhere in the Code product (rows are otherwise
            # append-only for the audit trail): without this, every
            # gateway-down retry of the same invite orphans one more row.
            from api.db.db_models import CodeKey
            with DB.connection_context():
                CodeKey.delete().where(CodeKey.id == key.id).execute()
            _mark_invite(inv.id, claimed_key_id=None)
            raise GatewayDown()
    except GatewayDown:
        raise
    except Exception:
        logger.exception("invite %s claim failed after reservation, rolling back", inv.id)
        _mark_invite(inv.id, claimed_key_id=None)
        raise InviteNotFound()

    _mark_invite(inv.id, claimed_key_id=key.id)
    return {"invite_id": inv.id, "plain_key": plain, "label": inv.email, "email": inv.email}
