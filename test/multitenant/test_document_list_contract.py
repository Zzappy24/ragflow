"""
Contract tests for POST /v1/document/list — the legacy web endpoint used by the
React frontend.

WHY THESE TESTS EXIST
---------------------
Upstream regularly renames API fields (chunk_num→chunk_count, parser_id→chunk_method,
run as integer→string). Each rename silently breaks the frontend until we notice.
These tests catch field-level regressions within seconds of starting the server.

Run:
    cd test/multitenant
    uv run python -m pytest test_document_list_contract.py -v
    # or from repo root:
    uv run python -m pytest test/multitenant/ -v

Prerequisites: RAGFlow server running at HOST_ADDRESS (default: http://127.0.0.1:9380),
test user exists in a workspace (TEST_EMAIL / TEST_PASSWORD env vars).
"""
import os
from pathlib import Path

import pytest
import requests
from requests.auth import AuthBase

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
VERSION = "v1"
INVALID_TOKEN = "invalid_token_000"

DOCUMENT_LIST_URL = f"{HOST_ADDRESS}/{VERSION}/document/list"
VALID_RUN_VALUES = {"UNSTART", "RUNNING", "CANCEL", "DONE", "FAIL"}

REQUIRED_DOC_FIELDS = {
    "chunk_count",   # renamed from chunk_num in upstream #14232
    "chunk_method",  # renamed from parser_id in upstream #14232
    "run",           # must be string enum, not raw "0"/"1"/...
    "id",
    "name",
    "progress",
}


class _InvalidAuth(AuthBase):
    def __call__(self, r):
        r.headers["Authorization"] = INVALID_TOKEN
        return r


def list_docs_web(auth, kb_id, payload=None):
    """Call POST /v1/document/list — the endpoint the React frontend uses."""
    return requests.post(
        DOCUMENT_LIST_URL,
        auth=auth,
        params={"id": kb_id},
        json=payload or {},
    ).json()


def _upload_txt(auth, kb_id, tmp_path, filename="contract_test.txt"):
    """Upload a small text file via the RESTful upload endpoint."""
    fp = tmp_path / filename
    fp.write_text("contract test content")
    url = f"{HOST_ADDRESS}/api/{VERSION}/datasets/{kb_id}/documents"
    with open(fp, "rb") as f:
        res = requests.post(url, auth=auth, files={"file": (fp.name, f)})
    assert res.json().get("code") == 0, f"Upload failed: {res.json()}"


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

@pytest.mark.p1
class TestDocumentListAuth:
    """Endpoint must reject unauthenticated and badly-authenticated requests."""

    def test_no_auth_returns_401(self, workspace_id):
        res = requests.post(
            DOCUMENT_LIST_URL,
            params={"id": "any-id"},
            json={},
        ).json()
        assert res["code"] == 401, f"Expected 401, got: {res}"

    def test_invalid_token_returns_401(self, workspace_id):
        res = list_docs_web(_InvalidAuth(), "any-id")
        assert res["code"] == 401, f"Expected 401, got: {res}"


# ---------------------------------------------------------------------------
# Workspace isolation tests
# ---------------------------------------------------------------------------

@pytest.mark.p1
class TestWorkspaceIsolation:
    """X-Workspace-Id must be enforced — missing or invalid → 401."""

    def test_missing_workspace_header_returns_401(self, bare_auth, ws_dataset):
        """Without X-Workspace-Id, active_tenant_id() raises 401."""
        res = list_docs_web(bare_auth, ws_dataset)
        assert res["code"] == 401, (
            f"Expected 401 when X-Workspace-Id is missing, got {res['code']}. "
            "Check that active_tenant_id() is called in the /list handler."
        )

    def test_nonexistent_workspace_id_returns_401(self, bare_auth, ws_dataset):
        """A fabricated workspace ID the user doesn't belong to → 401."""
        class _FakeWsAuth(AuthBase):
            def __init__(self, inner):
                self._inner = inner
            def __call__(self, r):
                r = self._inner(r)
                r.headers["X-Workspace-Id"] = "00000000000000000000000000000000"
                return r

        res = list_docs_web(_FakeWsAuth(bare_auth), ws_dataset)
        assert res["code"] == 401, (
            f"Expected 401 for fake workspace ID, got {res['code']}. "
            "Cross-workspace data leak possible."
        )

    def test_valid_workspace_returns_200(self, ws_auth, ws_dataset):
        """Sanity: correct workspace + auth → 200."""
        res = list_docs_web(ws_auth, ws_dataset)
        assert res["code"] == 0, f"Expected 200 with valid workspace, got: {res}"
        assert "docs" in res.get("data", {}), f"Missing 'docs' in response: {res}"


