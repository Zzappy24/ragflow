from api.db.db_models import DB, Organisation, OrgMember, Workspace, WsMember, Knowledgebase, Document
from api.db.services.common_service import CommonService
from common.misc_utils import get_uuid


class OrgService(CommonService):
    model = Organisation

    @classmethod
    @DB.connection_context()
    def get_by_slug(cls, slug):
        # Only match active orgs — deleted orgs should not block slug reuse.
        return cls.model.get_or_none(
            (cls.model.slug == slug) & (cls.model.status == "1")
        )

    @classmethod
    @DB.connection_context()
    def get_resource_counts(cls, org_id):
        """Count users, workspaces, datasets, and documents for an org.

        Only counts active (status='1') workspaces — soft-deleted ones are excluded.
        """
        active_ws = Workspace.select().where(
            (Workspace.org_id == org_id) & (Workspace.status == "1")
        )
        workspace_ids = [ws.id for ws in active_ws]
        tenant_ids = [ws.tenant_id for ws in active_ws]

        user_count = (
            OrgMember.select()
            .where((OrgMember.org_id == org_id) & (OrgMember.status == "1"))
            .count()
        )

        workspace_count = len(workspace_ids)

        dataset_count = (
            Knowledgebase.select()
            .where(Knowledgebase.tenant_id.in_(tenant_ids))
            .count()
        ) if tenant_ids else 0

        # Documents link to datasets via kb_id — single JOIN instead of 2 queries
        document_count = (
            Document.select()
            .join(Knowledgebase, on=(Document.kb_id == Knowledgebase.id))
            .where(Knowledgebase.tenant_id.in_(tenant_ids))
            .count()
        ) if tenant_ids else 0

        return {
            "users": user_count,
            "workspaces": workspace_count,
            "datasets": dataset_count,
            "documents": document_count,
        }


class OrgMemberService(CommonService):
    model = OrgMember

    @classmethod
    @DB.connection_context()
    def get_membership(cls, org_id, user_id):
        return cls.model.get_or_none(
            (cls.model.org_id == org_id) &
            (cls.model.user_id == user_id) &
            (cls.model.status == "1")
        )

    @classmethod
    @DB.connection_context()
    def list_by_org(cls, org_id):
        return list(
            cls.model.select()
            .where((cls.model.org_id == org_id) & (cls.model.status == "1"))
        )

    @classmethod
    @DB.connection_context()
    def list_orgs_for_user(cls, user_id):
        """Return all org memberships for a user."""
        return list(
            cls.model.select()
            .where((cls.model.user_id == user_id) & (cls.model.status == "1"))
        )
