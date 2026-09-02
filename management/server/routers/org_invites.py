"""
Invitations d'organisation unifiées — « tout est invitation », zéro-leak.

Remplace le couple « Ajouter un membre existant » / « Inviter (nouveau
compte) » qui exigeait de l'admin qu'il sache si l'email avait déjà un
compte sur la plateforme (information qu'il ne peut pas connaître, et
qu'on ne veut pas divulguer : énumération de comptes cross-org).

Principe : l'admin soumet des emails ; chaque email reçoit une row
``OrgInvite`` et un lien d'acceptation — SANS AUCUNE vérification
d'existence de compte à l'invitation (réponse et effets rigoureusement
identiques dans les deux cas). La distinction ne se joue qu'à
l'ACCEPTATION, côté invité :

  - compte existant  → il accepte, l'``OrgMember`` est créé ;
  - compte inconnu   → il choisit nom + mot de passe, ``provision_user``
                       déroule le pipeline complet (user + shell tenant +
                       org member + workspace par défaut), puis le mot de
                       passe réel est posé et le compte activé.

Bonus gouvernance : plus personne n'est rattaché à une organisation sans
avoir cliqué « accepter » (consentement explicite, RGPD-friendly).

Le membership n'existe qu'après acceptation : la liste des membres ne
montre que des membres réels ; les invitations vivent dans leur propre
liste, uniforme.
"""
import base64
import logging
from datetime import datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from management.server.auth.dependencies import get_current_user_id, require_org_admin
from management.server.config import settings
from management.server.services import audit as audit_svc
from management.server.services.mailer import send_mail

logger = logging.getLogger(__name__)
router = APIRouter()

_JWT_ALGO = "HS256"


class OrgInviteBatch(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=200)
    role: str = Field(default="member", pattern=r"^(org_admin|member)$")
    # Pré-affectation optionnelle, matérialisée à l'acceptation.
    ws_id: str | None = None
    ws_role: str | None = Field(default=None, pattern=r"^(ws_admin|editor|viewer)$")


class OrgInviteUpdate(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(org_admin|member)$")
    # ws_id="" (chaîne vide) = retirer la pré-affectation ; None = ne pas toucher.
    ws_id: str | None = None
    ws_role: str | None = Field(default=None, pattern=r"^(ws_admin|editor|viewer)$")


class OrgInviteAccept(BaseModel):
    token: str
    # Requis uniquement quand l'email n'a pas encore de compte :
    nickname: str | None = None
    password: str | None = None


def _sign(invite_id: str, expires_at: datetime) -> str:
    return jwt.encode(
        {"invite_id": invite_id, "exp": int(expires_at.timestamp()), "type": "org_invite"},
        settings.JWT_SECRET, algorithm=_JWT_ALGO,
    )


def _accept_url(token: str) -> str:
    base = (settings.PANEL_PUBLIC_URL or "").rstrip("/")
    return f"{base}/org-invite?token={token}"


def _load_valid_invite(token: str):
    """JWT valide + row encore présente + non expirée, sinon 404/410."""
    from api.db.db_models import OrgInvite
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[_JWT_ALGO])
        if payload.get("type") != "org_invite":
            raise ValueError("wrong type")
    except Exception:
        raise HTTPException(status_code=404, detail="Invitation invalide ou expirée")
    inv = OrgInvite.get_or_none(OrgInvite.id == payload["invite_id"])
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation invalide ou expirée")
    if inv.expires_at and inv.expires_at < datetime.now():
        raise HTTPException(status_code=410, detail="Invitation expirée — demandez un renvoi")
    return inv


def _org_name(org_id: str) -> str:
    from api.db.db_models import Organisation
    org = Organisation.get_or_none(Organisation.id == org_id)
    return getattr(org, "name", None) or "votre organisation"


def _invite_email_text(org_name: str, url: str) -> str:
    return (
        "Bonjour,\n\n"
        f"« {org_name} » vous invite à la rejoindre sur la plateforme Cyllene.\n"
        f"Pour accepter l'invitation :\n{url}\n\n"
        f"Ce lien expire dans {settings.INVITE_TOKEN_EXPIRE_SECONDS // 3600} heures.\n\n"
        "— L'équipe Cyllene"
    )


def _existing_active_user(email: str):
    from api.db.services.user_service import UserService
    users = UserService.query(email=email, status="1")
    return users[0] if users else None


def _is_org_member(org_id: str, user) -> bool:
    if not user:
        return False
    from api.db.services.org_service import OrgMemberService
    return OrgMemberService.get_membership(org_id, user.id) is not None