# ---------------------------------------------------------------------------
# API contract tests
# ---------------------------------------------------------------------------

@pytest.mark.p1
class TestDocumentListContract:
    """
    Verify the response shape matches what the React frontend expects.

    Each test targets a specific upstream regression pattern.
    If a test fails after an upstream merge, check the CLAUDE.md merge table.
    """

    @pytest.fixture()
    def dataset_with_doc(self, ws_auth, ws_dataset, tmp_path):
        _upload_txt(ws_auth, ws_dataset, tmp_path)
        return ws_dataset

    def test_response_envelope(self, ws_auth, ws_dataset):
        """Response must have code=0, data.docs list, data.total int."""
        res = list_docs_web(ws_auth, ws_dataset)
        assert res.get("code") == 0, res
        assert isinstance(res.get("data", {}).get("docs"), list), res
        assert isinstance(res.get("data", {}).get("total"), int), res

    def test_chunk_count_present(self, ws_auth, dataset_with_doc):
        """chunk_count must exist (renamed from chunk_num in upstream #14232)."""
        res = list_docs_web(ws_auth, dataset_with_doc)
        assert res["code"] == 0, res
        docs = res["data"]["docs"]
        assert docs, "No documents returned — cannot verify contract."
        for doc in docs:
            assert "chunk_count" in doc, (
                f"'chunk_count' missing. Upstream may have reverted field rename. "
                f"Keys: {sorted(doc.keys())}"
            )

    def test_chunk_method_present(self, ws_auth, dataset_with_doc):
        """chunk_method must exist (renamed from parser_id in upstream #14232)."""
        res = list_docs_web(ws_auth, dataset_with_doc)
        assert res["code"] == 0, res
        for doc in res["data"]["docs"]:
            assert "chunk_method" in doc, (
                f"'chunk_method' missing. Upstream may have reverted parser_id rename. "
                f"Keys: {sorted(doc.keys())}"
            )

    def test_run_is_string_enum(self, ws_auth, dataset_with_doc):
        """
        'run' must be a string status like 'UNSTART', not a raw integer '0'.
        Upstream commit 939933649 changed the frontend enum to string values.
        Our /list endpoint must map DB integers to strings.
        """
        res = list_docs_web(ws_auth, dataset_with_doc)
        assert res["code"] == 0, res
        for doc in res["data"]["docs"]:
            assert "run" in doc, f"'run' field missing. Keys: {sorted(doc.keys())}"
            assert doc["run"] in VALID_RUN_VALUES, (
                f"'run' = {doc['run']!r} is not a valid string status. "
                f"Expected one of {VALID_RUN_VALUES}. "
                f"Backend may be returning raw DB integer values."
            )

    def test_required_fields_all_present(self, ws_auth, dataset_with_doc):
        """All fields the React frontend reads must be present in every doc."""
        res = list_docs_web(ws_auth, dataset_with_doc)
        assert res["code"] == 0, res
        for doc in res["data"]["docs"]:
            missing = REQUIRED_DOC_FIELDS - set(doc.keys())
            assert not missing, (
                f"Required fields missing from doc response: {missing}. "
                f"Present keys: {sorted(doc.keys())}"
            )
