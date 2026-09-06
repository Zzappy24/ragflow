"""
LLMToolPlugin variant of agent/tools/render_docx_template.py.

WHY A PLUGIN AND ALSO A TOOL
----------------------------
RAGFlow exposes two parallel tool systems:

  - `agent/tools/*.py` (ToolBase) — used as canvas NODES. Their UI is
    hardcoded in the React frontend (`Operator` enum + `ToolFormConfigMap`),
    so adding a new one requires touching the frontend code.

  - `agent/plugin/embedded_plugins/llm_tools/*.py` (LLMToolPlugin) — used
    as inline LLM tools attached to an Agent node via function-calling.
    Discovered dynamically by GET /v1/plugin/tools and surfaced by the
    Agent's "+ Add tool" dropdown automatically.

For the CEO challenge (S3 procedure generation) we need the second flavor:
the agent calls render_docx_template at LLM-runtime, not as a separate
canvas node. This plugin mirrors agent/tools/render_docx_template.py but
with the tighter LLMToolPlugin interface.

Workspace isolation: tenant_id is read from quart.g (the active request
context) and BOTH the template and output folder must belong to that
tenant — the same load-bearing security check as the ToolBase variant.

LLM-facing parameters
---------------------
  - template_file_id : the .docx asset to render (must live in the active
                       workspace; cross-workspace access is denied)
  - output_folder_id : where the rendered .docx is saved
  - content          : JSON object whose keys match the template's
                       Jinja placeholders (e.g. procedure_name,
                       beneficiaires, finalite, sections).

The system prompt of the Agent should tell the LLM which template_file_id
and output_folder_id to use — those are workspace-known values, not
something the LLM should invent.

NOTE: this file deliberately does NOT use `from __future__ import annotations`.
pluginlib (0.10) checks that subclass method annotations are *object-equal*
to the abstract parent's. Stringifying annotations via PEP 563 makes them
str instead of class objects, which trips the equality check and silently
skips the plugin from discovery (PluginWarning 216 "Type annotations differ").
The bad_calculator reference plugin shipped with RAGFlow has the same
constraint — keep this file annotation-realised.
"""
import io
import json
import logging
import re
from datetime import datetime

from agent.plugin.llm_tool_plugin import LLMToolMetadata, LLMToolPlugin


def _slugify(value: str) -> str:
    if not value:
        return "document"
    value = re.sub(r"[^\w\s-]", "", str(value), flags=re.ASCII)
    value = re.sub(r"[\s_-]+", "-", value).strip("-")
    return value.lower() or "document"


def _format_filename(pattern: str, content: dict) -> str:
    # CUSTOM B2B SaaS — SandboxedEnvironment obligatoire (SSTI → RCE, audit
    # 2026-09-06) ; cf. agent/tools/render_docx_template.py.
    from jinja2 import BaseLoader, select_autoescape
    from jinja2.sandbox import SandboxedEnvironment

    env = SandboxedEnvironment(loader=BaseLoader(), autoescape=select_autoescape([]))
    env.filters["slugify"] = _slugify
    ctx = dict(content) if isinstance(content, dict) else {}
    ctx["ts"] = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    try:
        return env.from_string(pattern).render(**ctx)
    except Exception:
        return f"document-{ctx['ts']}.docx"


def _ensure_dict(content):
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        text = content.strip()
        if not text:
            return {}
        # Strip markdown ```json fences a small LLM may add.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
        return json.loads(text)
    raise ValueError(
        f"render_docx_template: `content` must be a JSON object or string, got {type(content).__name__}"
    )


def _resolve_tenant_id() -> str:
    """Pull the active workspace tenant_id from quart.g. The Agent's
    runtime always invokes plugin tools inside a request context (one of
    the chat or canvas-run handlers), so g is reliably populated by our
    `_rbac_resolve_tenant` before_request hook. If not — we refuse rather
    than guess."""
    try:
        from quart import g
        from api.utils.tenant_context import maybe_active_tenant_id
    except Exception as e:
        raise RuntimeError(f"render_docx_template: cannot import Quart context: {e}")
    tid = maybe_active_tenant_id()
    if tid:
        return tid
    # Fallback: rbac_user_id might be set by a token_required path that
    # didn't go through the same lazy resolver.
    tid = getattr(g, "active_tenant_id", None)
    if tid:
        return tid
    raise RuntimeError("render_docx_template: no active workspace tenant resolvable from request context.")


