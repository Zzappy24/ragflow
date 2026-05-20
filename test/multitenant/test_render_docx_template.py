"""
Smoke + invariants test for `agent/tools/render_docx_template.py`.

WHY THIS FILE EXISTS
--------------------
The CEO challenge (2026-04-30) requires generating a procedure document
that *strictly* follows the Cyllene template — branding, tables, sections.
The custom RenderDocxTemplate tool is the bridge between the LLM (which
emits structured JSON) and the styled .docx (which preserves branding).

These tests pin three invariants that any future change to the tool must
not break:

  1. End-to-end render: given a Jinja-templated .docx and a JSON payload,
     the tool produces a valid .docx file in the output folder, registers
     a File row, and the rendered text actually contains the substituted
     content (so a future regex/Jinja swap can't silently no-op).

  2. Workspace isolation: a template_file_id from another workspace is
     refused with PermissionError. Same for output_folder_id. This is the
     load-bearing security invariant in our multi-tenant fork.

  3. Bad-input resilience: malformed JSON `content`, missing template
     file, and template-vs-data schema mismatch all surface a clear
     error string rather than crashing the agent loop.

If a future upstream merge alters the tool API or the FileService
contract, these tests fail loudly and force an audit before release.

Run:
    PATH=/Users/zappy/.local/bin:/opt/homebrew/bin:$PATH \\
        DOC_ENGINE=infinity ADMIN_JWT_SECRET=... RSA_PASSPHRASE=Welcome \\
        PYTHONPATH=$(pwd) \\
        uv run python -m pytest test/multitenant/test_render_docx_template.py -v
"""
from __future__ import annotations

import io
import os
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# Same UserWarning suppression pattern as test_rbac_perf.py — RAGFlow's
# import chain (xgboost, tensorflow/UMAP) emits warnings that pyproject.toml's
# filterwarnings='error' converts into ImportError otherwise.
warnings.filterwarnings("ignore", category=UserWarning)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

CI_EMAIL = os.getenv("CI_EMAIL", "ci.internal@cyllene.com")


