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
    from api.db.db_models import DB, Organisation, Workspace, User
    from api.db.services.knowledgebase_service import KnowledgebaseService

    with DB.connection_context():
        total_orgs = Organisation.select().where(Organisation.status == "1").count()
        total_workspaces = Workspace.select().where(Workspace.status == "1").count()
        # Exclude technical workspace owners (ws-*@internal) from real-user count.
        total_users = (
            User.select()
            .where((User.status == "1") & (~User.email.endswith("@internal")))
            .count()
        )

    datasets = KnowledgebaseService.query(status="1")
    total_datasets = len(list(datasets))

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
    except Exception as e:
        return {"status": "unhealthy", "db": str(e)}
