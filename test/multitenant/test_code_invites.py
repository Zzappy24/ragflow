"""Code product seat invites — service-level atomicity + route-level RBAC/email.

Service tests (1-5) use org_with_entitlement (real dev DB, fake LiteLLM client,
no HTTP layer). Route tests (6-9) go through the FastAPI TestClient exactly
like test_code_routes_rbac.py, reusing its panel_client/_h fixtures.
"""
import datetime
from urllib.parse import parse_qs, urlparse

import pytest
from test.multitenant.test_code_provisioning import FakeLiteLLM, org_with_entitlement  # noqa: F401
from test.multitenant.test_code_routes_rbac import panel_client, _h  # noqa: F401

pytestmark = pytest.mark.p1


@pytest.fixture(autouse=True)
def _reset_claim_rate_limit():
    """The public claim route's rate limiter is a module-level, in-process
    dict keyed by source IP. TestClient always presents the same fake IP
    ("testclient"), so without a reset, hits accumulate across tests in this
    file (and any other file exercising the same route) and can trip a false
    429 in an unrelated test."""
    from management.server.routers import code as code_router
    code_router._CLAIM_HITS.clear()
    yield
    code_router._CLAIM_HITS.clear()


@pytest.fixture()
def inv_org(org_with_entitlement):  # noqa: F811
    """org_with_entitlement, extended to also purge CodeKeyInvite rows —
    org_with_entitlement's own teardown never touches that table (it didn't
    exist yet when that fixture was written). Must run BEFORE
    org_with_entitlement's teardown deletes the CodeTeam rows the invites
    point at; fixture teardown runs in reverse dependency order, so this
    body (registered after org_with_entitlement is set up) tears down first."""
    yield org_with_entitlement
    from api.db.db_models import DB, CodeTeam, CodeKeyInvite
    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_with_entitlement)]
        if team_ids:
            CodeKeyInvite.delete().where(CodeKeyInvite.code_team_id.in_(team_ids)).execute()


@pytest.fixture()
def rbac_org(org_with_entitlement_and_users):
    """Same wrapper as inv_org, but over the RBAC-ready org+users fixture used
    by the route-level tests."""
    yield org_with_entitlement_and_users
    org_id, _ = org_with_entitlement_and_users
    from api.db.db_models import DB, CodeTeam, CodeKeyInvite
    with DB.connection_context():
        team_ids = [t.id for t in CodeTeam.select().where(CodeTeam.org_id == org_id)]
        if team_ids:
            CodeKeyInvite.delete().where(CodeKeyInvite.code_team_id.in_(team_ids)).execute()


# ---------------------------------------------------------------------------
# Service-level tests
# ---------------------------------------------------------------------------

def test_create_invites_hashes_token_and_dedups(inv_org):
    import hashlib
    from api.db.db_models import DB, CodeKeyInvite
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)

    result = ci.create_invites(code_team_id=team.id,
                               emails=["a@x.com", "b@x.com", "a@x.com"],
                               created_by="tester")
    assert len(result) == 2  # dedup
    assert {r["email"] for r in result} == {"a@x.com", "b@x.com"}

    with DB.connection_context():
        rows = {r.id: r for r in CodeKeyInvite.select().where(CodeKeyInvite.code_team_id == team.id)}
    for item in result:
        row = rows[item["invite_id"]]
        assert row.token_hash == hashlib.sha256(item["claim_token"].encode()).hexdigest()
        # The plaintext token is never persisted anywhere on the row.
        assert item["claim_token"] != row.token_hash
        assert item["claim_token"] not in (row.id, row.email, row.created_by, row.code_team_id)


def test_create_invites_rejects_over_200(inv_org):
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    emails = [f"u{i}@x.com" for i in range(201)]
    with pytest.raises(ValueError):
        ci.create_invites(code_team_id=team.id, emails=emails, created_by="tester")


