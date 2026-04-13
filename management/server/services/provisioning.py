"""
Provisioning service — handles workspace and member creation.

Creating a workspace in our RBAC system requires creating a "technical" RAGFlow
user and tenant, because RAGFlow internally ties all resources (datasets, documents,
chats, agents) to a tenant_id. Each workspace maps 1:1 to a RAGFlow tenant.

Human users in the B2B model keep their real data inside workspace tenants, but
**every user also gets an empty "personal tenant" shell** (User + Tenant with
``id = user_id`` + UserTenant as OWNER + root File folder). This mirrors what
legacy ``user_register`` does and is required because large parts of RAGFlow's
frontend and middleware still call ``TenantService.get_info_by(user_id)`` which
JOINs ``user_tenant`` with the strict constraint ``role = OWNER``. Without the
shell, the bridge lands on "Tenant not found!" and every call to ``/v1/user/tenant_info``
crashes. The shell is intentionally empty — no TenantLLM rows, no datasets,
no documents ever live there.
"""
import secrets

from common.misc_utils import get_uuid
from werkzeug.security import generate_password_hash

# Hardcoded default parser IDs — mirrors ``common/settings.py::init_settings``.
# The admin-server process does not call ``init_settings()`` (it would attempt
# to connect to ES/Infinity on import-time side effects), so ``settings.PARSERS``
# is ``None`` in that context. We store the same default string RAGFlow uses for
# every freshly-registered user so the personal tenant shell looks identical to
# one created via the legacy ``/v1/user/register`` path.
_DEFAULT_PARSER_IDS = (
    "naive:General,qa:Q&A,resume:Resume,manual:Manual,table:Table,paper:Paper,"
    "book:Book,laws:Laws,presentation:Presentation,picture:Picture,one:One,"
    "audio:Audio,email:Email,tag:Tag"
)


