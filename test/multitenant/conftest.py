"""
Fixtures for multi-tenant / workspace isolation tests.

These tests require a running RAGFlow server at HOST_ADDRESS with our custom
B2B SaaS multi-tenant layer enabled (X-Workspace-Id middleware).

Configuration via env vars (with defaults for local dev):
  HOST_ADDRESS   default http://127.0.0.1:9380
  TEST_EMAIL     test user email  (default: qa@infiniflow.org)
  TEST_PASSWORD  test user password in plaintext  (default: 123)
"""
import os

import pytest
import requests
from requests.auth import AuthBase

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
VERSION = "v1"
# Credentials: either provide a pre-computed auth token + workspace_id directly,
# or provide email/password and the conftest will log in automatically.
TEST_AUTH_TOKEN = os.getenv("TEST_AUTH_TOKEN")       # e.g. "Authorization header value"
TEST_WORKSPACE_ID = os.getenv("TEST_WORKSPACE_ID")   # workspace UUID
TEST_EMAIL = os.getenv("TEST_EMAIL", "qa@infiniflow.org")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "123")
INVALID_TOKEN = "invalid_token_000"


# ---------------------------------------------------------------------------
# Workspace-aware auth helpers
# ---------------------------------------------------------------------------

class WorkspaceAuth(AuthBase):
    """requests auth that injects both Authorization and X-Workspace-Id."""

    def __init__(self, token: str, workspace_id: str):
        self._token = token
        self.workspace_id = workspace_id

    def __call__(self, r):
        r.headers["Authorization"] = self._token
        if self.workspace_id:
            r.headers["X-Workspace-Id"] = self.workspace_id
        return r


class BareAuth(AuthBase):
    """Valid JWT, NO X-Workspace-Id header — used to test missing-workspace rejection."""

    def __init__(self, token: str):
        self._token = token

    def __call__(self, r):
        r.headers["Authorization"] = self._token
        return r


# ---------------------------------------------------------------------------
# Session-level login + workspace resolution
# ---------------------------------------------------------------------------

_SETUP_HELP = """
TEST CREDENTIALS NOT CONFIGURED
--------------------------------
The RAGFlow server uses RSA client-side password encryption, so plaintext
email/password login is not possible from scripts.

Set these two environment variables before running the integration tests:

  export TEST_AUTH_TOKEN="<Authorization header value>"
  export TEST_WORKSPACE_ID="<workspace UUID>"

How to get them (one-time setup, takes 30 seconds):
  1. Log into RAGFlow in your browser (http://localhost:9380)
  2. Open DevTools → Network → any API request
  3. Copy the full "Authorization" header value  → TEST_AUTH_TOKEN
  4. In Console: localStorage.getItem('active_workspace_id')  → TEST_WORKSPACE_ID

Or set them permanently in your shell profile:
  echo 'export TEST_AUTH_TOKEN="..."' >> ~/.zshrc
  echo 'export TEST_WORKSPACE_ID="..."' >> ~/.zshrc
"""


@pytest.fixture(scope="session")
def _credentials():
    if TEST_AUTH_TOKEN and TEST_WORKSPACE_ID:
        return TEST_AUTH_TOKEN, TEST_WORKSPACE_ID
    pytest.exit(_SETUP_HELP, returncode=1)


@pytest.fixture(scope="session")
def ws_auth(_credentials):
    """WorkspaceAuth — valid JWT + correct workspace header."""
    token, workspace_id = _credentials
    return WorkspaceAuth(token, workspace_id)


@pytest.fixture(scope="session")
def bare_auth(_credentials):
    """BareAuth — valid JWT but no X-Workspace-Id (tests missing-workspace rejection)."""
    token, _ = _credentials
    return BareAuth(token)


@pytest.fixture(scope="session")
def workspace_id(_credentials):
    _, ws_id = _credentials
    return ws_id


# ---------------------------------------------------------------------------
# Dataset fixture scoped to the workspace
# ---------------------------------------------------------------------------

def _create_dataset(auth, name="contract-test"):
    res = requests.post(f"{HOST_ADDRESS}/api/{VERSION}/datasets", auth=auth, json={"name": name})
    data = res.json()
    assert data.get("code") == 0, f"Failed to create dataset: {data}"
    return data["data"]["id"]


def _delete_dataset(auth, kb_id):
    requests.delete(f"{HOST_ADDRESS}/api/{VERSION}/datasets", auth=auth, json={"ids": [kb_id]})


@pytest.fixture()
def ws_dataset(ws_auth):
    """Ephemeral dataset in the test workspace — deleted after each test."""
    kb_id = _create_dataset(ws_auth)
    yield kb_id
    _delete_dataset(ws_auth, kb_id)
