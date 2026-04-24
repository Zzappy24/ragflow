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
import enum
import time
from common import settings
from common.constants import LLMType
from api.db.services.llm_service import LLMService
from api.db.services.tenant_llm_service import TenantLLMService, TenantService

# CUSTOM PERF: TTL cache for model config lookups — config changes rarely, avoids repeated MySQL SELECTs
# Upstream has no cache here; every chat request and every embedding batch hit the DB.
_MODEL_CONFIG_CACHE: dict[str, tuple[dict, float]] = {}
_MODEL_CONFIG_TTL = 300  # 5 minutes


def _model_config_cache_key(tenant_id: str, model_type: str, model_name: str) -> str:
    return f"{tenant_id}:{model_type}:{model_name}"


def _invalidate_model_config_cache(tenant_id: str | None = None):
    if tenant_id is None:
        _MODEL_CONFIG_CACHE.clear()
    else:
        for k in list(_MODEL_CONFIG_CACHE.keys()):
            if k.startswith(f"{tenant_id}:"):
                del _MODEL_CONFIG_CACHE[k]


# CUSTOM B2B SaaS – graceful fallback from workspace tenant to creator's personal
# tenant. See CLAUDE.md "Custom B2B SaaS Multi-Tenant Layer" for merge warnings.
def _fallback_personal_tenant_id(workspace_tenant_id: str) -> str | None:
    """If workspace_tenant_id belongs to a Workspace, return the personal
    tenant_id (== user_id) of the workspace creator. Returns None otherwise."""
    try:
        from api.db.services.workspace_service import WorkspaceService
        ws = WorkspaceService.get_by_tenant_id(workspace_tenant_id)
        if ws and ws.created_by:
            return ws.created_by
    except Exception:
        pass
    return None


def get_model_config_by_id(tenant_model_id: int) -> dict:
    found, model_config = TenantLLMService.get_by_id(tenant_model_id)
    if not found:
        raise LookupError(f"Tenant Model with id {tenant_model_id} not found")
    config_dict = model_config.to_dict()
    llm = LLMService.query(llm_name=config_dict["llm_name"])
    if llm:
        config_dict["is_tools"] = llm[0].is_tools
    return config_dict


def get_model_config_by_type_and_name(tenant_id: str, model_type: str, model_name: str):
    if not model_name:
        raise Exception("Model Name is required")
    model_type_val = model_type.value if hasattr(model_type, "value") else model_type
    cache_key = _model_config_cache_key(tenant_id, model_type_val, model_name)
    cached = _MODEL_CONFIG_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[1] < _MODEL_CONFIG_TTL:
        return cached[0]
    model_config = TenantLLMService.get_api_key(tenant_id, model_name, model_type_val)
    if not model_config:
        # model_name in format 'name@factory', split model_name and try again
        pure_model_name, fid = TenantLLMService.split_model_name_and_factory(model_name)
        compose_profiles = os.getenv("COMPOSE_PROFILES", "")
        is_tei_builtin_embedding = (
            model_type_val == LLMType.EMBEDDING.value
            and "tei-" in compose_profiles
            and pure_model_name == os.getenv("TEI_MODEL", "")
            and (fid == "Builtin" or fid is None)
        )
        if is_tei_builtin_embedding:
            # configured local embedding model
            embedding_cfg = settings.EMBEDDING_CFG
            config_dict = {
                "llm_factory": "Builtin",
                "api_key": embedding_cfg["api_key"],
                "llm_name": pure_model_name,
                "api_base": embedding_cfg["base_url"],
                "model_type": LLMType.EMBEDDING.value,
            }
        elif model_type_val == LLMType.CHAT.value:
            # Retry as CHAT with pure_model_name first; then fall back to a multimodal model registered under IMAGE2TEXT.
            model_config = TenantLLMService.get_api_key(tenant_id, pure_model_name, LLMType.CHAT.value)
            if not model_config:
                model_config = TenantLLMService.get_api_key(tenant_id, pure_model_name, LLMType.IMAGE2TEXT.value)
            if not model_config:
                raise LookupError(f"Tenant Model with name {model_name} and type {model_type_val} not found")
            config_dict = model_config.to_dict()
        else:
            model_config = TenantLLMService.get_api_key(tenant_id, pure_model_name, model_type_val)
            if not model_config:
                # CUSTOM: fallback to workspace creator's personal tenant
                fb_tid = _fallback_personal_tenant_id(tenant_id)
                if fb_tid:
                    model_config = TenantLLMService.get_api_key(fb_tid, model_name, model_type_val)
                    if not model_config:
                        model_config = TenantLLMService.get_api_key(fb_tid, pure_model_name, model_type_val)
                    if model_config:
                        logging.info("Model %s resolved via fallback to personal tenant %s", model_name, fb_tid)
            if not model_config:
                raise LookupError(f"Tenant Model with name {model_name} and type {model_type_val} not found")
            config_dict = model_config.to_dict()
    else:
        # model_name without @factory
        config_dict = model_config.to_dict()
    config_model_type = config_dict.get("model_type")
    config_model_type = config_model_type.value if hasattr(config_model_type, "value") else config_model_type
    if config_model_type != model_type_val and not (
            model_type_val == LLMType.CHAT.value
            and config_model_type == LLMType.IMAGE2TEXT.value
    ):
        raise LookupError(
            f"Tenant Model with name {model_name} has type {config_model_type}, expected {model_type_val}"
        )
    llm = LLMService.query(llm_name=config_dict["llm_name"])
    if llm:
        config_dict["is_tools"] = llm[0].is_tools
    _MODEL_CONFIG_CACHE[cache_key] = (config_dict, time.monotonic())
    return config_dict


def get_tenant_default_model_by_type(tenant_id: str, model_type: str|enum.Enum):
    exist, tenant = TenantService.get_by_id(tenant_id)
    if not exist:
        raise LookupError("Tenant not found")
    model_type_val = model_type if isinstance(model_type, str) else model_type.value
    model_name: str = ""
    match model_type_val:
        case LLMType.EMBEDDING.value:
            model_name = tenant.embd_id
        case LLMType.SPEECH2TEXT.value:
            model_name =  tenant.asr_id
        case LLMType.IMAGE2TEXT.value:
            model_name = tenant.img2txt_id
        case LLMType.CHAT.value:
            model_name = tenant.llm_id
        case LLMType.RERANK.value:
            model_name = tenant.rerank_id
        case LLMType.TTS.value:
            model_name = tenant.tts_id
        case LLMType.OCR.value:
            raise Exception("OCR model name is required")
        case _:
            raise Exception(f"Unknown model type {model_type}")
    if not model_name:
        # CUSTOM: fallback to workspace creator's personal tenant for default model
        fb_tid = _fallback_personal_tenant_id(tenant_id)
        if fb_tid:
            logging.info("No default %s on workspace tenant %s, falling back to personal tenant %s", model_type_val, tenant_id, fb_tid)
            return get_tenant_default_model_by_type(fb_tid, model_type)
        raise Exception(f"No default {model_type} model is set.")
    try:
        return get_model_config_by_type_and_name(tenant_id, model_type, model_name)
    except LookupError:
        # CUSTOM: model name set on workspace tenant but config missing → fallback
        fb_tid = _fallback_personal_tenant_id(tenant_id)
        if fb_tid:
            logging.info("Model %s not found on workspace tenant %s, falling back to personal tenant %s", model_name, tenant_id, fb_tid)
            return get_model_config_by_type_and_name(fb_tid, model_type, model_name)
        raise