def provision_workspace(org_id: str, name: str, description: str, created_by: str):
    """
    Create a workspace with its associated RAGFlow tenant.

    Steps:
    1. Create a "technical" RAGFlow User (ws-{slug}@internal)
    2. Create a RAGFlow Tenant (tenant_id = technical_user.id)
    3. Create UserTenant linking the technical user to the tenant
    4. Create our Workspace record (org_id, tenant_id)
    5. Create WsMember for the creator (ws_admin)
    6. Create UserTenant for the creator → new tenant (role=NORMAL)
    7. Copy the creator's TenantLLM rows to the new tenant so the workspace
       inherits the creator's configured models (chat, embedding, rerank, ...)
       and their API keys. This is the pragmatic B2B approach: each workspace
       tenant gets an independent copy at creation time. If a global provider
       key needs to rotate, a single SQL UPDATE on ``tenant_llm`` covers all
       copies at once. Attempting to centralise the config at org-level would
       require rewriting ``_validate_llm_id`` and every other call site that
       assumes models live on the tenant.

    Returns the Workspace object.
    """
    import re
    from api.db.db_models import DB
    from api.db.services.user_service import UserService, TenantService, UserTenantService
    from api.db.services.workspace_service import WorkspaceService, WsMemberService
    from api.db.services.tenant_llm_service import TenantLLMService

    # Generate a slug-safe identifier
    slug = re.sub(r"[^a-z0-9]", "-", name.lower()).strip("-")[:50]
    technical_email = f"ws-{slug}-{get_uuid()[:8]}@internal"
    technical_user_id = get_uuid()

    with DB.connection_context():
        # 1. Create technical user (not meant for login)
        UserService.save(**{
            "id": technical_user_id,
            "nickname": f"[WS] {name}",
            "email": technical_email,
            "password": get_uuid(),  # random, never used for login
            "status": "1",
        })

        # 2. Create RAGFlow tenant — inherit the creator's default model IDs
        #    so chat/dataset creation finds the same models by default. The
        #    creator's personal Tenant row is the source of truth because
        #    ``user_register`` seeds it from ``settings.CHAT_MDL`` etc. and
        #    the creator may have since customised their picks.
        creator_tenant = None
        try:
            ok_ct, creator_tenant = TenantService.get_by_id(created_by)
            if not ok_ct:
                creator_tenant = None
        except Exception:
            creator_tenant = None
        TenantService.save(**{
            "id": technical_user_id,  # tenant_id = user_id (RAGFlow convention)
            "name": name,
            "llm_id": getattr(creator_tenant, "llm_id", "") or "",
            "embd_id": getattr(creator_tenant, "embd_id", "") or "",
            "asr_id": getattr(creator_tenant, "asr_id", "") or "",
            "img2txt_id": getattr(creator_tenant, "img2txt_id", "") or "",
            "rerank_id": getattr(creator_tenant, "rerank_id", "") or "",
            "parser_ids": getattr(creator_tenant, "parser_ids", "") or _DEFAULT_PARSER_IDS,
        })

        # 3. Technical user → tenant (owner)
        UserTenantService.save(**{
            "id": get_uuid(),
            "user_id": technical_user_id,
            "tenant_id": technical_user_id,
            "role": "owner",
            "invited_by": created_by,
        })

        # 4. Create our Workspace
        ws_id = get_uuid()
        WorkspaceService.save(**{
            "id": ws_id,
            "org_id": org_id,
            "tenant_id": technical_user_id,
            "name": name,
            "description": description,
            "created_by": created_by,
        })

        # 5. Creator becomes ws_admin
        WsMemberService.save(**{
            "id": get_uuid(),
            "workspace_id": ws_id,
            "user_id": created_by,
            "role": "ws_admin",
        })

        # 6. Creator gets RAGFlow access to the new tenant
        UserTenantService.save(**{
            "id": get_uuid(),
            "user_id": created_by,
            "tenant_id": technical_user_id,
            "role": "normal",
            "invited_by": created_by,
        })

        # 7. Copy creator's TenantLLM rows → new tenant (see docstring).
        creator_llms = TenantLLMService.query(tenant_id=created_by)
        llm_copies = []
        for row in creator_llms:
            llm_copies.append({
                "tenant_id": technical_user_id,
                "llm_factory": row.llm_factory,
                "model_type": row.model_type,
                "llm_name": row.llm_name,
                "api_key": row.api_key,
                "api_base": row.api_base,
                "max_tokens": row.max_tokens,
            })
        if llm_copies:
            TenantLLMService.insert_many(llm_copies)

    ok, ws = WorkspaceService.get_by_id(ws_id)
    return ws


def deprovision_workspace(ws_id: str) -> None:
    """
    Soft-delete a workspace and its RAGFlow tenant + technical user.

    All data (datasets, documents, chunks) is preserved on disk and in DB —
    we only flip ``status='0'`` so the resources stop appearing in lists.
    Use ``restore_workspace`` to undo, or ``purge_workspace`` to hard-delete.
    """
    from api.db.db_models import DB, User, Tenant
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        return
    tenant_id = ws.tenant_id

    with DB.connection_context():
        WorkspaceService.update_by_id(ws_id, {"status": "0"})
        Tenant.update(status="0").where(Tenant.id == tenant_id).execute()
        # Technical user shares the tenant_id (RAGFlow convention)
        User.update(status="0").where(User.id == tenant_id).execute()


def restore_workspace(ws_id: str) -> None:
    """Reverse of ``deprovision_workspace`` — flip status back to '1'."""
    from api.db.db_models import DB, User, Tenant
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        return
    tenant_id = ws.tenant_id

    with DB.connection_context():
        WorkspaceService.update_by_id(ws_id, {"status": "1"})
        Tenant.update(status="1").where(Tenant.id == tenant_id).execute()
        User.update(status="1").where(User.id == tenant_id).execute()


