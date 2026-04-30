"""
Pin the *security invariant* of bridge tests we env-skip.

WHY THIS FILE EXISTS
--------------------
A handful of tests in `test/multitenant_http_api/_ENV_SKIPS` are skipped
because our wire shape differs from upstream's expectation (e.g. body code:0
vs code:401 on auth failure, "You don't own the dataset" vs "Can't find this
chunk", code:102 vs 405 on routing edge cases). The tests don't fail because
our code is wrong — they fail because their assertion is upstream-shape-
specific. Skipping protects CI green; it does NOT protect against someone
later weakening the underlying behaviour.

This file pins the **invariant we actually care about**: the request was
rejected. If a future change makes any of these unexpectedly succeed (200
+ data leak), THIS test fails loudly even though the upstream test stays
skipped.

Run:
    uv run python -m pytest test/multitenant/test_skip_invariants.py -v
"""
from __future__ import annotations

import os

import pytest
import requests
from requests.auth import AuthBase

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
VERSION = "v1"
INVALID_ID_32 = "0" * 32


def _denied(r) -> bool:
    """A request is "denied" if HTTP status is 4xx/5xx OR body code is non-zero.

    The wire shape varies across our handlers (some return HTTP 401, others
    return HTTP 200 + JSON code:102); both forms reject the request. The
    only thing that MUST NEVER happen here is HTTP 200 + body code:0, which
    would mean the request succeeded.
    """
    if r.status_code >= 400:
        return True
    try:
        return r.json().get("code", 0) != 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pinned invariants
# ---------------------------------------------------------------------------

@pytest.mark.p1
class TestSkippedRejectionInvariants:
    """Even though the upstream-bridge tests for these endpoints are env-skipped
    (their assertion is shape-specific), the *security* invariant must hold:
    every malformed/unauthenticated/cross-workspace probe is REJECTED."""

    def test_chunks_invalid_auth_rejected(self, ws_dataset):
        """`POST /api/v1/datasets/<id>/documents/<doc>/chunks` with no auth
        must be rejected. Bridge's test_add_chunk[None-401] is skipped because
        upstream expects body `code:401` and we return body `code:0` + HTTP 401.
        Both are rejection — but a regression that LETS the call through (200
        + chunk created) would silently succeed."""
        r = requests.post(
            f"{HOST_ADDRESS}/api/{VERSION}/datasets/{INVALID_ID_32}/documents/{INVALID_ID_32}/chunks",
            json={"content": "should never land"},
        )
        assert _denied(r), (
            "Chunks add WITHOUT auth was accepted — our @login_required is "
            f"broken. status={r.status_code} body={r.json()}"
        )

    def test_chunks_invalid_auth_with_bad_token_rejected(self, ws_dataset):
        """Same probe with a bogus Bearer token — must still be rejected."""
        class _Bad(AuthBase):
            def __call__(self, r):
                r.headers["Authorization"] = "Bearer not_a_real_token"
                return r

        r = requests.post(
            f"{HOST_ADDRESS}/api/{VERSION}/datasets/{INVALID_ID_32}/documents/{INVALID_ID_32}/chunks",
            json={"content": "should never land"},
            auth=_Bad(),
        )
        assert _denied(r), (
            "Chunks add with bogus token was accepted — _load_user is "
            f"too permissive. status={r.status_code} body={r.json()}"
        )

    def test_list_documents_empty_dataset_id_rejected(self, ws_auth):
        """`GET /api/v1/datasets//documents` (empty dataset_id) must be
        rejected. The bridge skip exists because upstream expects body code:100
        / 405 message and we return code:102 / "lacks permission". The
        important guarantee: we never return 200 + actual document data for
        an empty/malformed dataset_id."""
        r = requests.get(
            f"{HOST_ADDRESS}/api/{VERSION}/datasets//documents",
            auth=ws_auth,
        )
        assert _denied(r), (
            "Empty dataset_id list_documents was accepted — routing is "
            f"leaking data. status={r.status_code} body={r.json()}"
        )
        # Belt-and-suspenders: even if denied, body must not contain a docs list.
        try:
            data = r.json().get("data")
            if isinstance(data, dict):
                assert not data.get("docs"), (
                    f"Body carried a docs list despite denial: {data}"
                )
        except Exception:
            pass  # non-JSON body is fine — denial via HTTP status

    def test_update_chunk_invalid_dataset_rejected(self, ws_auth):
        """`PATCH /api/v1/datasets/<bad>/documents/<bad>/chunks/<bad>` must be
        rejected. Bridge skip exists because upstream's Infinity-specific
        message is "Can't find this chunk" and we return "You don't own the
        dataset {id}" (the ownership check runs first — better security
        because it doesn't leak chunk-existence info to non-owners). The
        invariant: an invalid dataset never produces a successful chunk
        update."""
        r = requests.patch(
            f"{HOST_ADDRESS}/api/{VERSION}/datasets/{INVALID_ID_32}/documents/{INVALID_ID_32}/chunks/fake_chunk_id",
            json={"content": "x"},
            auth=ws_auth,
        )
        assert _denied(r), (
            "update_chunk with invalid dataset_id was accepted — workspace "
            f"isolation is broken. status={r.status_code} body={r.json()}"
        )
        # Extra: make sure we don't leak the chunk's existence in the response.
        body_text = str(r.json())
        # Not exhaustive, but a "chunk found" leak would typically include
        # fields like `chunk_id` or `content`. The ownership-first pattern
        # rejects with a message about the dataset, never about chunks.
        assert "content_with_weight" not in body_text, (
            f"Response leaked chunk content despite denial: {body_text[:200]}"
        )