def test_claim_generates_key_once_atomically(inv_org):
    import concurrent.futures
    from api.db.db_models import DB, CodeKey
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    invite = ci.create_invites(code_team_id=team.id, emails=["race@x.com"],
                               created_by="tester")[0]
    token = invite["claim_token"]

    def attempt(_):
        try:
            return ci.claim(token, client=fake)
        except ci.InviteNotFound as e:
            return e

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(attempt, range(2)))

    winners = [r for r in results if isinstance(r, dict)]
    losers = [r for r in results if isinstance(r, Exception)]
    assert len(winners) == 1 and len(losers) == 1
    assert winners[0]["plain_key"].startswith("sk-")
    assert winners[0]["email"] == "race@x.com"

    with DB.connection_context():
        assert CodeKey.select().where(CodeKey.code_team_id == team.id).count() == 1


def test_claim_expired_or_unknown_raises_notfound(inv_org):
    from api.db.db_models import DB, CodeKeyInvite
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    with pytest.raises(ci.InviteNotFound):
        ci.claim("this-token-does-not-exist")

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    invite = ci.create_invites(code_team_id=team.id, emails=["expired@x.com"],
                               created_by="tester")[0]
    past = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(hours=1)
    with DB.connection_context():
        CodeKeyInvite.update(expires_at=past).where(CodeKeyInvite.id == invite["invite_id"]).execute()

    with pytest.raises(ci.InviteNotFound):
        ci.claim(invite["claim_token"], client=fake)


def test_claim_gateway_down_keeps_token_valid(inv_org):
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    invite = ci.create_invites(code_team_id=team.id, emails=["down@x.com"],
                               created_by="tester")[0]
    token = invite["claim_token"]

    fake.down = True
    with pytest.raises(ci.GatewayDown):
        ci.claim(token, client=fake)

    # rollback: the SAME token still works once the gateway is back up
    fake.down = False
    result = ci.claim(token, client=fake)
    assert result["plain_key"].startswith("sk-")


def test_claim_gateway_down_does_not_orphan_code_key_rows(inv_org):
    """I3: create_code_key always writes the CodeKey row desired-state-first,
    even when generate_key then fails against a down gateway. Without an
    explicit cleanup, every gateway-down retry of the same invite leaves
    behind one more orphan row (no external LiteLLM object, plaintext never
    issued) — repeated retries must not accumulate rows."""
    from api.db.db_models import DB, CodeKey
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    invite = ci.create_invites(code_team_id=team.id, emails=["orphan@x.com"],
                               created_by="tester")[0]
    token = invite["claim_token"]

    fake.down = True
    with pytest.raises(ci.GatewayDown):
        ci.claim(token, client=fake)
    with pytest.raises(ci.GatewayDown):
        ci.claim(token, client=fake)

    fake.down = False
    result = ci.claim(token, client=fake)
    assert result["plain_key"].startswith("sk-")

    with DB.connection_context():
        assert CodeKey.select().where(CodeKey.code_team_id == team.id).count() == 1


def test_claim_rolls_back_reservation_on_unexpected_error(inv_org):
    """C1: create_code_key can raise ValueError for reasons other than a dead
    gateway (org entitlement suspended, team not synced, ...) — that must
    not brick the invite at claimed_key_id="pending" forever, and the public
    route must only ever see this as the generic 404 (never a 500)."""
    from api.db.db_models import DB, CodeEntitlement, CodeKeyInvite
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    invite = ci.create_invites(code_team_id=team.id, emails=["suspended@x.com"],
                               created_by="tester")[0]
    token = invite["claim_token"]

    with DB.connection_context():
        CodeEntitlement.update(status="suspended").where(
            CodeEntitlement.org_id == inv_org).execute()

    with pytest.raises(ci.InviteNotFound):
        ci.claim(token, client=fake)

    with DB.connection_context():
        inv = CodeKeyInvite.get_by_id(invite["invite_id"])
        assert inv.claimed_key_id is None  # rolled back, not stuck at "pending"
        CodeEntitlement.update(status="active").where(
            CodeEntitlement.org_id == inv_org).execute()

    # the SAME link still works once the underlying cause is fixed
    result = ci.claim(token, client=fake)
    assert result["plain_key"].startswith("sk-")


