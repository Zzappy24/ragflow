"""CUSTOM B2B SaaS — isolation inter-workspace étendue + cycle de vie des
comptes, EN DIRECT (serveur + panel + moteurs).

Complète test_live_security_recette.py (bases / documents / chats) avec les
familles où l'audit du 2026-09-06 avait trouvé des trous : agents, apps de
recherche, gestionnaire de fichiers, serveurs MCP, jetons API du workspace,
clés API par utilisateur, mémoires. Puis le cycle de vie d'un compte, du lien
d'invitation à la purge : mot de passe initial, rotation du jeton au
changement de mot de passe, cookie de session lié, désactivation par le
panel (jetons et clés morts dans la seconde), restauration, purge.

Outils : _live.py ; fixtures : conftest.py (live_*). Tout ce qui est créé
ici est supprimé ou purgé en fin de module.
"""
import io
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _live  # noqa: E402
from _live import API, api, bearer, code, id_of, login, refused, rsa_password  # noqa: E402

pytestmark = _live.live_markers()
RUN = int(time.time()) % 100000


def _create_or_skip(auth, method, path, what, **kw):
    r = api(auth, method, path, **kw)
    if code(r) != 0 or not id_of(r):
        pytest.skip(f"{what} : création impossible dans cet environnement ({r.status_code} {r.text[:120]})")
    return id_of(r)


# ------------------------------------------------ ressources du 2e workspace
@pytest.fixture(scope="module")
def foreign(live_second):
    """Ressources créées dans le 2e workspace, visées ensuite depuis le 1er."""
    a = live_second["auth"]
    res = {}
    r = api(a, "GET", "/agents/templates")
    templates = r.json().get("data") or []
    dsl = next((t.get("dsl") for t in templates if isinstance(t, dict) and t.get("dsl")), None)
    if dsl:
        r = api(a, "POST", "/agents", json={"title": f"recette-live agent {RUN}", "dsl": dsl})
        res["agent"] = id_of(r) if code(r) == 0 else None
    r = api(a, "POST", "/searches", json={"name": f"recette-live search {RUN}"})
    res["search"] = id_of(r) if code(r) == 0 else None
    r = api(a, "POST", "/files", files={"file": (f"secret-{RUN}.txt", io.BytesIO(b"PERROQUET-FICHIER"), "text/plain")})
    res["file"] = id_of(r) if code(r) == 0 else None
    r = api(a, "POST", "/mcp/servers", json={"name": f"recette-live mcp {RUN}", "url": "https://example.com/sse", "server_type": "sse"})
    res["mcp"] = id_of(r) if code(r) == 0 else None
    r = api(a, "POST", "/system/tokens", json={})
    res["token"] = (r.json()["data"].get("token") if code(r) == 0 else None)
    yield res
    # le workspace entier est purgé par la fixture live_second


