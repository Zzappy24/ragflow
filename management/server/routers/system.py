"""
System stats and health routes (superuser only).
"""
from fastapi import APIRouter, Depends

from management.server.auth.dependencies import require_superuser
from management.server.models.schemas import SystemStats

router = APIRouter()


@router.get("/stats", response_model=SystemStats)
def system_stats(user=Depends(require_superuser)):
    """Get global system statistics."""
    # CUSTOM B2B SaaS — import Peewee direct au lieu de KnowledgebaseService.
    # Le service cascade vers api_utils → mcp/openai/etc. Ici on ne fait qu'un
    # simple SELECT COUNT, on utilise Knowledgebase directement.
    from api.db.db_models import DB, Organisation, Workspace, User, Knowledgebase

    with DB.connection_context():
        total_orgs = Organisation.select().where(Organisation.status == "1").count()

        # Exclude technical tenants (ws-*@internal) from workspace count
        total_workspaces = (
            Workspace.select()
            .where(
                (Workspace.status == "1") &
                Workspace.tenant_id.is_null(False)
            )
            .count()
        )

        # Real human users only: active, not technical tenant users, not tombstones
        technical_user_ids = (
            Workspace.select(Workspace.tenant_id)
            .where(Workspace.tenant_id.is_null(False))
        )
        total_users = (
            User.select()
            .where(
                (User.status == "1") &
                (~User.email.startswith("purged_")) &
                User.id.not_in(technical_user_ids)
            )
            .count()
        )

        # Datasets scoped to workspace tenants only (excludes personal tenant datasets)
        ws_tenant_ids = list(
            Workspace.select(Workspace.tenant_id)
            .where(
                (Workspace.status == "1") &
                Workspace.tenant_id.is_null(False)
            )
            .tuples()
        )
        ws_tenant_ids = [row[0] for row in ws_tenant_ids]

    if ws_tenant_ids:
        total_datasets = (
            Knowledgebase.select()
            .where(
                (Knowledgebase.status == "1") &
                Knowledgebase.tenant_id.in_(ws_tenant_ids)
            )
            .count()
        )
    else:
        total_datasets = 0

    return SystemStats(
        total_orgs=total_orgs,
        total_workspaces=total_workspaces,
        total_users=total_users,
        total_datasets=total_datasets,
        total_documents=0,  # TODO: count from DocumentService when needed
    )


@router.get("/health")
def health():
    """Health check endpoint (no auth required)."""
    from api.db.db_models import DB
    try:
        with DB.connection_context():
            DB.execute_sql("SELECT 1")
        return {"status": "healthy", "db": "connected"}
    except Exception:
        return {"status": "unhealthy", "db": "connection failed"}