def purge_workspace(ws_id: str) -> None:
    """
    HARD delete a workspace and ALL its data.

    Removes:
    - Workspace + WsMembers + WsGroups + group memberships + group datasets
    - RAGFlow Tenant + technical User + UserTenant rows
    - All datasets (Knowledgebase) attached to the tenant + their documents
    - Document chunks in vector store (best-effort)

    Irreversible. Caller must guarantee superuser auth + explicit confirmation.
    """
    from api.db.db_models import (
        DB, User, Tenant, UserTenant, Workspace,
        WsMember, WsGroup, WsGroupMember, WsGroupDataset,
        Knowledgebase, Document,
    )
    from api.db.services.workspace_service import WorkspaceService

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        return
    tenant_id = ws.tenant_id

    with DB.connection_context():
        # Group-scoped rows must go before groups
        group_ids = [g.id for g in WsGroup.select(WsGroup.id).where(WsGroup.workspace_id == ws_id)]
        if group_ids:
            WsGroupMember.delete().where(WsGroupMember.group_id.in_(group_ids)).execute()
            WsGroupDataset.delete().where(WsGroupDataset.group_id.in_(group_ids)).execute()
        WsGroup.delete().where(WsGroup.workspace_id == ws_id).execute()
        WsMember.delete().where(WsMember.workspace_id == ws_id).execute()

        # Documents → datasets attached to this tenant
        kb_ids = [k.id for k in Knowledgebase.select(Knowledgebase.id).where(Knowledgebase.tenant_id == tenant_id)]
        if kb_ids:
            Document.delete().where(Document.kb_id.in_(kb_ids)).execute()
        Knowledgebase.delete().where(Knowledgebase.tenant_id == tenant_id).execute()

        # Tenant + UserTenant + technical user
        UserTenant.delete().where(UserTenant.tenant_id == tenant_id).execute()
        Tenant.delete().where(Tenant.id == tenant_id).execute()
        User.delete().where(User.id == tenant_id).execute()

        # Finally the workspace row itself
        Workspace.delete().where(Workspace.id == ws_id).execute()


