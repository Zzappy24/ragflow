"""
Workspace LLM model configuration routes.

Allows ws_admins, org_admins, and superusers to configure LLM providers and
default models for workspace tenants — without touching the active_tenant_id()
context used by RAGFlow's own routes. ws_admin access is scoped to their own
workspace via ``require_ws_admin``.

Supported providers (local-inference only):
  - Ollama          factory="Ollama",                 no name suffix
  - vLLM            factory="VLLM",                   name stored as "{name}___VLLM"
  - OpenAI-Compatible factory="OpenAI-API-Compatible", name stored as "{name}___OpenAI-API"

CUSTOM B2B SaaS — see CLAUDE.md "Custom B2B SaaS Multi-Tenant Layer" for merge warnings.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import get_current_user_id, require_ws_admin
from management.server.models.schemas import (
    WsLlmProviderAdd,
    WsLlmProviderUpdate,
    WsLlmDefaultsSet,
    WsLlmProviderResponse,
    WsLlmDefaultsResponse,
    WsLlmVerifyRequest,
    WsLlmVerifyResponse,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Name-suffix mapping — mirrors RAGFlow's add_llm logic (llm_app.py ~195-205)
# CUSTOM B2B SaaS — keep in sync with upstream on merge
# ---------------------------------------------------------------------------
_FACTORY_SUFFIX = {
    "VLLM": "___VLLM",
    "OpenAI-API-Compatible": "___OpenAI-API",
}


def _stored_name(factory: str, llm_name: str) -> str:
    """Return the name as stored in tenant_llm (with factory suffix if needed)."""
    suffix = _FACTORY_SUFFIX.get(factory, "")
    if suffix and not llm_name.endswith(suffix):
        return llm_name + suffix
    return llm_name


def _display_name(factory: str, llm_name: str) -> str:
    """Strip the factory suffix for display."""
    suffix = _FACTORY_SUFFIX.get(factory, "")
    if suffix and llm_name.endswith(suffix):
        return llm_name[: -len(suffix)]
    return llm_name


def _get_ws_tenant(ws_id: str) -> str:
    """Return tenant_id for the workspace or raise 404."""
    from api.db.services.workspace_service import WorkspaceService
    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws.tenant_id


# ---------------------------------------------------------------------------
# GET  /workspaces/{ws_id}/models/providers
# ---------------------------------------------------------------------------

@router.get("/workspaces/{ws_id}/models/providers", response_model=list[WsLlmProviderResponse])
def list_workspace_providers(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """List all LLM providers configured for this workspace tenant."""
    require_ws_admin(ws_id, user_id)
    tenant_id = _get_ws_tenant(ws_id)

    from api.db.services.tenant_llm_service import TenantLLMService
    llms = TenantLLMService.get_my_llms(tenant_id)
    return [
        WsLlmProviderResponse(
            llm_factory=r.get("llm_factory", ""),
            llm_name=_display_name(r.get("llm_factory", ""), r.get("llm_name", "")),
            model_type=r.get("model_type", ""),
            api_base=r.get("api_base") or "",
            max_tokens=r.get("max_tokens", 8192),
            used_tokens=r.get("used_tokens", 0),
            status=r.get("status", "1"),
        )
        for r in llms
    ]


# ---------------------------------------------------------------------------
# POST /workspaces/{ws_id}/models/providers  — add a model
# ---------------------------------------------------------------------------

@router.post(
    "/workspaces/{ws_id}/models/providers",
    status_code=status.HTTP_201_CREATED,
    response_model=WsLlmProviderResponse,
)
def add_workspace_provider(
    ws_id: str,
    body: WsLlmProviderAdd,
    user_id: str = Depends(get_current_user_id),
):
    """Add a new LLM model to the workspace tenant.

    The llm_name is stored with the factory suffix automatically
    (e.g. "llama3___VLLM" for VLLM factory) — mirroring RAGFlow's add_llm.
    """
    require_ws_admin(ws_id, user_id)
    tenant_id = _get_ws_tenant(ws_id)

    from api.db.services.tenant_llm_service import TenantLLMService
    from api.db.db_models import TenantLLM

    stored = _stored_name(body.llm_factory, body.llm_name)
    llm_row = {
        "tenant_id": tenant_id,
        "llm_factory": body.llm_factory,
        "llm_name": stored,
        "model_type": body.model_type,
        "api_key": body.api_key or "x",
        "api_base": body.api_base or "",
        "max_tokens": body.max_tokens,
    }

    existing = TenantLLMService.filter_update(
        [
            TenantLLM.tenant_id == tenant_id,
            TenantLLM.llm_factory == body.llm_factory,
            TenantLLM.llm_name == stored,
        ],
        llm_row,
    )
    if not existing:
        TenantLLMService.save(**llm_row)
    from api.db.joint_services.tenant_model_service import _invalidate_model_config_cache
    _invalidate_model_config_cache(tenant_id)
    return WsLlmProviderResponse(
        llm_factory=body.llm_factory,
        llm_name=body.llm_name,
        model_type=body.model_type,
        api_base=body.api_base or "",
        max_tokens=body.max_tokens,
        used_tokens=0,
        status="1",
    )


# ---------------------------------------------------------------------------
# PUT /workspaces/{ws_id}/models/providers  — edit (factory & llm_name as query params
# to avoid %2F path-separator issues with model names containing "/")
# ---------------------------------------------------------------------------

@router.put(
    "/workspaces/{ws_id}/models/providers",
    response_model=WsLlmProviderResponse,
)
def update_workspace_provider(
    ws_id: str,
    factory: str,
    llm_name: str,
    body: WsLlmProviderUpdate,
    user_id: str = Depends(get_current_user_id),
):
    """Update an existing LLM model configuration (api_key, api_base, max_tokens)."""
    require_ws_admin(ws_id, user_id)
    tenant_id = _get_ws_tenant(ws_id)

    from api.db.services.tenant_llm_service import TenantLLMService
    from api.db.db_models import TenantLLM

    stored = _stored_name(factory, llm_name)
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="Nothing to update")

    updated = TenantLLMService.filter_update(
        [
            TenantLLM.tenant_id == tenant_id,
            TenantLLM.llm_factory == factory,
            TenantLLM.llm_name == stored,
        ],
        update_data,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Model not found")
    from api.db.joint_services.tenant_model_service import _invalidate_model_config_cache
    _invalidate_model_config_cache(tenant_id)
    # Return updated row
    llms = TenantLLMService.get_my_llms(tenant_id)
    row = next((r for r in llms if r.get("llm_name") == stored and r.get("llm_factory") == factory), None)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found after update")

    return WsLlmProviderResponse(
        llm_factory=factory,
        llm_name=llm_name,
        model_type=row.get("model_type", ""),
        api_base=row.get("api_base") or "",
        max_tokens=row.get("max_tokens", 8192),
        used_tokens=row.get("used_tokens", 0),
        status=row.get("status", "1"),
    )


# ---------------------------------------------------------------------------
# PATCH /workspaces/{ws_id}/models/providers/status  — toggle
# (factory & llm_name as query params for the same reason as PUT above)
# ---------------------------------------------------------------------------

@router.patch(
    "/workspaces/{ws_id}/models/providers/status",
    response_model=WsLlmProviderResponse,
)
def toggle_workspace_provider_status(
    ws_id: str,
    factory: str,
    llm_name: str,
    enabled: bool,
    user_id: str = Depends(get_current_user_id),
):
    """Enable (enabled=true) or disable (enabled=false) a model."""
    require_ws_admin(ws_id, user_id)
    tenant_id = _get_ws_tenant(ws_id)

    from api.db.services.tenant_llm_service import TenantLLMService
    from api.db.db_models import TenantLLM

    stored = _stored_name(factory, llm_name)
    new_status = "1" if enabled else "0"

    updated = TenantLLMService.filter_update(
        [
            TenantLLM.tenant_id == tenant_id,
            TenantLLM.llm_factory == factory,
            TenantLLM.llm_name == stored,
        ],
        {"status": new_status},
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Model not found")
    from api.db.joint_services.tenant_model_service import _invalidate_model_config_cache
    _invalidate_model_config_cache(tenant_id)
    llms = TenantLLMService.get_my_llms(tenant_id)
    row = next((r for r in llms if r.get("llm_name") == stored and r.get("llm_factory") == factory), None)

    return WsLlmProviderResponse(
        llm_factory=factory,
        llm_name=llm_name,
        model_type=row.get("model_type", "") if row else "",
        api_base=row.get("api_base") or "" if row else "",
        max_tokens=row.get("max_tokens", 8192) if row else 8192,
        used_tokens=row.get("used_tokens", 0) if row else 0,
        status=new_status,
    )


# ---------------------------------------------------------------------------
# DELETE /workspaces/{ws_id}/models/providers  — (factory & llm_name as query params)
# ---------------------------------------------------------------------------

@router.delete(
    "/workspaces/{ws_id}/models/providers",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_workspace_provider(
    ws_id: str,
    factory: str,
    llm_name: str,
    user_id: str = Depends(get_current_user_id),
):
    """Remove a specific model from the workspace tenant."""
    require_ws_admin(ws_id, user_id)
    tenant_id = _get_ws_tenant(ws_id)

    from api.db.services.tenant_llm_service import TenantLLMService
    from api.db.db_models import TenantLLM

    stored = _stored_name(factory, llm_name)
    TenantLLMService.filter_delete([
        TenantLLM.tenant_id == tenant_id,
        TenantLLM.llm_factory == factory,
        TenantLLM.llm_name == stored,
    ])


# ---------------------------------------------------------------------------
# POST /workspaces/{ws_id}/models/verify  — test credentials before saving
# ---------------------------------------------------------------------------

@router.post("/workspaces/{ws_id}/models/verify", response_model=WsLlmVerifyResponse)
async def verify_workspace_model(
    ws_id: str,
    body: WsLlmVerifyRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Test that a model endpoint is reachable and the credentials work.

    Does NOT save anything. Returns {ok, message} with the result.
    Instantiates the model class directly (same logic as RAGFlow's add_llm
    with verify=true) scoped to no tenant — pure connectivity check.

    CUSTOM B2B SaaS — see CLAUDE.md for merge warnings.

    NOTE: chat verification uses `async_chat_streamly`. RAGFlow removed the
    synchronous `Base.chat()` method (only some legacy subclasses still
    define it), so calling `mdl.chat(...)` raises AttributeError on most
    factories — including the OpenAI-compatible one used for Ollama/vLLM.
    This handler is async so we can `await` the streaming generator.
    """
    require_ws_admin(ws_id, user_id)

    stored_name = _stored_name(body.llm_factory, body.llm_name)
    api_key = body.api_key or "x"
    api_base = body.api_base or ""
    model_type = body.model_type

    try:
        from rag.llm import EmbeddingModel, ChatModel, RerankModel, CvModel, TTSModel, Seq2txtModel
        from common.constants import LLMType
        import asyncio

        factory = body.llm_factory

        if model_type == LLMType.EMBEDDING.value:
            mdl = EmbeddingModel[factory](api_key, stored_name, base_url=api_base)
            await asyncio.to_thread(mdl.encode, ["Test if the api key is available"])

        elif model_type == LLMType.RERANK.value:
            mdl = RerankModel[factory](api_key, stored_name, base_url=api_base)
            await asyncio.to_thread(mdl.similarity, "What is RAGFlow?", ["RAGFlow is a RAG engine."])

        elif model_type == LLMType.IMAGE2TEXT.value:
            from rag.utils.base64_image import test_image
            mdl = CvModel[factory](api_key, stored_name, base_url=api_base)
            await asyncio.to_thread(mdl.describe, test_image)

        elif model_type == LLMType.TTS.value:
            mdl = TTSModel[factory](api_key, stored_name, base_url=api_base)
            for _ in mdl.tts("Test"):
                break

        elif model_type == LLMType.SPEECH2TEXT.value:
            # RAGFlow itself has no verify for ASR — skip actual test
            pass

        else:
            # Default: chat — mirror api/apps/llm_app.py::set_api_key.
            # async_chat_streamly is the canonical chat entry point on Base.
            mdl = ChatModel[factory](api_key, stored_name, base_url=api_base)
            timeout_s = int(os.environ.get("LLM_TIMEOUT_SECONDS", 30))
            received_chunk = False
            error_text = ""

            async def _check():
                nonlocal received_chunk, error_text
                async for chunk in mdl.async_chat_streamly(
                    None,
                    [{"role": "user", "content": "Hi"}],
                    {"temperature": 0.7},
                ):
                    if not isinstance(chunk, str):
                        continue
                    if "**ERROR**" in chunk:
                        error_text = chunk
                        return
                    if chunk.strip():
                        received_chunk = True
                        return

            try:
                await asyncio.wait_for(_check(), timeout=timeout_s)
            except asyncio.TimeoutError:
                return WsLlmVerifyResponse(
                    ok=False,
                    message=f"Timeout after {timeout_s}s — endpoint unreachable or model not warmed up.",
                )
            if error_text:
                return WsLlmVerifyResponse(ok=False, message=error_text)
            if not received_chunk:
                return WsLlmVerifyResponse(ok=False, message="No response received from model.")

        return WsLlmVerifyResponse(ok=True, message="Connection successful")

    except Exception as e:
        return WsLlmVerifyResponse(ok=False, message=str(e))


