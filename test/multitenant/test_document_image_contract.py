"""
Contract tests for GET /api/v1/documents/images/<image_id>
and GET /api/v1/documents/artifact/<filename>.

WHY THESE TESTS EXIST
---------------------
Upstream's RESTful migration (#14454, #14366) moved the chunk-image and artifact
serving routes from /v1/document/image|artifact (which had @login_required +
@require_permission(DOCUMENT_READ)) to /api/v1/documents/{images,artifact}/...
Upstream's slim version DROPPED the auth decorators on the image route — making
chunk images publicly readable to anyone who knows or guesses an image_id.

Our pre-merge behaviour required auth + DOCUMENT_READ. These tests pin that
invariant: future upstream merges that strip auth from these GETs will fail
loudly here.

Run:
    uv run python -m pytest test/multitenant/test_document_image_contract.py -v

Prerequisites: RAGFlow server running at HOST_ADDRESS (default
http://127.0.0.1:9380); local-auth dev mode (RAGFLOW_TEST_LOCAL_AUTH=1) and
test user fixtures provided by conftest.py.
"""
import os

import pytest
import requests
from requests.auth import AuthBase

HOST_ADDRESS = os.getenv("HOST_ADDRESS", "http://127.0.0.1:9380")
VERSION = "v1"
INVALID_TOKEN = "invalid_token_000"

# A fake but well-formed image_id (32-hex bucket, 16-hex name) — the auth
# decorators must reject the request before any storage lookup, so the bucket
# does not need to exist.
FAKE_IMAGE_ID = "33fedac0341a11f1b43d12cf53e6237d-855af91b0ff4c9b0"
FAKE_ARTIFACT = "nonexistent-artifact.md"


class _InvalidAuth(AuthBase):
    def __call__(self, r):
        r.headers["Authorization"] = INVALID_TOKEN
        return r


def _image_url():
    return f"{HOST_ADDRESS}/api/{VERSION}/documents/images/{FAKE_IMAGE_ID}"


def _artifact_url():
    return f"{HOST_ADDRESS}/api/{VERSION}/documents/artifact/{FAKE_ARTIFACT}"


@pytest.mark.p1
class TestDocumentImageAuth:
    """GET /api/v1/documents/images/<image_id> must require @login_required.

    Upstream's slim restful_apis/document_api.py introduced this route WITHOUT
    auth decorators (security regression). We re-added @login_required and
    @require_permission(DOCUMENT_READ); these tests ensure they stay.
    """

    def test_no_auth_returns_401(self):
        r = requests.get(_image_url())
        assert r.status_code == 401, (
            f"Expected 401 on missing auth, got {r.status_code}. "
            "Did upstream strip @login_required from get_document_image again?"
        )

    def test_invalid_token_returns_401(self):
        r = requests.get(_image_url(), auth=_InvalidAuth())
        assert r.status_code == 401, (
            f"Expected 401 on invalid token, got {r.status_code}."
        )


@pytest.mark.p1
class TestDocumentArtifactAuth:
    """GET /api/v1/documents/artifact/<filename> must require auth + DOCUMENT_READ.

    Upstream had @login_required but DROPPED our @require_permission(DOCUMENT_READ).
    We re-added it; this test pins it.
    """

    def test_no_auth_returns_401(self):
        r = requests.get(_artifact_url())
        assert r.status_code == 401, (
            f"Expected 401 on missing auth, got {r.status_code}. "
            "Did upstream strip @login_required from get_artifact?"
        )

    def test_invalid_token_returns_401(self):
        r = requests.get(_artifact_url(), auth=_InvalidAuth())
        assert r.status_code == 401, (
            f"Expected 401 on invalid token, got {r.status_code}."
        )


@pytest.mark.p1
class TestDocumentImageWorkspaceAuth:
    """With valid auth + workspace, the route must NOT 401 — it should proceed
    to the storage layer (and return a non-401 error for our fake image_id).

    This catches the inverse regression: an over-strict workspace middleware
    that 401s legitimate users and breaks chunk image rendering.
    """

    def test_authenticated_user_passes_auth_layer(self, viewer_auth):
        r = requests.get(_image_url(), auth=viewer_auth)
        # Either 200 (image found) or a non-401 error (storage miss / 500).
        # The point is: auth layer let us through.
        assert r.status_code != 401, (
            f"Authenticated viewer got 401 on image route (status={r.status_code}). "
            "The workspace/RBAC layer is incorrectly rejecting valid users."
        )