def provision_user(
    *,
    email: str,
    nickname: str,
    org_id: str,
    org_role: str,
    ws_id: str | None = None,
    ws_role: str | None = None,
    invited_by: str,
) -> str:
    """
    Create a human user through the top-down B2B provisioning pipeline.

    This function creates, inside a single transaction:

      1. RAGFlow ``User`` row — status=1, is_active=0 (inactive until the
         invite token is consumed and a real password is set)
      2. **Personal tenant shell** (RAGFlow convention, required by legacy code):
         - ``Tenant`` row with ``id = user_id``
         - ``UserTenant`` with ``role = OWNER`` (strict requirement of
           ``TenantService.get_info_by``)
         - Root ``File`` folder (``parent_id = self``, tenant-scoped)
         The shell is intentionally empty — no ``TenantLLM`` rows, no datasets —
         real data lives in workspace tenants. See module docstring for rationale.
      3. ``OrgMember`` — role = ``org_role`` (``org_admin`` | ``member``)
      4. Optionally: ``WsMember`` + ``UserTenant`` (role=NORMAL) via
         ``grant_workspace_access`` when ``ws_id`` is provided

    The password stored at creation time is a random unguessable string —
    the user cannot log in until they consume the invite token via
    ``POST /v1/user/set_initial_password``, which sets the real hash and
    flips ``is_active`` to ``'1'``.

    Returns the new ``user_id``.
    """
    from api.db import FileType, UserTenantRole
    from api.db.db_models import DB
    from api.db.services.user_service import UserService, TenantService, UserTenantService
    from api.db.services.file_service import FileService
    from api.db.services.org_service import OrgMemberService
    from api.db.services.workspace_service import WorkspaceService

    if org_role not in ("org_admin", "member"):
        raise ValueError(f"invalid org_role: {org_role}")
    if ws_id and ws_role not in ("ws_admin", "editor", "viewer"):
        raise ValueError(f"invalid ws_role: {ws_role}")

    # Pre-checks outside the transaction — cheap and give better error messages.
    if UserService.query(email=email):
        raise ValueError(f"email '{email}' already exists")

    ws = None
    if ws_id:
        ok, ws = WorkspaceService.get_by_id(ws_id)
        if not ok or not ws or ws.org_id != org_id:
            raise ValueError("workspace not found or not in this org")
    else:
        # No explicit workspace → assign to the org's default workspace.
        # Every user MUST land in a workspace (B2B rule — no personal-tenant usage).
        ws = WorkspaceService.get_default_workspace(org_id)
        if ws is None:
            raise ValueError(
                "This organisation has no active workspace. "
                "Create at least one workspace before inviting users."
            )
        ws_role = ws_role or "viewer"

    user_id = get_uuid()
    unguessable_password = secrets.token_urlsafe(32)

    with DB.connection_context():
        UserService.save(**{
            "id": user_id,
            "nickname": nickname,
            "email": email,
            "password": generate_password_hash(unguessable_password),
            "login_channel": "password",
            "is_superuser": False,
            "is_active": "0",  # gated behind invite token consumption
            "status": "1",
        })

        # Personal tenant shell — see module docstring. Empty on purpose.
        TenantService.insert(**{
            "id": user_id,
            "name": f"{nickname}'s Kingdom",
            "llm_id": "",
            "embd_id": "",
            "asr_id": "",
            "img2txt_id": "",
            "rerank_id": "",
            "parser_ids": _DEFAULT_PARSER_IDS,
        })
        UserTenantService.insert(**{
            "id": get_uuid(),
            "user_id": user_id,
            "tenant_id": user_id,
            "role": UserTenantRole.OWNER,
            "invited_by": invited_by,
        })
        root_file_id = get_uuid()
        FileService.insert({
            "id": root_file_id,
            "parent_id": root_file_id,
            "tenant_id": user_id,
            "created_by": user_id,
            "name": "/",
            "type": FileType.FOLDER.value,
            "size": 0,
            "location": "",
        })

        OrgMemberService.save(**{
            "id": get_uuid(),
            "org_id": org_id,
            "user_id": user_id,
            "role": org_role,
            "invited_by": invited_by,
        })

        if ws is not None:
            grant_workspace_access(
                ws=ws,
                target_user_id=user_id,
                role=ws_role,
                member_id=get_uuid(),
            )

    return user_id


def deprovision_user(user_id: str) -> None:
    """
    Soft-delete a human user: flip is_active to '0' and status to '0'.

    Org/workspace memberships are left intact so audit trails remain
    intelligible; the user simply can't log in anymore.
    """
    from api.db.db_models import DB, User

    with DB.connection_context():
        User.update(is_active="0", status="0").where(User.id == user_id).execute()


def grant_workspace_access(ws, target_user_id: str, role: str, member_id: str):
    """
    Grant a user access to a workspace.

    Steps:
    1. Create WsMember record
    2. Create UserTenant so RAGFlow lets the user access the workspace's tenant

    Args:
        ws: Workspace object (must have .id and .tenant_id)
        target_user_id: The user being granted access
        role: ws_admin | editor | viewer
        member_id: Pre-generated ID for the WsMember record
    """
    from api.db.db_models import DB
    from api.db.services.workspace_service import WsMemberService
    from api.db.services.user_service import UserTenantService

    with DB.connection_context():
        WsMemberService.save(**{
            "id": member_id,
            "workspace_id": ws.id,
            "user_id": target_user_id,
            "role": role,
        })

        # RAGFlow UserTenant — role is always "normal" because the real
        # role is in ws_member. RAGFlow only needs to know "this user
        # can access this tenant".
        UserTenantService.save(**{
            "id": get_uuid(),
            "user_id": target_user_id,
            "tenant_id": ws.tenant_id,
            "role": "normal",
            "invited_by": target_user_id,
        })
