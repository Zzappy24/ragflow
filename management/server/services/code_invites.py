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
import secrets
from datetime import datetime, timedelta, timezone

from common.misc_utils import get_uuid
from management.server.config import settings
from management.server.services.code_provisioning import create_code_key

logger = logging.getLogger(__name__)

INVITE_TTL_HOURS = 72
MAX_BULK_EMAILS = 200


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


def create_invites(*, code_team_id: str, emails: list[str], created_by: str) -> list[dict]:
    """Dedup + validate emails, cap at MAX_BULK_EMAILS, one CodeKeyInvite row
    per email. Returns [{"invite_id", "email", "claim_token"}] — claim_token
    is the ONLY place the plaintext token is ever surfaced."""
    from api.db.db_models import DB, CodeKeyInvite

    seen: set[str] = set()
    deduped: list[str] = []
    for raw in emails:
        e = (raw or "").strip()
        if not e or e in seen:
            continue
        if "@" not in e or "." not in e.split("@", 1)[1]:
            raise ValueError(f"invalid email address: {e}")
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
        CodeKeyInvite.update(token_hash=_hash_token(token), expires_at=expires).where(
            CodeKeyInvite.id == invite_id).execute()
    return token


def claim(token: str, client=None) -> dict:
    """One-time atomic claim: reserve the invite (conditional UPDATE), then
    provision the CodeKey outside the reservation lock. On gateway failure,
    the reservation is rolled back so the SAME link stays valid — the invite
    is never burned by a transient LiteLLM outage."""
    from api.db.db_models import DB, CodeKeyInvite

    token_hash = _hash_token(token)
    now = _now()
    with DB.connection_context():
        inv = CodeKeyInvite.get_or_none(CodeKeyInvite.token_hash == token_hash)
        if inv is None or inv.claimed_key_id is not None or inv.expires_at < now:
            raise InviteNotFound()
        # Atomic reservation (anti double-click): only one concurrent UPDATE
        # can flip claimed_key_id away from NULL — the other affects 0 rows.
        updated = (CodeKeyInvite.update(claimed_key_id="pending")
                   .where((CodeKeyInvite.id == inv.id) & (CodeKeyInvite.claimed_key_id.is_null(True)))
                   .execute())
        if updated == 0:
            raise InviteNotFound()

    from api.db.services.user_service import UserService
    users = UserService.query(email=inv.email, status="1")
    owner_id = users[0].id if users else None

    key, plain = create_code_key(code_team_id=inv.code_team_id, label=inv.email,
                                 owner_user_id=owner_id, created_by=inv.created_by, client=client)
    if plain is None:
        logger.warning("invite %s claim: LiteLLM down, rolling back reservation", inv.id)
        _mark_invite(inv.id, claimed_key_id=None)
        raise GatewayDown()

    _mark_invite(inv.id, claimed_key_id=key.id)
    return {"plain_key": plain, "label": inv.email, "email": inv.email}
