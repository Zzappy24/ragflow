"""
End-to-end smoke test: upload → parse → verify chunks.

Catches regressions that field-level contract tests miss:
  - Parsing pipeline broken (task executor down, wrong tenant, etc.)
  - chunk_count stays 0 after a successful parse
  - run status never transitions to DONE

Requires: server + task executor both running.
Run:
    TEST_AUTH_TOKEN=... TEST_WORKSPACE_ID=... uv run python -m pytest test/multitenant/test_e2e_smoke.py -v -s
"""
import os
import time

import pytest
import requests

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
VERSION = "v1"

PARSE_TIMEOUT = int(os.getenv("PARSE_TIMEOUT", "90"))   # seconds
POLL_INTERVAL = 3


def _list_docs(auth, kb_id):
    return requests.get(
        f"{HOST_ADDRESS}/api/{VERSION}/datasets/{kb_id}/documents",
        auth=auth,
    ).json()


def _trigger_parse(auth, kb_id, doc_id):
    res = requests.post(
        f"{HOST_ADDRESS}/api/{VERSION}/datasets/{kb_id}/documents/parse",
        auth=auth,
        json={"document_ids": [doc_id]},
    ).json()
    assert res.get("code") == 0, f"Failed to trigger parse: {res}"


def _wait_for_done(auth, kb_id, doc_id, timeout=PARSE_TIMEOUT):
    """Poll until the document run status is DONE or FAIL, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = _list_docs(auth, kb_id)
        assert res.get("code") == 0, f"list_docs failed during poll: {res}"
        docs = {d["id"]: d for d in res["data"]["docs"]}
        doc = docs.get(doc_id)
        if doc:
            status = doc.get("run")
            if status == "DONE":
                return doc
            if status == "FAIL":
                pytest.fail(
                    f"Document parsing FAILED. progress_msg: {doc.get('progress_msg', '')}"
                )
        time.sleep(POLL_INTERVAL)
    pytest.fail(
        f"Document did not finish parsing within {timeout}s. "
        f"Last status: {doc.get('run') if doc else 'unknown'}. "
        f"Is the task executor running?"
    )


@pytest.mark.p1
class TestE2EDocumentPipeline:
    """
    Full pipeline: upload a small text file, parse it, verify chunks appear.
    If this fails after a merge, the parsing pipeline or chunk mapping is broken.
    """

    def test_upload_parse_chunks(self, ws_auth, ws_dataset, tmp_path):
        kb_id = ws_dataset

        # 1. Upload a small text document
        fp = tmp_path / "smoke_test.txt"
        fp.write_text(
            "RAGFlow smoke test document.\n"
            "This file is used to verify the end-to-end parsing pipeline.\n"
            "It should produce at least one chunk after parsing.\n" * 10
        )
        url = f"{HOST_ADDRESS}/api/{VERSION}/datasets/{kb_id}/documents"
        with open(fp, "rb") as f:
            upload_res = requests.post(url, auth=ws_auth, files={"file": (fp.name, f)}).json()
        assert upload_res.get("code") == 0, f"Upload failed: {upload_res}"
        doc_id = upload_res["data"][0]["id"]

        # 2. Verify the document appears in list with run=UNSTART
        list_res = _list_docs(ws_auth, kb_id)
        assert list_res["code"] == 0, list_res
        docs = {d["id"]: d for d in list_res["data"]["docs"]}
        assert doc_id in docs, f"Uploaded doc {doc_id} not found in list"
        assert docs[doc_id]["run"] in {"UNSTART", "RUNNING"}, (
            f"Unexpected initial run status: {docs[doc_id]['run']}"
        )

        # 3. Trigger parsing
        _trigger_parse(ws_auth, kb_id, doc_id)

        # 4. Poll until DONE
        done_doc = _wait_for_done(ws_auth, kb_id, doc_id)

        # 5. Verify chunk_count > 0
        assert done_doc.get("chunk_count", 0) > 0, (
            f"Parsing completed but chunk_count=0. "
            f"progress_msg: {done_doc.get('progress_msg', '')}. "
            f"This likely means the chunk mapping or task executor is broken."
        )

        # 6. Verify all contract fields still present after parse
        required = {"chunk_count", "chunk_method", "run", "id", "name", "progress"}
        missing = required - set(done_doc.keys())
        assert not missing, f"Fields missing after parse: {missing}"
