"""Pins du flux d'invitations d'organisation unifiées (2026-09-02).

« Tout est invitation », zéro-leak : l'admin ne peut pas déduire si un
email a déjà un compte (ni via la réponse, ni via les endpoints — aucune
vérification d'existence n'a lieu à l'invitation) ; la distinction se joue
à l'acceptation, côté invité. Personne n'est rattaché à une organisation
sans avoir cliqué « accepter ».
"""
import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = (REPO / "management" / "server" / "routers" / "org_invites.py").read_text()
TREE = ast.parse(SRC)


def _func(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} introuvable")


def test_admin_endpoints_require_org_admin():
    for name in ("invite_members", "list_invitations", "resend_invitation", "cancel_invitation"):
        assert "require_org_admin" in ast.dump(_func(name)), f"{name} sans require_org_admin"


def test_public_endpoints_have_no_auth_dependency():
    """introspect/accept sont volontairement publics : l'accès est prouvé par
    le token signé reçu par email (même modèle que set-password et claim Code)."""
    for name in ("introspect_invitation", "accept_invitation"):
        dump = ast.dump(_func(name))
        assert "get_current_user_id" not in dump, f"{name} ne doit pas exiger d'auth admin"
        assert "_load_valid_invite" in dump, f"{name} doit valider le token"


def test_no_account_probing_at_invite_time():
    """Zéro-leak : la création d'invitation ne consulte JAMAIS l'existence du
    compte (_existing_active_user) — seule la détection 'déjà membre' est
    permise dans invite_members (info visible dans la liste de toute façon).
    _create_and_send doit être rigoureusement identique dans les deux cas."""
    dump = ast.dump(_func("_create_and_send"))
    assert "_existing_active_user" not in dump
    assert "UserService" not in dump


def test_accept_hashes_base64_of_password():
    """Le login RAGFlow vérifie le hash de Base64(mot de passe) — cf. CLAUDE.md
    « Password Encryption Pipeline ». Un hash du mot de passe brut rendrait le
    compte inutilisable silencieusement."""
    dump = ast.dump(_func("accept_invitation"))
    assert "b64encode" in dump and "generate_password_hash" in dump


def test_cancel_kills_the_token():
    """L'annulation supprime la row : le JWT encore valide devient mort
    (la row est la source de vérité, _load_valid_invite la vérifie)."""
    assert "delete_instance" in ast.dump(_func("cancel_invitation"))


def test_orginvite_model_exists():
    src = (REPO / "api" / "db" / "db_models.py").read_text()
    assert "class OrgInvite(DataBaseModel)" in src
    assert '"org_invite"' in src or "'org_invite'" in src


def test_ws_preassignment_materializes_at_accept_only():
    """La pré-affectation workspace (ws_id/ws_role portée par l'invitation)
    ne crée AUCUN membership à l'invitation — elle se matérialise à
    l'acceptation : provision_user(ws_id=...) pour un nouveau compte,
    grant_workspace_access pour un compte existant (jamais sans le check
    d'appartenance du ws à l'org)."""
    dump_invite = ast.dump(_func("invite_members"))
    assert "grant_workspace_access" not in dump_invite, "l'invitation ne doit rien matérialiser"
    dump_accept = ast.dump(_func("accept_invitation"))
    assert "grant_workspace_access" in dump_accept
    accept_src = ast.get_source_segment(SRC, _func("accept_invitation"))
    assert "ws_id=inv.ws_id" in accept_src, "provision_user doit recevoir la pré-affectation"
    assert "ws.org_id == inv.org_id" in accept_src, "le ws doit être re-validé contre l'org à l'acceptation"


def test_pending_invitation_is_editable_without_resend():
    """PATCH /org-invites/{id} : modifier la destination d'une invitation en
    attente SANS renvoyer d'email — possible parce que le token ne porte que
    l'invite_id, la destination est lue dans la row à l'acceptation. La garde
    require_org_admin s'applique, le ws est re-validé contre l'org."""
    node = _func("update_invitation")
    dump = ast.dump(node)
    assert "require_org_admin" in dump
    assert "send_mail" not in dump, "l'édition ne doit PAS renvoyer d'email"
    src = ast.get_source_segment(SRC, node)
    assert "ws.org_id != inv.org_id" in src, "le ws doit être validé contre l'org de l'invitation"


def test_all_peewee_access_under_connection_context():
    """Doctrine mgmt : tout accès Peewee direct passe sous
    DB.connection_context() — hors contexte, FastAPI (threadpool) ouvre des
    connexions implicites par thread jamais rendues au pool → épuisement
    intermittent du pool MySQL, et TOUT le panel attend (recherches gelées,
    /stats en 504, sans aucun log — suspect du 2026-09-02)."""
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            dump = ast.dump(node)
            touches_db = any(m in dump for m in
                             ("'get_or_none'", "'select'", "'save'", "'delete_instance'", "'create'"))
            if touches_db and "OrgInvite" in dump or "Organisation" in dump:
                assert "connection_context" in dump, (
                    f"{node.name} touche Peewee hors DB.connection_context()"
                )
