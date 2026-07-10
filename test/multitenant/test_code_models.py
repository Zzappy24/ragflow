"""Schema smoke test for the Code product tables."""
import pytest

pytestmark = pytest.mark.p1


def test_code_tables_exist_with_expected_columns():
    from api.db.db_models import DB, CodeEntitlement, CodeTeam, CodeTeamMember, CodeKey

    with DB.connection_context():
        cols_ent = {c.name for c in DB.get_columns("code_entitlement")}
        cols_team = {c.name for c in DB.get_columns("code_team")}
        cols_member = {c.name for c in DB.get_columns("code_team_member")}
        cols_key = {c.name for c in DB.get_columns("code_key")}

    assert {"id", "org_id", "status", "org_code_budget", "budget_period", "created_by"} <= cols_ent
    assert {"id", "org_id", "name", "litellm_team_id", "max_budget",
            "model_access", "status", "sync_status", "sync_error", "created_by"} <= cols_team
    assert {"id", "code_team_id", "user_id", "role"} <= cols_member
    assert {"id", "code_team_id", "label", "litellm_key_id", "key_masked",
            "owner_user_id", "status", "sync_status", "sync_error", "created_by"} <= cols_key