def test_claim_reclaims_stale_pending_sentinel(inv_org):
    """C1 crash recovery: a claim() that reserved the invite (claimed_key_id
    flipped to "pending:<epoch>") and then crashed before completing or
    rolling back leaves the invite recoverable once the sentinel is older
    than STALE_PENDING_SECONDS."""
    import time
    from api.db.db_models import DB, CodeKeyInvite
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    invite = ci.create_invites(code_team_id=team.id, emails=["crash@x.com"],
                               created_by="tester")[0]
    token = invite["claim_token"]

    old_epoch = int(time.time()) - (ci.STALE_PENDING_SECONDS + 10)
    with DB.connection_context():
        CodeKeyInvite.update(claimed_key_id=f"pending:{old_epoch}").where(
            CodeKeyInvite.id == invite["invite_id"]).execute()

    result = ci.claim(token, client=fake)
    assert result["plain_key"].startswith("sk-")


def test_claim_fresh_pending_sentinel_not_reclaimable(inv_org):
    """A recent (not yet stale) "pending:<epoch>" sentinel means another
    claim() is genuinely in flight right now — must stay a 404, not be
    reclaimed."""
    import time
    from api.db.db_models import DB, CodeKeyInvite
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    invite = ci.create_invites(code_team_id=team.id, emails=["fresh@x.com"],
                               created_by="tester")[0]
    token = invite["claim_token"]

    fresh_epoch = int(time.time()) - 10  # well under STALE_PENDING_SECONDS
    with DB.connection_context():
        CodeKeyInvite.update(claimed_key_id=f"pending:{fresh_epoch}").where(
            CodeKeyInvite.id == invite["invite_id"]).execute()

    with pytest.raises(ci.InviteNotFound):
        ci.claim(token, client=fake)


def test_create_invites_rejects_header_injection_email(inv_org):
    """M2: whitespace/control chars inside an address (e.g. an embedded
    newline attempting header injection into the invite email) must be
    rejected, not just a bare missing '@' or '.'."""
    from management.server.services import code_provisioning as cp
    from management.server.services import code_invites as ci

    fake = FakeLiteLLM()
    team = cp.create_code_team(org_id=inv_org, name="t", max_budget=10.0,
                               model_access=[], created_by="tester", client=fake)
    with pytest.raises(ValueError):
        ci.create_invites(code_team_id=team.id, emails=["a@x.com\nBcc: e@vil"],
                          created_by="tester")


# ---------------------------------------------------------------------------
# Route-level tests
# ---------------------------------------------------------------------------

def test_bulk_route_rbac_and_email_sent(panel_client, rbac_org, monkeypatch):  # noqa: F811
    client, fake = panel_client
    org_id, tokens = rbac_org
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()

    # 403 for a plain member (not delegated to this team)
    assert client.post(f"/api/admin/code/teams/{team['id']}/keys/bulk",
                       json={"emails": ["dev1@x.com"]},
                       headers=_h(tokens["plain_member"])).status_code == 403

    sent_calls = []

    async def fake_send_mail(to, subject, body_text):
        sent_calls.append((to, subject, body_text))
        return True

    import management.server.services.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "send_mail", fake_send_mail)

    r = client.post(f"/api/admin/code/teams/{team['id']}/keys/bulk",
                    json={"emails": ["dev1@x.com", "dev2@x.com"]},
                    headers=_h(tokens["org_admin"]))
    assert r.status_code == 201
    items = r.json()
    assert {i["email"] for i in items} == {"dev1@x.com", "dev2@x.com"}
    for item in items:
        assert item["email_sent"] is True
        # email_sent=True -> no reason to surface the token twice in the response
        assert "claim_url" not in item

    assert len(sent_calls) == 2
    # the email body carries the claim link (with its token), which is the
    # only place the plaintext token is ever exposed for this successful path
    for to, subject, body_text in sent_calls:
        assert "token=" in body_text
        assert to in ("dev1@x.com", "dev2@x.com")

    # pending invites are visible via the list route, no token/claim_url leaked
    pending = client.get(f"/api/admin/code/teams/{team['id']}/invites",
                         headers=_h(tokens["org_admin"])).json()
    assert {p["email"] for p in pending} == {"dev1@x.com", "dev2@x.com"}
    assert "token" not in str(pending) and "claim_url" not in str(pending)


