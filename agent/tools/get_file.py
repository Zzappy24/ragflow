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
import logging
import os
from abc import ABC
from datetime import timedelta

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from api.db import FileType
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from common import settings


def _human_size(num_bytes) -> str:
    """Render a byte count as a short human-readable string, e.g. '10.4 MB'."""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class GetFileParam(ToolParamBase):
    """
    Define the GetFile component parameters.
    """

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "get_file",
            "description": "Returns a short-lived internal URL for a file stored in the workspace Files. Use it to hand data files (CSV, Excel, ...) to code_exec recipes: call get_file first, then pass the returned url to the code.",
            "parameters": {
                "name": {
                    "type": "string",
                    "description": "Exact file name as uploaded in Files (e.g. 'Payload-20260526.csv')",
                    "default": "",
                    "required": True,
                }
            },
        }
        super().__init__()
        self.url_expires_s = 900

    def check(self):
        self.check_positive_integer(self.url_expires_s, "[GetFile] URL expiration seconds")
        if not (60 <= self.url_expires_s <= 3600):
            raise ValueError("[GetFile] URL expiration seconds {} not supported, should be between 60 and 3600".format(self.url_expires_s))

    def get_input_form(self) -> dict[str, dict]:
        return {
            "name": {
                "name": "File name",
                "type": "line",
            }
        }


class GetFile(ToolBase, ABC):
    component_name = "GetFile"

    def _invoke(self, **kwargs):
        if self.check_if_canceled("GetFile processing"):
            return

        name = kwargs.get("name")
        if not name:
            msg = "get_file: a file `name` is required."
            self.set_output("formalized_content", msg)
            return msg

        tenant_id = self._canvas.get_tenant_id()
        if not tenant_id:
            msg = "get_file: could not resolve the workspace for this file lookup."
            self.set_output("formalized_content", msg)
            return msg

        try:
            files = list(FileService.query(name=name, tenant_id=tenant_id))
        except Exception:
            logging.exception(f"get_file: lookup failed for '{name}'")
            msg = f"Storage unavailable: could not look up file '{name}'."
            self.set_output("formalized_content", msg)
            return msg

        files = [f for f in files if getattr(f, "type", None) != FileType.FOLDER.value]

        if not files:
            msg = f"File '{name}' not found in workspace Files."
            self.set_output("formalized_content", msg)
            return msg

        if len(files) > 1:
            candidates = "\n".join(f"- '{f.name}' in folder '{f.parent_id}' (id={f.id})" for f in files)
            msg = f"Multiple files named '{name}' were found, please disambiguate:\n{candidates}"
            self.set_output("formalized_content", msg)
            return msg

        f = files[0]

        try:
            url = self._presigned_url(f)
        except Exception:
            logging.exception(f"get_file: failed to presign URL for '{name}'")
            url = None

        if not url:
            msg = f"Storage unavailable: could not generate a URL for file '{name}'."
            self.set_output("formalized_content", msg)
            return msg

        minutes = max(1, self._param.url_expires_s // 60)
        msg = f"File '{f.name}' ({_human_size(f.size)}) available at: {url} (valid {minutes} min)"
        self.set_output("formalized_content", msg)
        return msg

    def _presigned_url(self, f) -> str:
        # Files-manager files are stored under bucket=parent_id, key=location
        # (api/apps/services/file_api_service.py::upload_file, ~line 88:
        # `settings.STORAGE_IMPL.put(last_folder.id, location, blob)`). This is
        # the primary address the download route itself tries first
        # (api/apps/restful_apis/file_api.py, ~line 317:
        # `stream_blob_response(file.parent_id, file.location, ...)`).
        #
        # If no object actually lives at that address, fall back the same way
        # the download route does: File2DocumentService.get_storage_address(file_id=...)
        # returns (file.parent_id, file.location) for LOCAL-sourced files, or
        # (doc.kb_id, doc.location) for KB-sourced files
        # (api/db/services/file2document_service.py::get_storage_address, lines 83-96).
        bucket, key = f.parent_id, f.location

        try:
            exists = settings.STORAGE_IMPL.obj_exist(bucket, key)
        except Exception:
            logging.exception(f"get_file: obj_exist check failed for {bucket}/{key}")
            exists = False

        if not exists:
            try:
                bucket, key = File2DocumentService.get_storage_address(file_id=f.id)
            except Exception:
                logging.exception(f"get_file: fallback storage address lookup failed for file id={f.id}")

        expires = timedelta(seconds=self._param.url_expires_s)
        sandbox_endpoint = os.environ.get("SANDBOX_PRESIGN_ENDPOINT") or None
        return settings.STORAGE_IMPL.get_presigned_url(bucket, key, expires, endpoint_override=sandbox_endpoint)

    def thoughts(self) -> str:
        return "Looking up the file and generating a short-lived URL..."