async def _create_and_send(org_id: str, email: str, role: str, invited_by: str,
                           ws_id: str | None = None, ws_role: str | None = None) -> dict:
    """Row + lien + email — identique quel que soit l'état du compte."""
    from common.misc_utils import get_uuid
    from common.time_utils import current_timestamp
    from api.db.db_models import DB, OrgInvite

    expires_at = datetime.now() + timedelta(seconds=settings.INVITE_TOKEN_EXPIRE_SECONDS)
    invite_id = get_uuid()
    with DB.connection_context():
        # create_time/update_time : remplis par les services d'habitude —
        # Model.create() les laisserait NULL (le listing ordonne dessus).
        OrgInvite.create(id=invite_id, org_id=org_id, email=email, role=role,
                         user_id=None, ws_id=ws_id, ws_role=ws_role,
                         expires_at=expires_at, invited_by=invited_by,
                         create_time=current_timestamp(), update_time=current_timestamp())
    url = _accept_url(_sign(invite_id, expires_at))
    email_sent = await send_mail(
        to=email, subject="Invitation à rejoindre une organisation Cyllene",
        body_text=_invite_email_text(_org_name(org_id), url),
    )
    return {"email": email, "status": "invited", "email_sent": email_sent,
            "invite_url": None if email_sent else url}


