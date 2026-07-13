"""Quota tokens RAG : la limite org doit s'appliquer à la SOMME des workspaces.

Bug corrigé le 2026-07-13 : check_token_quota comparait la conso d'UN SEUL
workspace à la limite de l'org — une org à N workspaces pouvait donc consommer
N x max_tokens_monthly. Ces tests verrouillent la sémantique org-wide.
"""
import datetime

import pytest

pytestmark = pytest.mark.p1


@pytest.fixture()
def org_two_workspaces():
    """Org avec quota 1000 tokens/mois, période courante, 2 workspaces."""
    from api.db.db_models import DB, Organisation, Workspace, TokenUsageDaily
    from common.misc_utils import get_uuid

    org_id = get_uuid()
    t1, t2 = get_uuid(), get_uuid()
    today = datetime.date.today()
    first = today.replace(day=1)
    last = (first + datetime.timedelta(days=40)).replace(day=1) - datetime.timedelta(days=1)
    with DB.connection_context():
        Organisation.create(id=org_id, name=f"quota-test-{org_id[:6]}",
                            slug=f"quota-test-{org_id[:6]}", created_by="tester",
                            max_tokens_monthly=1000, allow_overage=False,
                            current_period_start=str(first), current_period_end=str(last))
        for i, tid in enumerate((t1, t2)):
            Workspace.create(id=get_uuid(), org_id=org_id, tenant_id=tid,
                             name=f"ws-{i}", created_by="tester")
    yield org_id, t1, t2
    with DB.connection_context():
        TokenUsageDaily.delete().where(TokenUsageDaily.tenant_id.in_([t1, t2])).execute()
        Workspace.delete().where(Workspace.org_id == org_id).execute()
        Organisation.delete().where(Organisation.id == org_id).execute()


def _add_usage(tenant_id: str, tokens: int):
    from api.db.db_models import DB, TokenUsageDaily
    with DB.connection_context():
        TokenUsageDaily.create(tenant_id=tenant_id, llm_factory="test", model_type="chat",
                               llm_name="test-model", date=str(datetime.date.today()),
                               tokens=tokens)


def _flush_cache(org_id: str):
    from api.db.services.quota_service import invalidate_org_quota_cache
    from api.db.db_models import DB, Organisation
    with DB.connection_context():
        org = Organisation.get_by_id(org_id)
    invalidate_org_quota_cache(org_id, str(org.current_period_start))


def test_org_quota_sums_all_workspaces(org_two_workspaces):
    """600 + 600 sur deux workspaces : chacun est SOUS la limite de 1000,
    la somme est au-dessus — le quota doit être dépassé pour LES DEUX
    (l'ancien comptage par-workspace disait False)."""
    from api.db.services.quota_service import check_token_quota
    org_id, t1, t2 = org_two_workspaces
    _add_usage(t1, 600)
    _add_usage(t2, 600)
    _flush_cache(org_id)

    for tid in (t1, t2):
        qs = check_token_quota(tid)
        assert qs["enabled"] is True
        assert qs["current_usage"] == 1200, "usage must be the ORG-WIDE sum"
        assert qs["quota_exceeded"] is True
        assert qs["allow_overage"] is False


def test_org_quota_under_limit_not_exceeded(org_two_workspaces):
    from api.db.services.quota_service import check_token_quota
    org_id, t1, t2 = org_two_workspaces
    _add_usage(t1, 300)
    _add_usage(t2, 300)
    _flush_cache(org_id)

    qs = check_token_quota(t1)
    assert qs["current_usage"] == 600
    assert qs["quota_exceeded"] is False


def test_workspace_period_usage_stays_per_workspace(org_two_workspaces):
    """La ventilation par workspace (affichage panel) reste bien par tenant."""
    from api.db.services.quota_service import workspace_period_usage
    from api.db.db_models import DB, Organisation
    org_id, t1, t2 = org_two_workspaces
    _add_usage(t1, 100)
    _add_usage(t2, 900)
    with DB.connection_context():
        org = Organisation.get_by_id(org_id)
    ps, pe = str(org.current_period_start), str(org.current_period_end)
    assert workspace_period_usage(t1, ps, pe) == 100
    assert workspace_period_usage(t2, ps, pe) == 900


def test_no_quota_configured_is_permissive(org_two_workspaces):
    from api.db.db_models import DB, Organisation
    from api.db.services.quota_service import check_token_quota
    org_id, t1, _ = org_two_workspaces
    with DB.connection_context():
        Organisation.update(max_tokens_monthly=0).where(Organisation.id == org_id).execute()
    qs = check_token_quota(t1)
    assert qs["enabled"] is False and qs["quota_exceeded"] is False


def test_unknown_tenant_is_permissive():
    from api.db.services.quota_service import check_token_quota
    qs = check_token_quota("no-such-tenant-xxxxxxxxxxxxxxxx")
    assert qs["enabled"] is False and qs["quota_exceeded"] is False
