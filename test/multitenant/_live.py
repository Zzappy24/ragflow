"""CUSTOM B2B SaaS — outils partagés des tests LIVE (serveur + panel + moteurs).

Importé par les modules ``test_live_*`` via ``sys.path`` (dossier courant).
Aucun test ici. Les fixtures partagées vivent dans ``conftest.py``
(``live_panel``, ``live_primary``, ``live_second``, ``live_content*``).
"""
import base64
import io
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest
import requests

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
API = f"{HOST_ADDRESS}/api/v1"
MANAGEMENT_HOST = os.getenv("MANAGEMENT_HOST", "http://127.0.0.1:9381")
PANEL = f"{MANAGEMENT_HOST}/api/admin"
CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")


def reachable(url: str) -> bool:
    p = urlparse(url)
    try:
        with socket.create_connection((p.hostname or "127.0.0.1", p.port or 80), timeout=1.0):
            return True
    except OSError:
        return False


def live_markers():
    """Marqueurs de module communs : opt-in explicite + serveur et panel joignables."""
    return [
        pytest.mark.skipif(os.getenv("RAGFLOW_TEST_LOCAL_AUTH") != "1", reason="tests live : RAGFLOW_TEST_LOCAL_AUTH=1 requis"),
        pytest.mark.skipif(not reachable(HOST_ADDRESS), reason=f"API injoignable : {HOST_ADDRESS}"),
        pytest.mark.skipif(not reachable(MANAGEMENT_HOST), reason=f"panel injoignable : {MANAGEMENT_HOST}"),
    ]


# --------------------------------------------------------------------- HTTP
def code(r):
    try:
        return r.json().get("code", r.status_code)
    except Exception:
        return r.status_code


def refused(r) -> bool:
    return code(r) not in (0, 200)


def id_of(r):
    """Identifiant renvoyé par une route de création, quelle que soit sa forme."""
    data = r.json().get("data")
    if isinstance(data, dict):
        return data.get("id") or next((v for k, v in data.items() if k.endswith("_id") and isinstance(v, str)), None)
    if isinstance(data, list) and data:
        return data[0].get("id") if isinstance(data[0], dict) else data[0]
    return data if isinstance(data, str) else None


def api(auth, method, path, **kw):
    return requests.request(method, API + path, auth=auth, timeout=180, **kw)


class BearerAuth(requests.auth.AuthBase):
    def __init__(self, token: str, ws_id: str | None = None, bearer: bool = True):
        self.token, self.ws_id, self.bearer = token, ws_id, bearer

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self.token}" if self.bearer else self.token
        if self.ws_id:
            r.headers["X-Workspace-Id"] = self.ws_id
        return r


def beta_auth(beta: str):
    return BearerAuth(beta)


def bearer(token: str, ws_id: str | None = None):
    return BearerAuth(token, ws_id)


def rsa_password(password: str) -> str:
    """Ce que le navigateur envoie : RSA(Base64(mot de passe)) avec conf/public.pem."""
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA
    pub = RSA.import_key((REPO / "conf" / "public.pem").read_text())
    b64 = base64.b64encode(password.encode()).decode()
    return base64.b64encode(PKCS1_v1_5.new(pub).encrypt(b64.encode())).decode()


def login(email: str, password: str):
    """Retourne (response, jeton Authorization ou None). Chaque login fait tourner l'access_token."""
    r = requests.post(API + "/auth/login", json={"email": email, "password": rsa_password(password)}, timeout=30)
    return r, r.headers.get("Authorization")


# -------------------------------------------------------------------- panel
class Panel:
    """Session panel opaque ouverte en base pour le premier superuser."""

    def __init__(self, user_id: str | None = None):
        from api.db.db_models import DB, User
        from management.server.auth.sessions import open_session
        if user_id is None:
            with DB.connection_context():
                su = User.select().where(User.is_superuser == True).first()  # noqa: E712
            if su is None:
                pytest.skip("aucun superuser en base pour piloter le panel")
            user_id = su.id
        self.user_id = user_id
        self.token = open_session(user_id)

    def __call__(self, method, path, **kw):
        return requests.request(method, PANEL + path, headers={"Authorization": f"Bearer {self.token}"}, timeout=120, **kw)