class RenderDocxTemplatePlugin(LLMToolPlugin):
    """Fill a corporate Word template (.docx with Jinja placeholders) with
    structured content and save the result to a workspace folder.

    Workspace-isolated: rejects template_file_id or output_folder_id that
    do not belong to the active workspace.
    """

    _version_ = "1.0.0"

    @classmethod
    def get_metadata(cls) -> LLMToolMetadata:
        return {
            "name": "render_docx_template",
            "displayName": "Render DOCX template",
            "description": (
                "Fill a corporate Word template (.docx with Jinja-style "
                "placeholders) with structured content and save the "
                "rendered document to a workspace folder. Use this when "
                "the user wants a formal document that strictly matches "
                "a company template (procedure, contract, report, etc.). "
                "The `content` parameter must be a JSON object whose keys "
                "match the template's placeholders."
            ),
            "displayDescription": (
                "Fill a Word template with JSON content and save the .docx"
            ),
            "parameters": {
                "template_file_id": {
                    "type": "string",
                    "description": (
                        "ID of the .docx template file. Must live in the "
                        "active workspace. The system prompt provides this id."
                    ),
                    "displayDescription": "Template file ID (workspace-scoped)",
                    "required": True,
                },
                "output_folder_id": {
                    "type": "string",
                    "description": (
                        "ID of the folder where the rendered .docx will be "
                        "saved. Must live in the active workspace. The "
                        "system prompt provides this id."
                    ),
                    "displayDescription": "Output folder ID (workspace-scoped)",
                    "required": True,
                },
                "content": {
                    "type": "string",
                    "description": (
                        "JSON object (as a string) with the field values "
                        "expected by the template. Schema depends on the "
                        "template — see the template's documentation. "
                        "Plain text only, no markdown."
                    ),
                    "displayDescription": "JSON content matching the template schema",
                    "required": True,
                },
            },
        }

    def invoke(self, template_file_id: str = "", output_folder_id: str = "",
               content: str = "{}", **kwargs) -> str:
        # Lazy imports — keeps plugin discovery cheap.
        from docxtpl import DocxTemplate
        from api.db.db_models import DB
        from api.db.services.file_service import FileService
        from api.db import FileType
        from common import settings as _settings
        from common.misc_utils import get_uuid

        if not template_file_id:
            return "render_docx_template: missing template_file_id."
        if not output_folder_id:
            return "render_docx_template: missing output_folder_id."

        try:
            content_dict = _ensure_dict(content)
        except json.JSONDecodeError as e:
            msg = f"render_docx_template: invalid JSON in `content`: {e}. Pass a valid JSON object."
            logging.warning(msg)
            return msg

        try:
            tenant_id = _resolve_tenant_id()
        except Exception as e:
            return f"render_docx_template: {e}"

        # Workspace isolation: load template + folder, refuse foreign tenants.
        with DB.connection_context():
            ok, tmpl_file = FileService.get_by_id(template_file_id)
            if not ok or tmpl_file is None:
                return f"render_docx_template: template file_id={template_file_id!r} not found."
            if tmpl_file.tenant_id != tenant_id:
                return "render_docx_template: template does not belong to the active workspace."

            ok, out_folder = FileService.get_by_id(output_folder_id)
            if not ok or out_folder is None:
                return f"render_docx_template: output folder_id={output_folder_id!r} not found."
            if out_folder.tenant_id != tenant_id:
                return "render_docx_template: output folder does not belong to the active workspace."
            if out_folder.type != FileType.FOLDER.value:
                return f"render_docx_template: output_folder_id is not a folder (type={out_folder.type})."

        tmpl_blob = _settings.STORAGE_IMPL.get(tmpl_file.parent_id, tmpl_file.location)
        if not tmpl_blob:
            return (
                "render_docx_template: failed to fetch template bytes from storage "
                f"(bucket={tmpl_file.parent_id}, location={tmpl_file.location})."
            )

        doc = DocxTemplate(io.BytesIO(tmpl_blob))
        try:
            # CUSTOM B2B SaaS — bac à sable Jinja obligatoire (RCE sinon).
            from jinja2.sandbox import SandboxedEnvironment
            doc.render(content_dict, jinja_env=SandboxedEnvironment())
        except Exception as e:
            msg = (
                "render_docx_template: template render failed — check that placeholders "
                f"match the keys in `content`. {e}"
            )
            logging.exception(msg)
            return msg

        out_buf = io.BytesIO()
        doc.save(out_buf)
        out_bytes = out_buf.getvalue()
        max_bytes = 50 * 1024 * 1024
        if len(out_bytes) > max_bytes:
            return f"render_docx_template: rendered output {len(out_bytes)} bytes exceeds 50MB cap."

        filename = _format_filename(
            "{{procedure_name|default('document')|slugify}}-{{ts}}.docx",
            content_dict,
        )
        if not filename.lower().endswith(".docx"):
            filename = f"{filename}.docx"

        # Avoid collision: numeric suffix on existing names in the folder.
        with DB.connection_context():
            base, ext = filename.rsplit(".", 1)
            n = 1
            candidate = filename
            while FileService.query(parent_id=out_folder.id, name=candidate):
                candidate = f"{base}-{n}.{ext}"
                n += 1
            filename = candidate

        location = filename
        _settings.STORAGE_IMPL.put(out_folder.id, location, out_bytes)

        new_file_id = get_uuid()
        with DB.connection_context():
            FileService.insert({
                "id": new_file_id,
                "parent_id": out_folder.id,
                "tenant_id": tenant_id,
                "created_by": tenant_id,
                "name": filename,
                "location": location,
                "type": FileType.DOC.value,
                "size": len(out_bytes),
            })

        return (
            f"Document rendered: **{filename}** ({len(out_bytes)} bytes) "
            f"saved to folder `{out_folder.name}` (file_id={new_file_id})."
        )