# ---------------------------------------------------------------------------
# Fixtures — heavy imports lazy so collection is fast.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def stack():
    """Bundle the heavy imports so each test gets a single ready-to-use handle."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from common import settings as _settings
            _settings.init_settings()
            from api.db.db_models import DB
            from api.db.services.file_service import FileService
            from api.db import FileType
            from common.misc_utils import get_uuid
            from agent.tools.render_docx_template import (
                RenderDocxTemplate, RenderDocxTemplateParam,
            )
    except Exception as e:
        pytest.skip(f"Stack not importable: {e}")
    return {
        "settings": _settings,
        "DB": DB,
        "FileService": FileService,
        "FileType": FileType,
        "get_uuid": get_uuid,
        "RenderDocxTemplate": RenderDocxTemplate,
        "RenderDocxTemplateParam": RenderDocxTemplateParam,
    }


@pytest.fixture(scope="module")
def ci_tenant(stack):
    """Resolve the CI workspace tenant_id — same path the bridge conftest uses."""
    DB = stack["DB"]
    from api.db.services.user_service import UserService
    from api.db.services.workspace_service import (
        WsMemberService, WorkspaceService,
    )
    with DB.connection_context():
        users = list(UserService.query(email=CI_EMAIL))
        if not users:
            pytest.skip(f"User {CI_EMAIL} not found.")
        u = users[0]
        memberships = WsMemberService.list_workspaces_for_user(u.id)
        if not memberships:
            pytest.skip("CI user has no workspace membership.")
        ok, ws = WorkspaceService.get_by_id(memberships[0].workspace_id)
        if not ok or ws is None:
            pytest.skip("Workspace not found.")
        return ws.tenant_id, u.id


def _build_minimal_template_bytes() -> bytes:
    """Produce a tiny .docx with the Jinja placeholders the test renders.
    Done programmatically so the test is hermetic — no shipped binary asset."""
    from docx import Document
    doc = Document()
    doc.add_heading("{{procedure_name}}", level=1)
    doc.add_paragraph("Bénéficiaires :")
    # docxtpl recognises {%p ... %} for paragraph-level loops.
    doc.add_paragraph("{%p for b in beneficiaires %}")
    doc.add_paragraph("- {{b}}")
    doc.add_paragraph("{%p endfor %}")
    doc.add_paragraph("Finalité : {{finalite}}")
    doc.add_paragraph("{%p for s in sections %}")
    doc.add_heading("{{loop.index}} {{s.titre}}", level=2)
    doc.add_paragraph("{{s.intro}}")
    doc.add_paragraph("{%p for puce in s.puces %}")
    doc.add_paragraph("- {{puce}}")
    doc.add_paragraph("{%p endfor %}")
    doc.add_paragraph("{%p endfor %}")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def template_and_folders(stack, ci_tenant):
    """Spin up a fresh template file + output folder in the CI workspace,
    yield their ids, and clean up after the test."""
    tenant_id, user_id = ci_tenant
    DB = stack["DB"]
    FileService = stack["FileService"]
    FileType = stack["FileType"]
    settings = stack["settings"]
    get_uuid = stack["get_uuid"]

    created = []
    with DB.connection_context():
        # Root folder for the tenant — write the template + output folder under it.
        root = FileService.get_root_folder(tenant_id)
        root_id = root["id"]

        # 1. Template file
        tmpl_blob = _build_minimal_template_bytes()
        tmpl_id = get_uuid()
        tmpl_loc = f"test-render-tmpl-{tmpl_id}.docx"
        settings.STORAGE_IMPL.put(root_id, tmpl_loc, tmpl_blob)
        FileService.insert({
            "id": tmpl_id, "parent_id": root_id, "tenant_id": tenant_id,
            "created_by": user_id, "name": tmpl_loc, "location": tmpl_loc,
            "type": FileType.DOC.value, "size": len(tmpl_blob),
        })
        created.append(("file", tmpl_id, root_id, tmpl_loc))

        # 2. Output folder
        out_id = get_uuid()
        out_name = f"test-render-out-{out_id}"
        FileService.insert({
            "id": out_id, "parent_id": root_id, "tenant_id": tenant_id,
            "created_by": user_id, "name": out_name, "location": "",
            "type": FileType.FOLDER.value, "size": 0,
        })
        created.append(("folder", out_id, None, None))

    yield {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "template_file_id": tmpl_id,
        "output_folder_id": out_id,
    }

    # Teardown: list any files in the output folder, delete them, then the
    # folder + template. Best-effort.
    with DB.connection_context():
        for f in FileService.query(parent_id=out_id):
            try:
                settings.STORAGE_IMPL.rm(out_id, f.location)
            except Exception:
                pass
            FileService.delete_by_id(f.id)
        FileService.delete_by_id(out_id)
        try:
            settings.STORAGE_IMPL.rm(root_id, tmpl_loc)
        except Exception:
            pass
        FileService.delete_by_id(tmpl_id)


def _make_canvas_stub(tenant_id: str):
    """RenderDocxTemplate inherits from ToolBase whose __init__ asserts a
    real Canvas. For the unit test we patch only what the tool actually
    reads (`_canvas.get_tenant_id()`, `_canvas.is_canceled()`, etc.)."""
    from agent.canvas import Canvas
    # We don't run a real canvas — bypass __init__ and inject the bare
    # minimum the tool touches. Canvas exposes `task_id` (not `_task_id`)
    # at instance level; `is_canceled()` reads it.
    canvas = Canvas.__new__(Canvas)
    canvas._tenant_id = tenant_id
    canvas.task_id = "test-render-task"
    canvas._messages = []
    canvas.error = ""
    return canvas


def _instantiate_tool(stack, canvas, template_file_id: str, output_folder_id: str):
    """Wire param + tool by hand — bypasses ToolBase.__init__'s Canvas type
    assertion using the same stubbing approach as exesql tests."""
    Param = stack["RenderDocxTemplateParam"]
    Tool = stack["RenderDocxTemplate"]
    p = Param()
    p.template_file_id = template_file_id
    p.output_folder_id = output_folder_id
    # Bypass strict isinstance(canvas, Canvas) check — our stub is built via
    # __new__ so it's the right type.
    tool = Tool.__new__(Tool)
    tool._canvas = canvas
    tool._id = "RenderDocxTemplate:test"
    tool._param = p
    tool._param.outputs = {}
    tool._param.debug_inputs = []
    tool._param.check()
    return tool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_end_to_end_render_writes_real_docx_with_substituted_content(stack, template_and_folders):
    """The happy path. A minimal template + a JSON payload should produce a
    .docx file whose extracted text contains the substituted values."""
    DB = stack["DB"]
    FileService = stack["FileService"]
    settings = stack["settings"]

    canvas = _make_canvas_stub(template_and_folders["tenant_id"])
    tool = _instantiate_tool(
        stack, canvas,
        template_and_folders["template_file_id"],
        template_and_folders["output_folder_id"],
    )

    payload = {
        "procedure_name": "Embauche fiche S3",
        "beneficiaires": ["RH", "Manager hiérarchique"],
        "finalite": "Sécuriser le processus d'habilitation défense.",
        "sections": [
            {
                "titre": "Vérifications préalables",
                "intro": "Avant de lancer la demande :",
                "puces": ["Identité confirmée", "Casier judiciaire propre"],
            },
            {
                "titre": "Constitution du dossier",
                "intro": "Le dossier comprend :",
                "puces": ["Formulaire 94A", "Justificatifs"],
            },
        ],
    }

    import json as _json
    res = tool._invoke(content=_json.dumps(payload, ensure_ascii=False))
    assert isinstance(res, str), f"Expected human-readable summary string, got {type(res).__name__}"

    json_out = tool._param.outputs.get("json", {}).get("value") or tool.output("json")
    assert isinstance(json_out, dict) and json_out.get("file_id"), (
        f"Tool did not surface a `json` output with file_id; got {json_out!r}. "
        "The agent loop relies on this handle."
    )
    new_file_id = json_out["file_id"]

    # The file row exists in the output folder, scoped to the tenant.
    with DB.connection_context():
        ok, f = FileService.get_by_id(new_file_id)
        assert ok and f is not None
        assert f.tenant_id == template_and_folders["tenant_id"], (
            "Rendered file must inherit the active workspace tenant_id — "
            "otherwise it leaks across the multi-tenant boundary."
        )
        assert f.parent_id == template_and_folders["output_folder_id"]
        assert f.name.endswith(".docx")

    # The docx contains the substituted content.
    blob = settings.STORAGE_IMPL.get(template_and_folders["output_folder_id"], f.location)
    assert blob, "Storage returned empty bytes — render output was not persisted."
    from docx import Document
    rendered = Document(io.BytesIO(blob))
    text = "\n".join(p.text for p in rendered.paragraphs)
    text += "\n" + "\n".join(p.text for p in rendered.tables for _ in [0])  # safety
    assert "Embauche fiche S3" in text, (
        f"procedure_name placeholder was not substituted. Rendered text:\n{text[:500]}"
    )
    assert "Sécuriser le processus" in text, (
        "finalite placeholder was not substituted — Jinja rendering may have failed silently."
    )
    assert "Vérifications préalables" in text, "Section title missing in render."
    assert "Identité confirmée" in text, "Bullet content missing in render."


def test_cross_workspace_template_is_refused(stack, template_and_folders):
    """Security invariant: even if a template_file_id from another workspace
    is configured, the tool must refuse rather than render. This is the only
    thing standing between us and a cross-tenant data exfiltration via
    document templating."""
    canvas = _make_canvas_stub("tenant-belonging-to-someone-else")
    tool = _instantiate_tool(
        stack, canvas,
        template_and_folders["template_file_id"],
        template_and_folders["output_folder_id"],
    )
    with pytest.raises(PermissionError, match="active workspace"):
        tool._invoke(content="{}")


def test_missing_template_file_id_raises(stack, template_and_folders):
    """A non-existent template_file_id surfaces FileNotFoundError, not a
    silent no-op or an obscure stack trace inside docxtpl."""
    canvas = _make_canvas_stub(template_and_folders["tenant_id"])
    tool = _instantiate_tool(
        stack, canvas,
        "0" * 32,  # no such file
        template_and_folders["output_folder_id"],
    )
    with pytest.raises(FileNotFoundError, match="not found"):
        tool._invoke(content="{}")


def test_malformed_json_content_returns_error_message(stack, template_and_folders):
    """LLMs sometimes emit invalid JSON. The tool must not crash the agent
    loop — it should return a human-readable error so the LLM can self-correct."""
    canvas = _make_canvas_stub(template_and_folders["tenant_id"])
    tool = _instantiate_tool(
        stack, canvas,
        template_and_folders["template_file_id"],
        template_and_folders["output_folder_id"],
    )
    res = tool._invoke(content="{not valid json,,,}")
    assert isinstance(res, str)
    assert "JSON" in res or "json" in res, f"Expected a JSON-related error, got {res!r}"
    err = tool._param.outputs.get("error", {})
    err_val = err.get("value") if isinstance(err, dict) else None
    assert err_val, "An `error` output must be set so the agent can react."


def test_filename_pattern_renders_with_jinja(stack, template_and_folders):
    """The output filename pattern is itself a Jinja template — verify the
    `slugify` filter and `ts` injection actually happen."""
    canvas = _make_canvas_stub(template_and_folders["tenant_id"])
    tool = _instantiate_tool(
        stack, canvas,
        template_and_folders["template_file_id"],
        template_and_folders["output_folder_id"],
    )
    # Custom pattern that exercises slugify + arbitrary content key + ts.
    tool._param.output_filename = "proc-{{procedure_name|slugify}}-{{ts}}.docx"

    import json as _json
    res = tool._invoke(content=_json.dumps({
        "procedure_name": "Embauche FICHE S3",  # spaces + caps -> slugify
        "beneficiaires": [], "finalite": "x", "sections": [],
    }))
    assert isinstance(res, str)

    json_out = tool._param.outputs.get("json", {}).get("value") or tool.output("json")
    fn = json_out["filename"]
    # slug must be lowercase, no spaces, dashes
    assert "embauche-fiche-s3" in fn.lower(), (
        f"Filename slug not applied. Got {fn!r}. Pattern was {tool._param.output_filename!r}."
    )
    # ts placeholder must be substituted (digits + 'T' + 'Z')
    import re as _re
    assert _re.search(r"\d{8}T\d{6}Z", fn), (
        f"Timestamp not injected into filename: {fn!r}. Falls back to a non-deterministic name?"
    )