def test_rotate_revokes_and_reinvites(panel_client, rbac_org, monkeypatch):  # noqa: F811
    client, fake = panel_client
    org_id, tokens = rbac_org
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    key = client.post(f"/api/admin/code/teams/{team['id']}/keys",
                      json={"label": "dev1@x.com"},
                      headers=_h(tokens["org_admin"])).json()["key"]

    # 403 for a plain member (not delegated) — must not mutate anything
    assert client.post(f"/api/admin/code/keys/{key['id']}/rotate",
                       headers=_h(tokens["plain_member"])).status_code == 403

    async def fake_send_mail(to, subject, body_text):
        return True
    import management.server.services.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "send_mail", fake_send_mail)

    r = client.post(f"/api/admin/code/keys/{key['id']}/rotate", headers=_h(tokens["org_admin"]))
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "dev1@x.com"
    assert body["email_sent"] is True
    assert body["revoked_key_id"] == key["id"]
    assert "claim_url" not in body

    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview", headers=_h(tokens["org_admin"])).json()
    t = next(t for t in ov["teams"] if t["id"] == team["id"])
    revoked = next(k for k in t["keys"] if k["id"] == key["id"])
    assert revoked["status"] == "revoked"

    pending = client.get(f"/api/admin/code/teams/{team['id']}/invites",
                         headers=_h(tokens["org_admin"])).json()
    assert any(p["email"] == "dev1@x.com" for p in pending)


def test_rotate_rejects_non_email_label(panel_client, rbac_org, monkeypatch):  # noqa: F811
    """I2: rotate_key must validate the key's label is email-shaped BEFORE
    revoking anything — a key created via the plain (non-bulk) route has no
    email-format guarantee on its label, so rotating it must fail cleanly
    (422), leave the key active, send no email, and fire no audit."""
    client, fake = panel_client
    org_id, tokens = rbac_org
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()
    key = client.post(f"/api/admin/code/teams/{team['id']}/keys",
                      json={"label": "not-an-email"},
                      headers=_h(tokens["org_admin"])).json()["key"]

    sent_calls = []

    async def fake_send_mail(to, subject, body_text):
        sent_calls.append(to)
        return True
    import management.server.services.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "send_mail", fake_send_mail)

    r = client.post(f"/api/admin/code/keys/{key['id']}/rotate", headers=_h(tokens["org_admin"]))
    assert r.status_code == 422
    assert sent_calls == []

    ov = client.get(f"/api/admin/orgs/{org_id}/code/overview", headers=_h(tokens["org_admin"])).json()
    t = next(t for t in ov["teams"] if t["id"] == team["id"])
    k = next(k for k in t["keys"] if k["id"] == key["id"])
    assert k["status"] == "active"  # untouched

    pending = client.get(f"/api/admin/code/teams/{team['id']}/invites",
                         headers=_h(tokens["org_admin"])).json()
    assert not any(p["email"] == "not-an-email" for p in pending)  # no orphan invite

    from api.db.db_models import DB, AuditLog
    with DB.connection_context():
        rotated = AuditLog.select().where(
            (AuditLog.action == "CODE_KEY_ROTATED") & (AuditLog.resource_id == key["id"])).count()
    assert rotated == 0