class TestCrossWorkspaceIsolationExtended:
    def test_agents(self, live_primary, foreign):
        if not foreign.get("agent"):
            pytest.skip("aucun template d'agent disponible")
        a, agent = live_primary["auth"], foreign["agent"]
        probes = {
            "sessions list": api(a, "GET", f"/agents/{agent}/sessions"),
            "session create": api(a, "POST", f"/agents/{agent}/sessions", json={}),
            "sessions delete": api(a, "DELETE", f"/agents/{agent}/sessions", json={"ids": []}),
            "input form": api(a, "GET", f"/agents/{agent}/components/begin/input-form"),
            "upload": api(a, "POST", f"/agents/{agent}/upload", files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")}),
        }
        leaks = [k for k, r in probes.items() if not refused(r)]
        assert not leaks, f"agent d'un autre workspace atteignable : {leaks}"
        listed = api(a, "GET", "/agents").json().get("data") or []
        listed = listed if isinstance(listed, list) else listed.get("agents", [])
        assert agent not in [x.get("id") for x in listed if isinstance(x, dict)]

    def test_search_apps(self, live_primary, foreign):
        if not foreign.get("search"):
            pytest.skip("app de recherche non créable")
        a, sid = live_primary["auth"], foreign["search"]
        probes = {
            "get": api(a, "GET", f"/searches/{sid}"),
            "update": api(a, "PUT", f"/searches/{sid}", json={"name": "pwned"}),
            "delete": api(a, "DELETE", f"/searches/{sid}"),
        }
        leaks = [k for k, r in probes.items() if not refused(r)]
        assert not leaks, f"app de recherche d'un autre workspace atteignable : {leaks}"
        listed = api(a, "GET", "/searches").json().get("data") or []
        listed = listed if isinstance(listed, list) else listed.get("search_apps", listed.get("searches", []))
        assert sid not in [x.get("id") for x in listed if isinstance(x, dict)]

    def test_file_manager(self, live_primary, foreign):
        if not foreign.get("file"):
            pytest.skip("gestionnaire de fichiers : upload impossible")
        a, fid = live_primary["auth"], foreign["file"]
        probes = {
            "get": api(a, "GET", f"/files/{fid}"),
            "download token": api(a, "POST", f"/files/{fid}/download-token"),
            "parent": api(a, "GET", f"/files/{fid}/parent"),
            "ancestors": api(a, "GET", f"/files/{fid}/ancestors"),
            "delete": api(a, "DELETE", "/files", json={"ids": [fid]}),
            "move": api(a, "POST", "/files/move", json={"ids": [fid], "parent_id": "root"}),
        }
        leaks = [k for k, r in probes.items() if not refused(r) or "PERROQUET-FICHIER" in r.text]
        assert not leaks, f"fichier d'un autre workspace atteignable : {leaks}"

    def test_mcp_servers(self, live_primary, foreign):
        if not foreign.get("mcp"):
            pytest.skip("serveur MCP non créable (garde SSRF : example.com doit résoudre)")
        a, mid = live_primary["auth"], foreign["mcp"]
        probes = {
            "get": api(a, "GET", f"/mcp/servers/{mid}"),
            "update": api(a, "PUT", f"/mcp/servers/{mid}", json={"name": "pwned"}),
            "test": api(a, "POST", f"/mcp/servers/{mid}/test", json={}),
            "delete": api(a, "DELETE", f"/mcp/servers/{mid}"),
        }
        leaks = [k for k, r in probes.items() if not refused(r)]
        assert not leaks, f"serveur MCP d'un autre workspace atteignable : {leaks}"
        listed = api(a, "GET", "/mcp/servers").json().get("data") or []
        listed = listed if isinstance(listed, list) else listed.get("mcp_servers", [])
        assert mid not in [x.get("id") for x in listed if isinstance(x, dict)]

    def test_workspace_api_tokens(self, live_primary, live_second, foreign):
        if not foreign.get("token"):
            pytest.skip("jeton API du 2e workspace non créé")
        a, tok = live_primary["auth"], foreign["token"]
        listed = api(a, "GET", "/system/tokens").json().get("data") or []
        assert tok not in [t.get("token") for t in listed if isinstance(t, dict)], "le jeton d'un autre workspace est listé"
        assert refused(api(a, "DELETE", f"/system/tokens/{tok}")) or tok in [
            t.get("token") for t in (api(live_second["auth"], "GET", "/system/tokens").json().get("data") or []) if isinstance(t, dict)
        ], "le jeton d'un autre workspace a pu être supprimé"

    def test_user_api_keys_are_creator_scoped(self, live_primary, editor_auth):
        r = api(editor_auth, "POST", "/api_keys", json={"name": f"recette-live editor key {RUN}", "permissions": ["dataset.read"]})
        assert code(r) == 0, r.text
        scope_id = r.json()["data"].get("id") or r.json()["data"].get("scope_id")
        try:
            listed = api(live_primary["auth"], "GET", "/api_keys").json().get("data") or []
            assert scope_id not in [k.get("id") for k in listed if isinstance(k, dict)], "la clé d'un autre utilisateur est listée"
            r = api(live_primary["auth"], "DELETE", f"/api_keys/{scope_id}")
            still = api(editor_auth, "GET", "/api_keys").json().get("data") or []
            assert refused(r) or scope_id in [k.get("id") for k in still if isinstance(k, dict)], "la clé d'un autre utilisateur a pu être révoquée"
        finally:
            api(editor_auth, "DELETE", f"/api_keys/{scope_id}")

    def test_memories_are_per_user(self, live_primary, viewer_auth, editor_auth):
        """Les mémoires sont STRICTEMENT par utilisateur (B2B) : ni un autre
        membre ni le ws_admin ne lisent celles d'un collègue."""
        from api.db.db_models import DB, Tenant
        with DB.connection_context():
            t = Tenant.get_or_none(Tenant.id == live_primary["tenant_id"])
        if not t or not t.embd_id or not t.llm_id:
            pytest.skip("mémoires : modèles par défaut absents")
        r = api(editor_auth, "POST", "/memories", json={"name": f"recette-live memory {RUN}", "memory_type": ["raw"], "embd_id": t.embd_id, "llm_id": t.llm_id})
        if code(r) != 0 or not id_of(r):
            pytest.skip(f"mémoire non créable : {r.text[:120]}")
        mid = id_of(r)
        try:
            for who, auth in (("viewer", viewer_auth), ("ws_admin", live_primary["auth"])):
                for label, rr in (("get", api(auth, "GET", f"/memories/{mid}")), ("config", api(auth, "GET", f"/memories/{mid}/config")), ("update", api(auth, "PUT", f"/memories/{mid}", json={"name": "pwned"})), ("delete", api(auth, "DELETE", f"/memories/{mid}"))):
                    assert refused(rr), f"{who} atteint la mémoire d'un autre utilisateur via {label}"
        finally:
            api(editor_auth, "DELETE", f"/memories/{mid}")


# ----------------------------------------------------- cycle de vie d'un compte
@pytest.fixture(scope="module")
def invited(live_panel, live_primary):
    """Compte jetable : invité par le panel, mot de passe posé via le lien d'invitation."""
    email = f"lifecycle-{RUN}@recette.invalid"
    r = live_panel("POST", "/users", json={"email": email, "nickname": f"Lifecycle {RUN}", "org_id": live_primary["org_id"], "org_role": "member", "ws_id": live_primary["ws_id"], "ws_role": "viewer"})
    assert r.status_code == 201, r.text
    body = r.json()
    uid, invite_url = body["user_id"], body.get("invite_url")
    if not invite_url:
        pytest.skip("lien d'invitation non renvoyé (e-mail envoyé ?) : flux non testable ici")
    code_ = parse_qs(urlparse(invite_url).query).get("invite_code", [""])[0]
    assert code_, invite_url
    password = f"Recette-{RUN}-Aa!"
    r = requests.post(API + "/set_initial_password", json={"code": code_, "password": rsa_password(password)}, timeout=30)
    assert r.status_code == 200 and code(r) == 0, r.text
    yield {"uid": uid, "email": email, "password": password, "code": code_}
    # nettoyage : désactivation puis purge (idempotent si déjà fait par les tests)
    live_panel("DELETE", f"/users/{uid}")
    live_panel("DELETE", f"/users/{uid}/purge", params={"confirm": "DELETE"})


class TestAccountLifecycle:
    def test_invite_link_is_single_use_and_login_works(self, invited):
        r, tok = login(invited["email"], invited["password"])
        assert r.status_code == 200 and tok, r.text[:200]
        assert code(api(_live.BearerAuth(tok, bearer=False), "GET", "/users/me")) == 0
        # le code d'invitation est à usage unique
        assert not requests.post(API + "/set_initial_password", json={"code": invited["code"], "password": rsa_password("Autre-2026-Aa!")}, timeout=30).json().get("code") == 0

    def test_password_change_rotates_token_and_session_cookie(self, invited, live_primary):
        r, old_tok = login(invited["email"], invited["password"])
        assert old_tok, r.text[:200]
        cookie_only = requests.Session()
        cookie_only.cookies.update(r.cookies)
        auth_old = _live.BearerAuth(old_tok, live_primary["ws_id"], bearer=False)
        assert code(api(auth_old, "GET", "/users/me")) == 0
        new_password = invited["password"] + "-2"
        r = api(auth_old, "PATCH", "/users/me", json={"password": rsa_password(invited["password"]), "new_password": rsa_password(new_password)})
        assert code(r) == 0, r.text[:200]
        invited["password"] = new_password
        r = api(auth_old, "GET", "/users/me")
        assert r.status_code == 401 or code(r) == 401, "l'ancien jeton survit au changement de mot de passe"
        r = cookie_only.get(API + "/users/me", timeout=30)
        assert r.status_code == 401 or code(r) == 401, "le cookie de session antérieur à la rotation reste valide"
        r, _ = login(invited["email"], invited["password"][:-2])
        assert code(r) != 0, "l'ancien mot de passe fonctionne encore"
        r, new_tok = login(invited["email"], new_password)
        assert new_tok, r.text[:200]

    def test_deactivation_kills_tokens_and_keys_then_restore(self, invited, live_panel, live_primary):
        r, tok = login(invited["email"], invited["password"])
        assert tok, r.text[:200]
        auth = _live.BearerAuth(tok, live_primary["ws_id"], bearer=False)
        r = api(auth, "POST", "/api_keys", json={"name": f"recette-live lifecycle key {RUN}", "permissions": ["dataset.read"]})
        assert code(r) == 0, r.text[:200]
        key = r.json()["data"].get("key") or r.json()["data"].get("api_key") or r.json()["data"].get("token")
        k = bearer(key, live_primary["ws_id"])
        assert code(api(k, "GET", "/datasets")) == 0

        r = live_panel("DELETE", f"/users/{invited['uid']}")
        assert r.status_code == 204, r.text
        assert code(api(auth, "GET", "/users/me")) in (401, 403), "jeton de session d'un compte désactivé accepté"
        assert code(api(k, "GET", "/datasets")) in (401, 403), "clé API d'un compte désactivé acceptée"
        r, tok2 = login(invited["email"], invited["password"])
        assert not tok2 or code(r) != 0, "un compte désactivé peut encore se connecter"

        # Contrat du panel : un compte supprimé individuellement n'est pas
        # restaurable (adhésions effacées à la suppression) — seule la purge suit.
        r = live_panel("POST", f"/archives/users/{invited['uid']}/restore")
        assert r.status_code == 400, r.text
        r, tok3 = login(invited["email"], invited["password"])
        assert not tok3 or code(r) != 0, "un compte désactivé peut encore se connecter"

    def test_purge_removes_user_membership_and_keys(self, invited, live_panel, live_primary):
        from api.db.db_models import DB, ApiKeyScope, User, WsMember
        r = live_panel("DELETE", f"/users/{invited['uid']}")
        assert r.status_code in (204, 409, 400), r.text
        r = live_panel("DELETE", f"/users/{invited['uid']}/purge", params={"confirm": "DELETE"})
        assert r.status_code == 204, r.text
        # Purge RGPD = tombstone : la ligne reste (intégrité des FK) mais toute
        # donnée personnelle est écrasée ; adhésions et clés disparaissent.
        with DB.connection_context():
            u = User.get_or_none(User.id == invited["uid"])
            assert u is not None and invited["email"] not in (u.email or "") and "anonymized" in (u.email or ""), f"PII encore présentes : {getattr(u, 'email', None)}"
            assert str(u.is_active) == "0"
            assert WsMember.select().where(WsMember.user_id == invited["uid"]).count() == 0, "adhésions toujours présentes après purge"
            assert ApiKeyScope.select().where((ApiKeyScope.created_by == invited["uid"]) & (ApiKeyScope.status == "1")).count() == 0, "clé API encore active après purge"
        r, tok = login(invited["email"], invited["password"])
        assert not tok or code(r) != 0
