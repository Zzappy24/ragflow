#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
"""
Custom B2B SaaS tool — render a Jinja-templated DOCX with content from an LLM.

WHY THIS TOOL EXISTS
--------------------
RAGFlow ships ExeSQL (DBs) and CodeExec (Python sandbox) but nothing that
faithfully fills a styled DOCX template. For our customers' "produce a
formal procedure / contract / report" use-case, fidelity to the corporate
template (logo, fonts, colors, table layouts) matters more than the LLM
quality.

The pattern this tool enables:

    Begin → Retrieval → Agent (LLM produces JSON) → RenderDocxTemplate → File

The template is a regular .docx authored in Word with `{{ field }}` and
`{% for ... %}` placeholders (docxtpl / Jinja2 syntax). The LLM only ever
emits structured JSON; the binding to file storage and the styled rendering
happen entirely on our side. Two consequences:

  1. Branding stays pixel-perfect (we never round-trip through markdown).
  2. The LLM never produces or sees raw bytes of the template — only schema
     and content fields — so this is compatible with the "data sovereignty"
     requirement when paired with a local LLM.

Tool config (set on the canvas, not by the LLM):
  - template_file_id : the .docx asset to render (must live in the active
                       workspace; cross-workspace access is denied)
  - output_folder_id : where the rendered .docx goes
  - output_filename  : optional Jinja-templated filename (defaults to a
                       timestamped name)

Tool input from LLM (function-calling):
  - content : a JSON object matching the template's expected schema

Returns: the new file's id + a human-readable summary.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import datetime
from abc import ABC

from agent.tools.base import ToolParamBase, ToolBase, ToolMeta
from common.connection_utils import timeout


class RenderDocxTemplateParam(ToolParamBase):
    """Define the RenderDocxTemplate component parameters."""

    def __init__(self):
        self.meta: ToolMeta = {
            "name": "render_docx_template",
            "description": (
                "Fill a corporate Word template (.docx with Jinja-style "
                "placeholders) with structured content and save the result "
                "to a workspace folder. Use this when the user wants a "
                "formal document that strictly matches a company template "
                "(procedure, contract, report, etc.). Pass `content` as a "
                "JSON object whose keys match the template's placeholders."
            ),
            "parameters": {
                "content": {
                    "type": "string",
                    "description": (
                        "JSON object (as a string) with the field values "
                        "expected by the template. The exact schema depends "
                        "on the template; see the template's documentation "
                        "or its placeholder list."
                    ),
                    "default": "{}",
                    "required": True,
                },
            },
        }
        super().__init__()
        # Canvas-level config (not from LLM):
        self.template_file_id = ""
        self.output_folder_id = ""
        # Jinja-rendered filename. Defaults to a timestamp + the document's
        # `procedure_name` field (slugified) when present.
        self.output_filename = "{{procedure_name|default('document')|slugify}}-{{ts}}.docx"
        # Hard cap on rendered file size to protect storage. 50MB is generous
        # for any reasonable procedure/contract; raises on overflow.
        self.max_output_bytes = 50 * 1024 * 1024

    def check(self):
        self.check_empty(self.template_file_id, "Template file id")
        self.check_empty(self.output_folder_id, "Output folder id")
        self.check_positive_integer(self.max_output_bytes, "Max output bytes")

    def get_input_form(self) -> dict[str, dict]:
        return {
            "content": {
                "name": "Content (JSON)",
                "type": "line",
            }
        }


def _slugify(value: str) -> str:
    """Filesystem-safe slug. Keeps unicode letters out for portability."""
    if not value:
        return "document"
    value = re.sub(r"[^\w\s-]", "", str(value), flags=re.ASCII)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return value.lower() or "document"


def _format_filename(pattern: str, content: dict) -> str:
    """Render the output filename pattern with the same `content` dict the
    template received, plus a `ts` field. Uses Jinja2 directly (already a
    transitive dep via docxtpl) so users can write things like
    `{{procedure_name|slugify}}-{{ts}}.docx` in the canvas."""
    from jinja2 import Environment, BaseLoader, select_autoescape

    env = Environment(loader=BaseLoader(), autoescape=select_autoescape([]))
    env.filters["slugify"] = _slugify
    ctx = dict(content) if isinstance(content, dict) else {}
    ctx["ts"] = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    try:
        return env.from_string(pattern).render(**ctx)
    except Exception:
        # Fall back to a safe default rather than failing the whole render.
        return f"document-{ctx['ts']}.docx"


def _ensure_dict(content):
    """The LLM may pass `content` as a JSON string or as an already-parsed
    dict (function-calling sometimes deserialises). Coerce safely."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        text = content.strip()
        if not text:
            return {}
        # Strip ```json fences the LLM occasionally adds even with structured output
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        return json.loads(text)
    raise ValueError(
        f"render_docx_template: `content` must be a JSON object or string, got {type(content).__name__}"
    )


