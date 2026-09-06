"""CUSTOM B2B SaaS — audit sécurité du panel (2026-09-06), pins des correctifs.

F1 · Prise de contrôle inter-org d'un compte en attente : un org_admin
     pouvait attacher à SON org un utilisateur provisionné par une autre org
     mais pas encore activé, renvoyer l'invitation (lien renvoyé à l'écran),
     poser le mot de passe et se connecter à sa place.
F2 · Empoisonnement du workspace de référence : `settings_json.model_template`
     (superuser-only) était posable par un org_admin via PUT workspace → tous
     les nouveaux workspaces de la plateforme copiaient ses modèles, clés API
     et api_base.
F3 · Un org_admin pouvait relever ses propres quotas (max_*).
F4 · `require_org_admin` ignorait le statut de l'org : l'admin d'une org
     archivée gardait la main (création/restauration de workspaces, bridge).

Pins statiques (pas de serveur) : un merge upstream ou un refactor qui retire
une garde fait échouer le test correspondant.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
MEMBERS = ROOT / "management/server/routers/members.py"
USERS = ROOT / "management/server/routers/users.py"
USER_API = ROOT / "api/apps/restful_apis/user_api.py"
WORKSPACES = ROOT / "management/server/routers/workspaces.py"
ORGS = ROOT / "management/server/routers/orgs.py"
DEPS = ROOT / "management/server/auth/dependencies.py"


def _func_source(path: pathlib.Path, name: str) -> str:
    src = path.read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} introuvable dans {path.name}")


class TestF1PendingUserTakeover:
    def test_add_org_member_refuses_pending_accounts_with_uniform_404(self):
        body = _func_source(MEMBERS, "add_org_member")
        guard = body.index('is_active", "0")) != "1"')
        assert guard < body.index("OrgMemberService.save("), "le refus des comptes non activés doit précéder l'ajout"
        # même message que « inconnu » : pas d'oracle d'existence
        assert body.count("not found in RAGFlow") >= 2

    def test_resend_invite_hides_link_when_email_was_sent(self):
        body = _func_source(USERS, "resend_invite")
        assert "None if email_sent else invite_url" in body

    def test_invite_code_cannot_reset_an_active_account(self):
        for name in ("set_initial_password", "internal_invite_prepare"):
            body = _func_source(USER_API, name)
            assert 'is_active", "0")) == "1"' in body, f"{name} doit refuser un compte déjà actif"
            assert "Account already active" in body


class TestF2ModelTemplatePoisoning:
    def test_update_workspace_strips_model_template_and_preserves_existing_flag(self):
        body = _func_source(WORKSPACES, "update_workspace")
        assert 'incoming.pop("model_template", None)' in body
        assert 'get("model_template") is True' in body
        assert body.index('incoming.pop("model_template"') < body.index("WorkspaceService.update_by_id(")

    def test_only_superuser_routes_write_the_flag(self):
        src = WORKSPACES.read_text()
        # les seules écritures du drapeau vivent dans le helper des routes superuser
        writers = [n for n in ("set_workspace_model_template", "unset_workspace_model_template")]
        for name in writers:
            assert "require_superuser" in _func_source(WORKSPACES, name)
        assert src.count('"model_template"') >= 3


class TestF3OrgAdminQuotas:
    def test_update_org_restricts_non_superusers_to_name(self):
        body = _func_source(ORGS, "update_org")
        assert 'set(update_data) - {"name"}' in body
        assert "Superuser only" in body
        assert body.index("Superuser only") < body.index("OrgService.update_by_id(")


class TestF4ArchivedOrgAdmins:
    def test_require_org_admin_checks_org_status_for_non_superusers(self):
        body = _func_source(DEPS, "require_org_admin")
        assert 'str(org.status) != "1"' in body
        assert body.index("org.status") < body.index("get_membership("), "le statut de l'org se vérifie avant l'appartenance"
        assert body.index("is_superuser") < body.index("org.status"), "le superuser reste exempt"