def test_public_claim_audit_records_invite_id(panel_client, rbac_org, monkeypatch):  # noqa: F811
    """M3: the CODE_SEAT_CLAIMED audit record's resource_id is the invite_id
    (not None, and not the freshly-created CodeKey's id), so a claim can be
    traced back to the invite that produced it."""
    client, fake = panel_client
    org_id, tokens = rbac_org
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()

    async def fail_send_mail(to, subject, body_text):
        return False  # forces claim_url into the response so we can grab the token
    import management.server.services.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "send_mail", fail_send_mail)

    bulk = client.post(f"/api/admin/code/teams/{team['id']}/keys/bulk",
                       json={"emails": ["audit@x.com"]},
                       headers=_h(tokens["org_admin"])).json()[0]
    token = parse_qs(urlparse(bulk["claim_url"]).query)["token"][0]

    r = client.post("/api/admin/public/code/claim", json={"token": token})
    assert r.status_code == 200

    from api.db.db_models import DB, AuditLog
    with DB.connection_context():
        row = AuditLog.get_or_none((AuditLog.action == "CODE_SEAT_CLAIMED") &
                                   (AuditLog.resource_id == bulk["invite_id"]))
    assert row is not None


def test_public_claim_route_rate_limited(panel_client):  # noqa: F811
    client, _ = panel_client
    statuses = [client.post("/api/admin/public/code/claim",
                            json={"token": "bogus-token"}).status_code
                for _ in range(11)]
    assert statuses[:10] == [404] * 10
    assert statuses[10] == 429


def test_public_claim_rate_limit_keyed_by_forwarded_for(panel_client):  # noqa: F811
    """I1: two different X-Forwarded-For values must get independent rate
    limit buckets — TestClient always presents the same underlying socket
    ("testclient"), so without honoring XFF this would be indistinguishable
    from a single client hammering the route."""
    client, _ = panel_client
    headers_a = {"X-Forwarded-For": "203.0.113.10"}
    headers_b = {"X-Forwarded-For": "203.0.113.20"}

    statuses_a = [client.post("/api/admin/public/code/claim", json={"token": "bogus-a"},
                              headers=headers_a).status_code for _ in range(10)]
    assert statuses_a == [404] * 10

    # IP B has its own independent bucket -- still well under its limit
    r = client.post("/api/admin/public/code/claim", json={"token": "bogus-b"}, headers=headers_b)
    assert r.status_code == 404

    # IP A's 11th hit trips its own bucket
    r = client.post("/api/admin/public/code/claim", json={"token": "bogus-a"}, headers=headers_a)
    assert r.status_code == 429


def test_resend_regenerates_token(panel_client, rbac_org, monkeypatch):  # noqa: F811
    client, fake = panel_client
    org_id, tokens = rbac_org
    team = client.post(f"/api/admin/orgs/{org_id}/code/teams",
                       json={"name": "s", "max_budget": 10.0, "model_access": []},
                       headers=_h(tokens["org_admin"])).json()

    async def fail_send_mail(to, subject, body_text):
        return False  # forces claim_url into the response so we can grab the token
    import management.server.services.mailer as mailer_module
    monkeypatch.setattr(mailer_module, "send_mail", fail_send_mail)

    bulk = client.post(f"/api/admin/code/teams/{team['id']}/keys/bulk",
                       json={"emails": ["resend@x.com"]},
                       headers=_h(tokens["org_admin"])).json()[0]
    invite_id = bulk["invite_id"]
    old_token = parse_qs(urlparse(bulk["claim_url"]).query)["token"][0]

    # 403 for a plain member (not delegated)
    assert client.post(f"/api/admin/code/invites/{invite_id}/resend",
                       headers=_h(tokens["plain_member"])).status_code == 403

    resend = client.post(f"/api/admin/code/invites/{invite_id}/resend",
                         headers=_h(tokens["org_admin"]))
    assert resend.status_code == 200
    new_token = parse_qs(urlparse(resend.json()["claim_url"]).query)["token"][0]
    assert new_token != old_token

    # old token is dead
    assert client.post("/api/admin/public/code/claim",
                       json={"token": old_token}).status_code == 404
    # new token works
    claim = client.post("/api/admin/public/code/claim", json={"token": new_token})
    assert claim.status_code == 200
    assert claim.json()["plain_key"].startswith("sk-")

    # already claimed -> resend now refused
    r3 = client.post(f"/api/admin/code/invites/{invite_id}/resend", headers=_h(tokens["org_admin"]))
    assert r3.status_code == 409