@router.post("/orgs/{org_id}/members/invitations")
async def invite_members(request: Request, org_id: str, body: OrgInviteBatch,
                         user_id: str = Depends(get_current_user_id)):
    require_org_admin(org_id, user_id)
    from api.db.db_models import OrgInvite
    from api.db.services.quota_service import check_quota

    if body.ws_id:
        if not body.ws_role:
            raise HTTPException(status_code=400, detail="ws_role est requis avec ws_id")
        from api.db.services.workspace_service import WorkspaceService
        ok, ws = WorkspaceService.get_by_id(body.ws_id)
        if not ok or not ws or ws.org_id != org_id or ws.status != "1":
            raise HTTPException(status_code=400, detail="Workspace introuvable dans cette organisation")

    results = []
    seen: set[str] = set()
    for raw in body.emails:
        email = (raw or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        if "@" not in email or len(email) > 255:
            results.append({"email": email, "status": "invalid"})
            continue
        # Déjà membre : visible dans la liste des membres de toute façon —
        # ce n'est pas un leak cross-org.
        if _is_org_member(org_id, _existing_active_user(email)):
            results.append({"email": email, "status": "already_member"})
            continue
        # Invitation pendante : on re-signe et renvoie (idempotent).
        pending = OrgInvite.get_or_none((OrgInvite.org_id == org_id) & (OrgInvite.email == email))
        if pending:
            pending.delete_instance()
        allowed, msg = check_quota(org_id, "user")
        if not allowed:
            results.append({"email": email, "status": "quota_exceeded", "detail": msg})
            continue
        results.append(await _create_and_send(org_id, email, body.role, user_id,
                                              ws_id=body.ws_id, ws_role=body.ws_role))

    audit_svc.record(
        request=request, actor_user_id=user_id, action=audit_svc.USER_INVITE,
        org_id=org_id, resource_type="org_invite", resource_id=org_id,
        details={"emails": sorted(seen), "role": body.role,
                 "target_display_name": f"{len(seen)} invitation(s)"},
    )
    return results


@router.get("/orgs/{org_id}/members/invitations")
def list_invitations(org_id: str, user_id: str = Depends(get_current_user_id)):
    require_org_admin(org_id, user_id)
    from api.db.db_models import OrgInvite
    rows = (OrgInvite.select().where(OrgInvite.org_id == org_id)
            .order_by(OrgInvite.create_time.desc()))
    return [{"id": r.id, "email": r.email, "role": r.role,
             "ws_id": r.ws_id, "ws_role": r.ws_role,
             "expires_at": r.expires_at.isoformat() if r.expires_at else None,
             "expired": bool(r.expires_at and r.expires_at < datetime.now())}
            for r in rows]


@router.post("/org-invites/{invite_id}/resend")
async def resend_invitation(invite_id: str, user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import OrgInvite
    inv = OrgInvite.get_or_none(OrgInvite.id == invite_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation introuvable")
    require_org_admin(inv.org_id, user_id)
    inv.expires_at = datetime.now() + timedelta(seconds=settings.INVITE_TOKEN_EXPIRE_SECONDS)
    inv.save()
    url = _accept_url(_sign(inv.id, inv.expires_at))
    email_sent = await send_mail(
        to=inv.email, subject="Invitation à rejoindre une organisation Cyllene",
        body_text=_invite_email_text(_org_name(inv.org_id), url),
    )
    return {"email": inv.email, "email_sent": email_sent,
            "invite_url": None if email_sent else url}


@router.patch("/org-invites/{invite_id}")
def update_invitation(invite_id: str, body: OrgInviteUpdate,
                      user_id: str = Depends(get_current_user_id)):
    """Modifie une invitation EN ATTENTE sans renvoyer d'email : le lien
    déjà envoyé ne porte que l'id — la destination (rôle org, workspace,
    rôle ws) est lue dans la row à l'acceptation, donc l'éditer suffit."""
    from api.db.db_models import OrgInvite
    inv = OrgInvite.get_or_none(OrgInvite.id == invite_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation introuvable")
    require_org_admin(inv.org_id, user_id)

    if body.role is not None:
        inv.role = body.role
    if body.ws_id is not None:
        if body.ws_id == "":
            inv.ws_id, inv.ws_role = None, None
        else:
            if not (body.ws_role or inv.ws_role):
                raise HTTPException(status_code=400, detail="ws_role est requis avec ws_id")
            from api.db.services.workspace_service import WorkspaceService
            ok, ws = WorkspaceService.get_by_id(body.ws_id)
            if not ok or not ws or ws.org_id != inv.org_id or ws.status != "1":
                raise HTTPException(status_code=400, detail="Workspace introuvable dans cette organisation")
            inv.ws_id = body.ws_id
    if body.ws_role is not None and inv.ws_id:
        inv.ws_role = body.ws_role
    inv.save()
    return {"id": inv.id, "email": inv.email, "role": inv.role,
            "ws_id": inv.ws_id, "ws_role": inv.ws_role}


@router.delete("/org-invites/{invite_id}", status_code=204)
def cancel_invitation(invite_id: str, user_id: str = Depends(get_current_user_id)):
    from api.db.db_models import OrgInvite
    inv = OrgInvite.get_or_none(OrgInvite.id == invite_id)
    if not inv:
        return
    require_org_admin(inv.org_id, user_id)
    # La row est la source de vérité : sans elle, le JWT encore valide est mort.
    inv.delete_instance()


# ---------------------------------------------------------------------------
# Endpoints PUBLICS (pas de Depends auth) — l'accès est prouvé par le token
# signé reçu par email, comme le flux set-password et le claim Code.
# ---------------------------------------------------------------------------

@router.get("/org-invites/introspect")
def introspect_invitation(token: str):
    inv = _load_valid_invite(token)
    ws_name = None
    if inv.ws_id:
        from api.db.services.workspace_service import WorkspaceService
        ok, ws = WorkspaceService.get_by_id(inv.ws_id)
        ws_name = getattr(ws, "name", None) if ok else None
    return {
        "org_name": _org_name(inv.org_id),
        "email": inv.email,
        "role": inv.role,
        "workspace_name": ws_name,
        "workspace_role": inv.ws_role,
        # Côté INVITÉ uniquement — il sait déjà s'il a un compte.
        "needs_password": _existing_active_user(inv.email) is None,
    }


@router.post("/org-invites/accept")
async def accept_invitation(request: Request, body: OrgInviteAccept):
    from common.misc_utils import get_uuid
    from api.db.db_models import DB
    from werkzeug.security import generate_password_hash

    inv = _load_valid_invite(body.token)
    user = _existing_active_user(inv.email)

    if user is None:
        # Nouveau compte : nom + mot de passe requis, pipeline complet réutilisé.
        if not body.password or len(body.password) < 8:
            raise HTTPException(status_code=400, detail="Mot de passe requis (8 caractères minimum)")
        nickname = (body.nickname or inv.email.split("@")[0]).strip()[:100]
        from management.server.services.provisioning import provision_user
        try:
            new_user_id = provision_user(
                email=inv.email, nickname=nickname, org_id=inv.org_id,
                org_role=inv.role, invited_by=inv.invited_by or "org_invite",
                ws_id=inv.ws_id, ws_role=inv.ws_role,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Le login RAGFlow vérifie le hash de Base64(mot de passe) — cf.
        # CLAUDE.md « Password Encryption Pipeline ». On pose le vrai mot de
        # passe et on active le compte : l'email vient d'être prouvé.
        from api.db.services.user_service import UserService
        b64 = base64.b64encode(body.password.encode()).decode()
        with DB.connection_context():
            UserService.update_by_id(new_user_id, {
                "password": generate_password_hash(b64),
                "is_active": "1",
            })
        acting_user = new_user_id
    else:
        # Compte existant : le membership seulement — c'est l'acceptation.
        from api.db.services.org_service import OrgMemberService
        if not OrgMemberService.get_membership(inv.org_id, user.id):
            with DB.connection_context():
                OrgMemberService.save(**{
                    "id": get_uuid(), "org_id": inv.org_id, "user_id": user.id,
                    "role": inv.role, "status": "1",
                    "invited_by": inv.invited_by,
                })
        # Pré-affectation workspace du compte existant — même geste que
        # « Ajouter depuis l'organisation », déclenché par l'acceptation.
        if inv.ws_id:
            from api.db.services.workspace_service import WorkspaceService, WsMemberService
            ok, ws = WorkspaceService.get_by_id(inv.ws_id)
            if ok and ws and ws.org_id == inv.org_id and ws.status == "1" \
                    and not WsMemberService.get_membership(inv.ws_id, user.id):
                from management.server.services.provisioning import grant_workspace_access
                grant_workspace_access(ws=ws, target_user_id=user.id,
                                       role=inv.ws_role or "viewer", member_id=get_uuid())
        acting_user = user.id

    inv.delete_instance()
    audit_svc.record(
        request=request, actor_user_id=acting_user, action=audit_svc.ORG_MEMBER_ADD,
        org_id=inv.org_id, resource_type="user", resource_id=acting_user,
        details={"target_display_name": inv.email, "email": inv.email,
                 "role": inv.role, "via": "org_invite_accept"},
    )
    return {"ok": True, "org_name": _org_name(inv.org_id),
            "ragflow_url": settings.RAGFLOW_BASE_URL}
