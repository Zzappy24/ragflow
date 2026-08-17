#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from agent.tools.get_file import GetFile, GetFileParam


class _Canvas:
    def __init__(self, tenant_id="tenant-123"):
        self._tenant_id = tenant_id

    def is_canceled(self):
        return False

    def get_tenant_id(self):
        return self._tenant_id


def _make_file(name="Payload-20260526.csv", file_id="file-1", created_by="tenant-123", parent_id="folder-1", location=None, size=10904371, ftype="csv"):
    return SimpleNamespace(id=file_id, name=name, created_by=created_by, parent_id=parent_id, location=location or name, size=size, type=ftype)


def _tool(param=None, tenant_id="tenant-123"):
    param = param or GetFileParam()
    cpn = GetFile.__new__(GetFile)
    cpn._canvas = _Canvas(tenant_id)
    cpn._id = "get_file"
    cpn._param = param
    return cpn


def test_get_file_success_returns_presigned_url():
    file_record = _make_file()
    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings") as mock_settings:
        mock_file_service.query.return_value = [file_record]
        mock_settings.STORAGE_IMPL.obj_exist.return_value = True
        mock_settings.STORAGE_IMPL.get_presigned_url.return_value = "http://minio.local/signed-url?sig=abc"

        cpn = _tool()
        result = cpn._invoke(name="Payload-20260526.csv")

    assert "http://minio.local/signed-url?sig=abc" in result
    assert "http://minio.local/signed-url?sig=abc" in cpn.output("formalized_content")
    assert "Payload-20260526.csv" in result

    # CRITICAL 1 regression: (bucket, key) must be derived from
    # (f.parent_id, f.location), never a user-downloads/file-id convention.
    mock_settings.STORAGE_IMPL.obj_exist.assert_called_once_with(file_record.parent_id, file_record.location)
    args, kwargs = mock_settings.STORAGE_IMPL.get_presigned_url.call_args
    assert args[0] == file_record.parent_id
    assert args[1] == file_record.location
    assert kwargs.get("endpoint_override") is None


def test_get_file_falls_back_to_file2document_storage_address_when_primary_missing():
    file_record = _make_file()
    with (
        patch("agent.tools.get_file.FileService") as mock_file_service,
        patch("agent.tools.get_file.File2DocumentService") as mock_f2d,
        patch("agent.tools.get_file.settings") as mock_settings,
    ):
        mock_file_service.query.return_value = [file_record]
        mock_settings.STORAGE_IMPL.obj_exist.return_value = False
        mock_f2d.get_storage_address.return_value = ("kb-1", "doc-location.csv")
        mock_settings.STORAGE_IMPL.get_presigned_url.return_value = "http://minio.local/signed-fallback"

        cpn = _tool()
        result = cpn._invoke(name="Payload-20260526.csv")

    mock_f2d.get_storage_address.assert_called_once_with(file_id=file_record.id)
    args, kwargs = mock_settings.STORAGE_IMPL.get_presigned_url.call_args
    assert args[0] == "kb-1"
    assert args[1] == "doc-location.csv"
    assert "http://minio.local/signed-fallback" in result


def test_get_file_not_found_message():
    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings"):
        mock_file_service.query.return_value = []

        cpn = _tool()
        result = cpn._invoke(name="missing.csv")

    assert "not found" in result.lower()
    assert "missing.csv" in result


def test_get_file_homonyms_lists_all_candidates():
    f1 = _make_file(file_id="file-1", parent_id="folder-a")
    f2 = _make_file(file_id="file-2", parent_id="folder-b")
    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings"):
        mock_file_service.query.return_value = [f1, f2]

        cpn = _tool()
        result = cpn._invoke(name="Payload-20260526.csv")

    assert "file-1" in result
    assert "file-2" in result
    assert "folder-a" in result
    assert "folder-b" in result


def test_get_file_url_expires_s_passed_to_presign():
    file_record = _make_file()
    param = GetFileParam()
    param.url_expires_s = 120

    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings") as mock_settings:
        mock_file_service.query.return_value = [file_record]
        mock_settings.STORAGE_IMPL.get_presigned_url.return_value = "http://minio.local/signed"

        cpn = _tool(param=param)
        cpn._invoke(name="Payload-20260526.csv")

    args, _ = mock_settings.STORAGE_IMPL.get_presigned_url.call_args
    assert args[2] == timedelta(seconds=120)


def test_get_file_sandbox_presign_endpoint_threaded_through_storage_impl(monkeypatch):
    # IMPORTANT 2: the sandbox endpoint must be threaded through
    # STORAGE_IMPL.get_presigned_url's own endpoint_override kwarg, so the
    # @use_default_bucket/@use_prefix_path decorators in minio_conn.py still
    # apply the bucket/key remapping — not bypassed via a raw Minio() client
    # built directly in the tool.
    monkeypatch.setenv("SANDBOX_PRESIGN_ENDPOINT", "sandbox-minio:9000")
    file_record = _make_file()

    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings") as mock_settings:
        mock_file_service.query.return_value = [file_record]
        mock_settings.STORAGE_IMPL.obj_exist.return_value = True
        mock_settings.STORAGE_IMPL.get_presigned_url.return_value = "http://sandbox-minio/signed"

        cpn = _tool()
        result = cpn._invoke(name="Payload-20260526.csv")

    args, kwargs = mock_settings.STORAGE_IMPL.get_presigned_url.call_args
    assert args[0] == file_record.parent_id
    assert args[1] == file_record.location
    assert kwargs.get("endpoint_override") == "sandbox-minio:9000"
    assert "http://sandbox-minio/signed" in result


def test_get_file_no_sandbox_presign_endpoint_passes_none_override(monkeypatch):
    monkeypatch.delenv("SANDBOX_PRESIGN_ENDPOINT", raising=False)
    file_record = _make_file()

    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings") as mock_settings:
        mock_file_service.query.return_value = [file_record]
        mock_settings.STORAGE_IMPL.obj_exist.return_value = True
        mock_settings.STORAGE_IMPL.get_presigned_url.return_value = "http://minio.local/signed"

        cpn = _tool()
        cpn._invoke(name="Payload-20260526.csv")

    _, kwargs = mock_settings.STORAGE_IMPL.get_presigned_url.call_args
    assert not kwargs.get("endpoint_override")


def test_get_file_lookup_scoped_to_canvas_tenant():
    file_record = _make_file()
    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings") as mock_settings:
        mock_file_service.query.return_value = [file_record]
        mock_settings.STORAGE_IMPL.get_presigned_url.return_value = "http://minio.local/signed"

        cpn = _tool(tenant_id="tenant-xyz")
        cpn._invoke(name="Payload-20260526.csv")

    _, kwargs = mock_file_service.query.call_args
    assert kwargs.get("tenant_id") == "tenant-xyz"
    assert kwargs.get("name") == "Payload-20260526.csv"


def test_get_file_missing_tenant_id_fails_closed_without_querying():
    # IMPORTANT 3: FileService.query silently drops None-valued kwargs, so an
    # unresolved tenant_id would turn this into a tenant-unscoped lookup —
    # a cross-tenant exfiltration risk. Must fail closed before querying.
    with patch("agent.tools.get_file.FileService") as mock_file_service, patch("agent.tools.get_file.settings"):
        cpn = _tool(tenant_id=None)
        result = cpn._invoke(name="Payload-20260526.csv")

    assert "workspace" in result.lower()
    mock_file_service.query.assert_not_called()
