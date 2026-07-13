"""Alertes budget Code (80/100%) — anti-spam par cycle, team + siège."""
import pytest

from test.multitenant.test_code_provisioning import FakeLiteLLM, org_with_entitlement  # noqa: F401

pytestmark = pytest.mark.p1


@pytest.fixture()
def alert_env(org_with_entitlement, monkeypatch):  # noqa: F811
    """Org admin réel + mailer patché (capture les envois) + SMTP 'configuré'."""
    from api.db.db_models import DB, User, OrgMember, CodeBudgetAlert, CodeTeam
    from common.misc_utils import get_uuid
    from management.server.services import mailer

    org_id = org_with_entitlement
    admin_id = get_uuid()
    admin_email = f"admin-{admin_id[:8]}@alerts.test"
    with DB.connection_context():
        User.create(id=admin_id, nickname="alert-admin", email=admin_email, status="1")
        OrgMember.create(id=get_uuid(), org_id=org_id, user_id=admin_id,
                         role="org_admin", status="1")

    sent: list[tuple[str, str, str]] = []

    async def fake_send(to, subject, body):
        sent.append((to, subject, body))
        return True

    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_mail", fake_send)

    yield org_id, admin_email, sent

    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_id)]
        if team_ids:
            CodeBudgetAlert.delete().where(CodeBudgetAlert.ref_id.in_(team_ids)).execute()
        OrgMember.delete().where(OrgMember.user_id == admin_id).execute()
        User.delete().where(User.id == admin_id).execute()


def _mk_team(org_id, fake, budget=10.0, name="squad"):
    from management.server.services import code_provisioning as cp
    return cp.create_code_team(org_id=org_id, name=name, max_budget=budget,
                               model_access=[], created_by="tester", client=fake)


def test_team_alert_at_80_sent_once(alert_env):
    from management.server.services.code_budget_alerts import check_and_send_alerts
    org_id, admin_email, sent = alert_env
    fake = FakeLiteLLM()
    team = _mk_team(org_id, fake)
    fake.teams[team.litellm_team_id]["spend"] = 8.5  # 85%

    assert check_and_send_alerts(client=fake) == 1
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == admin_email
    assert "80%" in subject and team.name in subject
    assert "8.5" in body and "10" in body

    # même spend au run suivant -> silence (anti-spam)
    assert check_and_send_alerts(client=fake) == 0
    assert len(sent) == 1


def test_escalation_80_then_100_then_rearm(alert_env):
    from api.db.db_models import DB, CodeBudgetAlert
    from management.server.services.code_budget_alerts import check_and_send_alerts
    org_id, _, sent = alert_env
    fake = FakeLiteLLM()
    team = _mk_team(org_id, fake)

    fake.teams[team.litellm_team_id]["spend"] = 8.5
    assert check_and_send_alerts(client=fake) == 1  # 80%

    fake.teams[team.litellm_team_id]["spend"] = 10.5
    assert check_and_send_alerts(client=fake) == 1  # 100%
    assert "100%" in sent[-1][1]
    assert "refusées" in sent[-1][2]

    # reset de cycle LiteLLM : le spend retombe -> marqueurs supprimés
    fake.teams[team.litellm_team_id]["spend"] = 0.4
    assert check_and_send_alerts(client=fake) == 0
    with DB.connection_context():
        assert not CodeBudgetAlert.select().where(CodeBudgetAlert.ref_id == team.id).exists()

    # nouveau cycle, nouveau dépassement -> ré-alerté
    fake.teams[team.litellm_team_id]["spend"] = 9.0
    assert check_and_send_alerts(client=fake) == 1


def test_straight_to_100_sends_one_mail_marks_both(alert_env):
    from management.server.services.code_budget_alerts import check_and_send_alerts
    org_id, _, sent = alert_env
    fake = FakeLiteLLM()
    team = _mk_team(org_id, fake)
    fake.teams[team.litellm_team_id]["spend"] = 12.0  # saute 80 ET 100

    assert check_and_send_alerts(client=fake) == 1  # un seul mail, le plus haut
    assert "100%" in sent[-1][1]
    assert check_and_send_alerts(client=fake) == 0  # 80 marqué aussi, pas de rattrapage


def test_seat_alert_for_key_with_own_budget(alert_env):
    from management.server.services import code_provisioning as cp
    from management.server.services.code_budget_alerts import check_and_send_alerts
    org_id, _, sent = alert_env
    fake = FakeLiteLLM()
    team = _mk_team(org_id, fake, budget=50.0)
    key, _plain = cp.create_code_key(code_team_id=team.id, label="dev-bob",
                                     owner_user_id=None, created_by="tester",
                                     max_budget=5.0, client=fake)
    fake.keys[key.litellm_key_id]["spend"] = 4.2  # 84% du siège, team très en dessous

    assert check_and_send_alerts(client=fake) == 1
    assert "dev-bob" in sent[-1][1] and "80%" in sent[-1][1]
    # cleanup des marqueurs de la clé
    from api.db.db_models import DB, CodeBudgetAlert
    with DB.connection_context():
        CodeBudgetAlert.delete().where(CodeBudgetAlert.ref_id == key.id).execute()


def test_no_smtp_means_no_alert_and_no_marker(alert_env, monkeypatch):
    from api.db.db_models import DB, CodeBudgetAlert
    from management.server.services import mailer
    from management.server.services.code_budget_alerts import check_and_send_alerts
    org_id, _, sent = alert_env
    monkeypatch.setattr(mailer, "is_configured", lambda: False)
    fake = FakeLiteLLM()
    team = _mk_team(org_id, fake)
    fake.teams[team.litellm_team_id]["spend"] = 9.9

    assert check_and_send_alerts(client=fake) == 0
    assert sent == []
    with DB.connection_context():
        # pas de marqueur posé : l'alerte partira au premier run APRÈS
        # configuration du SMTP au lieu d'avoir été consommée en silence
        assert not CodeBudgetAlert.select().where(CodeBudgetAlert.ref_id == team.id).exists()
