from api.db.db_models import DB, WsGroup, WsGroupMember, WsGroupDataset
from api.db.services.common_service import CommonService


class GroupService(CommonService):
    model = WsGroup

    @classmethod
    @DB.connection_context()
    def list_by_workspace(cls, workspace_id):
        return list(
            cls.model.select()
            .where((cls.model.workspace_id == workspace_id) & (cls.model.status == "1"))
        )

    @classmethod
    @DB.connection_context()
    def get_user_groups(cls, workspace_id, user_id):
        """Return all groups a user belongs to in a workspace."""
        group_ids = [
            gm.group_id for gm in
            WsGroupMember.select(WsGroupMember.group_id)
            .join(WsGroup, on=(WsGroupMember.group_id == WsGroup.id))
            .where(
                (WsGroupMember.user_id == user_id) &
                (WsGroup.workspace_id == workspace_id) &
                (WsGroup.status == "1")
            )
        ]
        if not group_ids:
            return []
        return list(cls.model.select().where(cls.model.id.in_(group_ids)))

    @classmethod
    @DB.connection_context()
    def get_datasets_for_groups(cls, group_ids):
        """Return the set of dataset_ids visible to a list of groups (union)."""
        if not group_ids:
            return set()
        return {
            gd.dataset_id for gd in
            WsGroupDataset.select(WsGroupDataset.dataset_id)
            .where(WsGroupDataset.group_id.in_(group_ids))
        }


class GroupMemberService(CommonService):
    model = WsGroupMember

    @classmethod
    @DB.connection_context()
    def list_by_group(cls, group_id):
        return list(cls.model.select().where(cls.model.group_id == group_id))

    @classmethod
    @DB.connection_context()
    def remove(cls, group_id, user_id):
        return cls.model.delete().where(
            (cls.model.group_id == group_id) & (cls.model.user_id == user_id)
        ).execute()


class GroupDatasetService(CommonService):
    model = WsGroupDataset

    @classmethod
    @DB.connection_context()
    def list_by_group(cls, group_id):
        return list(cls.model.select().where(cls.model.group_id == group_id))

    @classmethod
    @DB.connection_context()
    def remove(cls, group_id, dataset_id):
        return cls.model.delete().where(
            (cls.model.group_id == group_id) & (cls.model.dataset_id == dataset_id)
        ).execute()