class RenderDocxTemplate(ToolBase, ABC):
    component_name = "RenderDocxTemplate"

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 60)))
    def _invoke(self, **kwargs):
        if self.check_if_canceled("RenderDocxTemplate processing"):
            return

        # Lazy imports — heavy deps not used elsewhere.
        from docxtpl import DocxTemplate
        from api.db.db_models import DB
        from api.db.services.file_service import FileService
        from api.db import FileType
        from common import settings as _settings
        from common.misc_utils import get_uuid

        # 1. Validate + parse the content the LLM gave us.
        try:
            content = _ensure_dict(kwargs.get("content", "{}"))
        except json.JSONDecodeError as e:
            msg = f"render_docx_template: invalid JSON in `content`: {e}. Pass a JSON object."
            logging.warning(msg)
            self.set_output("error", msg)
            return msg

        # 2. Resolve workspace tenant_id from the canvas — used as the
        #    isolation key when reading the template and writing the output.
        tenant_id = self._canvas.get_tenant_id()
        if not tenant_id:
            raise RuntimeError("render_docx_template: canvas has no tenant_id.")

        # 3. Load the template file. Cross-workspace access is denied here
        #    even if a malicious agent config slipped in a foreign file_id.
        with DB.connection_context():
            ok, tmpl_file = FileService.get_by_id(self._param.template_file_id)
            if not ok or tmpl_file is None:
                raise FileNotFoundError(
                    f"render_docx_template: template file_id={self._param.template_file_id} not found."
                )
            if tmpl_file.tenant_id != tenant_id:
                # Hard refusal — workspace isolation invariant.
                raise PermissionError(
                    "render_docx_template: template does not belong to the active workspace."
                )

            ok, out_folder = FileService.get_by_id(self._param.output_folder_id)
            if not ok or out_folder is None:
                raise FileNotFoundError(
                    f"render_docx_template: output folder_id={self._param.output_folder_id} not found."
                )
            if out_folder.tenant_id != tenant_id:
                raise PermissionError(
                    "render_docx_template: output folder does not belong to the active workspace."
                )
            if out_folder.type != FileType.FOLDER.value:
                raise ValueError(
                    f"render_docx_template: output_folder_id must be a folder, got type={out_folder.type}."
                )

        # 4. Pull the template bytes from object storage (MinIO/S3-compat).
        tmpl_blob = _settings.STORAGE_IMPL.get(tmpl_file.parent_id, tmpl_file.location)
        if not tmpl_blob:
            raise IOError(
                f"render_docx_template: failed to fetch template bytes from storage "
                f"(bucket={tmpl_file.parent_id}, location={tmpl_file.location})."
            )

        if self.check_if_canceled("RenderDocxTemplate processing"):
            return

        # 5. Render. docxtpl uses Jinja2 — same syntax as the filename pattern,
        #    so authors learn one templating system.
        doc = DocxTemplate(io.BytesIO(tmpl_blob))
        try:
            doc.render(content)
        except Exception as e:
            msg = (
                f"render_docx_template: template render failed — "
                f"check that placeholders match the keys in `content`. {e}"
            )
            logging.exception(msg)
            self.set_output("error", msg)
            return msg

        out_buf = io.BytesIO()
        doc.save(out_buf)
        out_bytes = out_buf.getvalue()
        if len(out_bytes) > self._param.max_output_bytes:
            raise ValueError(
                f"render_docx_template: rendered output {len(out_bytes)} bytes "
                f"exceeds max_output_bytes={self._param.max_output_bytes}."
            )

        # 6. Write to storage and register a File row in the destination folder.
        filename = _format_filename(self._param.output_filename, content)
        if not filename.lower().endswith(".docx"):
            filename = f"{filename}.docx"
        # Avoid collisions: existing file with the same name in this folder
        # gets a numeric suffix. Cheap, no lock — race-condition window is
        # narrow and the worst outcome is two near-identical filenames.
        with DB.connection_context():
            base, ext = os.path.splitext(filename)
            n = 1
            candidate = filename
            while FileService.query(parent_id=out_folder.id, name=candidate):
                candidate = f"{base}-{n}{ext}"
                n += 1
            filename = candidate

        location = filename  # location within the folder bucket
        _settings.STORAGE_IMPL.put(out_folder.id, location, out_bytes)

        new_file_id = get_uuid()
        with DB.connection_context():
            FileService.insert({
                "id": new_file_id,
                "parent_id": out_folder.id,
                "tenant_id": tenant_id,
                "created_by": tenant_id,  # agent runs as the workspace tenant
                "name": filename,
                "location": location,
                "type": FileType.DOC.value,
                "size": len(out_bytes),
            })

        # 7. Surface a human-readable summary + machine-readable handle.
        result = {
            "file_id": new_file_id,
            "filename": filename,
            "folder_id": out_folder.id,
            "size_bytes": len(out_bytes),
            "tenant_id": tenant_id,
        }
        formalized = (
            f"Document rendered: **{filename}** ({len(out_bytes)} bytes) "
            f"saved to folder `{out_folder.name}`."
        )
        self.set_output("json", result)
        self.set_output("formalized_content", formalized)
        return formalized

    def thoughts(self) -> str:
        return "Filling the corporate template with the structured content..."