# ---------------------------------------------------------------------------
# GET  /workspaces/{ws_id}/models/defaults
# ---------------------------------------------------------------------------

@router.get("/workspaces/{ws_id}/models/defaults", response_model=WsLlmDefaultsResponse)
def get_workspace_defaults(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """Get the default model IDs configured on this workspace tenant."""
    require_ws_admin(ws_id, user_id)
    tenant_id = _get_ws_tenant(ws_id)

    from api.db.services.tenant_llm_service import TenantService
    ok, tenant = TenantService.get_by_id(tenant_id)
    if not ok or not tenant:
        raise HTTPException(status_code=404, detail="Workspace tenant not found")

    return WsLlmDefaultsResponse(
        llm_id=tenant.llm_id or "",
        embd_id=tenant.embd_id or "",
        asr_id=tenant.asr_id or "",
        img2txt_id=tenant.img2txt_id or "",
        rerank_id=tenant.rerank_id or "",
        tts_id=tenant.tts_id or "",
    )


# ---------------------------------------------------------------------------
# PUT  /workspaces/{ws_id}/models/defaults
# ---------------------------------------------------------------------------

@router.put("/workspaces/{ws_id}/models/defaults", response_model=WsLlmDefaultsResponse)
def set_workspace_defaults(
    ws_id: str,
    body: WsLlmDefaultsSet,
    user_id: str = Depends(get_current_user_id),
):
    """Set the default model IDs for this workspace tenant."""
    require_ws_admin(ws_id, user_id)
    tenant_id = _get_ws_tenant(ws_id)

    params = body.model_dump(exclude_none=True)

    from api.utils.tenant_utils import ensure_tenant_model_id_for_params
    try:
        update_dict = ensure_tenant_model_id_for_params(tenant_id, params)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Model resolution failed: {e}")

    from api.db.services.tenant_llm_service import TenantService
    TenantService.update_by_id(tenant_id, update_dict)

    ok, tenant = TenantService.get_by_id(tenant_id)
    return WsLlmDefaultsResponse(
        llm_id=tenant.llm_id or "",
        embd_id=tenant.embd_id or "",
        asr_id=tenant.asr_id or "",
        img2txt_id=tenant.img2txt_id or "",
        rerank_id=tenant.rerank_id or "",
        tts_id=tenant.tts_id or "",
    )