# ------------------------------------------------------------------ contenu
def wait_parsed(auth, kb, doc, timeout=240) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = api(auth, "GET", f"/datasets/{kb}/documents", params={"id": doc})
        docs = ((r.json().get("data") or {}).get("docs") or []) if r.ok else []
        if docs:
            d = docs[0]
            if d.get("run") == "3" or float(d.get("progress") or 0) >= 1:
                return True
            if d.get("run") == "4" or float(d.get("progress") or 0) < 0:
                pytest.skip(f"parse en échec (modèle d'embedding absent ?) : {str(d.get('progress_msg', ''))[-200:]}")
        time.sleep(3)
    pytest.skip("parse trop long (>240 s) : executor absent ?")


def setup_content(auth, tag: str, secret: str) -> dict:
    """Base + document parsé + chat (le chat exige une base parsée)."""
    run = int(time.time()) % 100000
    r = api(auth, "POST", "/datasets", json={"name": f"recette-live {tag} {run}"})
    assert code(r) == 0, r.text
    kb = r.json()["data"]["id"]
    files = {"file": (f"note-{tag}.txt", io.BytesIO(f"Le mot de passe du coffre {tag} est {secret}. Le contrat expire en 2027.".encode()), "text/plain")}
    r = api(auth, "POST", f"/datasets/{kb}/documents", files=files)
    assert code(r) == 0, r.text
    doc = r.json()["data"][0]["id"]
    api(auth, "POST", f"/datasets/{kb}/documents/parse", json={"document_ids": [doc]})
    wait_parsed(auth, kb, doc)
    r = api(auth, "POST", "/chats", json={"name": f"recette-live chat {tag} {run}", "dataset_ids": [kb]})
    chat = r.json()["data"]["id"] if code(r) == 0 else None
    return {"kb": kb, "doc": doc, "chat": chat, "secret": secret}


def tenant_of_workspace(ws_id: str):
    from api.db.db_models import DB, Workspace
    with DB.connection_context():
        ws = Workspace.get_or_none(Workspace.id == ws_id)
    assert ws is not None, f"workspace {ws_id} introuvable"
    return ws


def copy_models(src_tenant: str, dst_tenant: str) -> dict:
    """Copie les modèles configurés (tenant_llm) et renvoie les défauts source."""
    from api.db.db_models import DB, Tenant, TenantLLM
    from management.server.services.sync_tenant_model_tables import sync_tenant_llm_to_new_tables
    with DB.connection_context():
        rows = list(TenantLLM.select().where(TenantLLM.tenant_id == src_tenant).dicts())
        src = Tenant.get_or_none(Tenant.id == src_tenant)
        for row in rows:
            row = {k: v for k, v in row.items() if k not in ("id", "create_time", "create_date", "update_time", "update_date")}
            row["tenant_id"] = dst_tenant
            TenantLLM.insert(**row).on_conflict_ignore().execute()
    for f in {r["llm_factory"] for r in rows}:
        sync_tenant_llm_to_new_tables(dst_tenant, f)
    return {"llm_id": src.llm_id or "", "embd_id": src.embd_id or ""} if src else {}


def make_disposable_workspace(panel, primary: dict, name: str) -> dict:
    """Workspace jetable dans l'org du workspace de recette, CI_EMAIL ws_admin, modèles copiés."""
    r = panel("POST", f"/orgs/{primary['org_id']}/workspaces", json={"name": name})
    assert r.status_code == 201, r.text
    ws = r.json()
    r = panel("POST", f"/workspaces/{ws['id']}/members", json={"email": CI_EMAIL, "role": "ws_admin"})
    assert r.status_code in (201, 409, 400), r.text
    defaults = {k: v for k, v in copy_models(primary["tenant_id"], ws["tenant_id"]).items() if v}
    if defaults:
        r = panel("PUT", f"/workspaces/{ws['id']}/models/defaults", json=defaults)
        assert r.status_code == 200, r.text
    auth = type(primary["auth"])(primary["auth"]._token, ws["id"])
    return {"auth": auth, "ws_id": ws["id"], "tenant_id": ws["tenant_id"], "org_id": primary["org_id"]}


def purge_workspace(panel, org_id: str, ws_id: str):
    panel("DELETE", f"/orgs/{org_id}/workspaces/{ws_id}")
    return panel("DELETE", f"/orgs/{org_id}/workspaces/{ws_id}/purge", params={"confirm": "DELETE"})
