"""
Provisioning service — handles workspace and member creation.

Creating a workspace in our RBAC system requires creating a "technical" RAGFlow
user and tenant, because RAGFlow internally ties all resources (datasets, documents,
chats, agents) to a tenant_id. Each workspace maps 1:1 to a RAGFlow tenant.
"""
from common.misc_utils import get_uuid
from werkzeug.security import generate_password_hash


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

    Returns the Workspace object.
    """
    import re
    from api.db.db_models import DB
    from api.db.services.user_service import UserService, TenantService, UserTenantService
    from api.db.services.workspace_service import WorkspaceService, WsMemberService

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

        # 2. Create RAGFlow tenant
        TenantService.save(**{
            "id": technical_user_id,  # tenant_id = user_id (RAGFlow convention)
            "name": name,
            "llm_id": "",
            "embd_id": "",
            "asr_id": "",
            "img2txt_id": "",
            "rerank_id": "",
            "parser_ids": "",
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
