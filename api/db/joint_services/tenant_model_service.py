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
import json
import time
from common import settings
from common.constants import LLMType, ActiveStatusEnum, MINERU_DEFAULT_CONFIG, MINERU_ENV_KEYS, OPENDATALOADER_DEFAULT_CONFIG, OPENDATALOADER_ENV_KEYS, PADDLEOCR_DEFAULT_CONFIG, PADDLEOCR_ENV_KEYS
from api.db.services.llm_service import LLMService
from api.db.services.tenant_llm_service import TenantLLMService, TenantService
from api.db.services.tenant_model_provider_service import TenantModelProviderService
from api.db.services.tenant_model_instance_service import TenantModelInstanceService
from api.db.services.tenant_model_service import TenantModelService

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
    api_key, is_tools, api_key_payload = TenantLLMService._decode_api_key_config(config_dict.get("api_key", ""))
    config_dict["api_key"] = api_key
    if api_key_payload is not None:
        config_dict["api_key_payload"] = api_key_payload
    if is_tools is not None:
        config_dict["is_tools"] = is_tools
    llm = LLMService.query(llm_name=config_dict["llm_name"])
    if "is_tools" not in config_dict and llm:
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
    api_key, is_tools, api_key_payload = TenantLLMService._decode_api_key_config(config_dict.get("api_key", ""))
    config_dict["api_key"] = api_key
    if api_key_payload is not None:
        config_dict["api_key_payload"] = api_key_payload
    if is_tools is not None:
        config_dict["is_tools"] = is_tools
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
    if "is_tools" not in config_dict and llm:
        config_dict["is_tools"] = llm[0].is_tools
    _MODEL_CONFIG_CACHE[cache_key] = (config_dict, time.monotonic())
    return config_dict


def _factory_model_types(llm: dict) -> list[str]:
    model_type = llm.get("model_type")
    if isinstance(model_type, list):
        return model_type
    return [model_type] if model_type else []
def _decode_api_key_config(raw_api_key: str) -> tuple[str, bool | None, str | None]:
    if not raw_api_key:
        return raw_api_key, None, None

    try:
        parsed = json.loads(raw_api_key)
    except Exception:
        return raw_api_key, None, None

    if not isinstance(parsed, dict):
        return raw_api_key, None, None

    is_tools = bool(parsed["is_tools"]) if "is_tools" in parsed else None
    if set(parsed.keys()) <= {"api_key", "is_tools"}:
        return parsed.get("api_key", ""), is_tools, None

    return parsed.get("api_key", raw_api_key), is_tools, raw_api_key


def get_first_provider_model_name(tenant_id: str, provider_name: str, model_type: str | enum.Enum) -> str | None:
    model_type_val = model_type if isinstance(model_type, str) else model_type.value
    provider_obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)
    if not provider_obj:
        return None

    for instance_obj in TenantModelInstanceService.get_all_by_provider_id(provider_obj.id):
        if instance_obj.status != ActiveStatusEnum.ACTIVE.value:
            continue
        for model_obj in TenantModelService.get_models_by_instance_id(instance_obj.id):
            if model_obj.model_type == model_type_val and model_obj.status == ActiveStatusEnum.ACTIVE.value:
                return f"{model_obj.model_name}@{instance_obj.instance_name}@{provider_name}"
    return None


def _collect_env_config(env_keys: list[str], default_config: dict) -> dict | None:
    config = dict(default_config)
    found = False
    for key in env_keys:
        value = os.environ.get(key)
        if value:
            found = True
            config[key] = value
    return config if found else None


def _ensure_ocr_provider_from_env(tenant_id: str, provider_name: str, model_name: str, config: dict | None) -> str | None:
    if not config:
        return None

    provider_obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)
    if not provider_obj:
        TenantModelProviderService.insert(tenant_id=tenant_id, provider_name=provider_name)
        provider_obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)

    api_key = json.dumps(config)
    instance_obj = TenantModelInstanceService.get_by_provider_id_and_api_key(provider_obj.id, api_key)
    if not instance_obj:
        instance_obj = TenantModelInstanceService.create_instance(
            provider_id=provider_obj.id,
            instance_name=model_name,
            api_key=api_key,
            extra="{}",
        )

    model_obj = TenantModelService.get_by_provider_id_and_instance_id_and_model_type_and_model_name(
        provider_obj.id,
        instance_obj.id,
        LLMType.OCR.value,
        model_name,
    )
    if not model_obj:
        TenantModelService.insert(
            model_name=model_name,
            provider_id=provider_obj.id,
            instance_id=instance_obj.id,
            model_type=LLMType.OCR.value,
            extra=json.dumps({"max_tokens": 0}),
        )

    return f"{model_name}@{instance_obj.instance_name}@{provider_name}"


def ensure_mineru_from_env(tenant_id: str) -> str | None:
    return _ensure_ocr_provider_from_env(
        tenant_id,
        "MinerU",
        "mineru-from-env",
        _collect_env_config(MINERU_ENV_KEYS, MINERU_DEFAULT_CONFIG),
    )


def ensure_paddleocr_from_env(tenant_id: str) -> str | None:
    return _ensure_ocr_provider_from_env(
        tenant_id,
        "PaddleOCR",
        "paddleocr-from-env",
        _collect_env_config(PADDLEOCR_ENV_KEYS, PADDLEOCR_DEFAULT_CONFIG),
    )


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
    # CUSTOM B2B SaaS: route through `get_model_config_from_provider_instance`
    # so the post-migration 3-part `name@instance@provider` shape stored in
    # `tenant.embd_id` / `llm_id` / etc. resolves through the new tables.
    # The legacy `get_model_config_by_type_and_name` only understands 2-part
    # identifiers and would raise LookupError on the 3-part form.
    try:
        return get_model_config_from_provider_instance(tenant_id, model_type, model_name)
    except LookupError:
        # Workspace tenant's default points at a model whose config isn't in
        # either table for this tenant → fall back to the workspace creator's
        # personal tenant. Same shape (3-part) is tried there.
        fb_tid = _fallback_personal_tenant_id(tenant_id)
        if fb_tid:
            logging.info("Model %s not found on workspace tenant %s, falling back to personal tenant %s", model_name, tenant_id, fb_tid)
            return get_model_config_from_provider_instance(fb_tid, model_type, model_name)
        raise


# ============================================================
# Upstream TenantModel* helpers (new provider/instance/model stack).
# Kept side-by-side with the legacy get_model_config_by_type_and_name path
# above so callers on either route resolve correctly during the refactor.
# ============================================================
def split_model_name(model_name: str):
    # Parse model_name: {model_name} or {model_name}@{factory_name} or {model_name}@{instance_name}@{factory_name}
    parts = model_name.split("@")
    if len(parts) == 1:
        pure_model_name = parts[0]
        provider_name = ""
        instance_name = ""
    elif len(parts) == 2:
        pure_model_name = parts[0]
        provider_name = parts[1]
        instance_name = "default"
    else:
        pure_model_name = parts[0]
        instance_name = parts[1]
        provider_name = parts[2]
    return pure_model_name, instance_name, provider_name


def get_model_config_from_provider_instance(tenant_id, model_type: str|enum.Enum, model_name: str):
    pure_model_name, instance_name, provider_name = split_model_name(model_name)
    model_type_val = model_type if isinstance(model_type, str) else model_type.value
    # Builtin embedding model
    compose_profiles = os.getenv("COMPOSE_PROFILES", "")
    is_tei_builtin_embedding = (
            model_type_val == LLMType.EMBEDDING.value
            and "tei-" in compose_profiles
            and pure_model_name == os.getenv("TEI_MODEL", "")
            and (provider_name == "Builtin" or not provider_name)
    )
    if is_tei_builtin_embedding:
        # configured local embedding model
        embedding_cfg = settings.EMBEDDING_CFG
        return {
            "llm_factory": "Builtin",
            "api_key": embedding_cfg["api_key"],
            "llm_name": pure_model_name,
            "api_base": embedding_cfg["base_url"],
            "model_type": LLMType.EMBEDDING.value,
        }

    # CUSTOM B2B SaaS: when the new provider/instance row is missing for
    # this tenant we fall back to the legacy `tenant_llm` lookup. Upstream's
    # `get_model_config_by_type_and_name` only understands 2-part
    # `name@provider` identifiers, so collapse a 3-part input to 2-part
    # before handing it off — otherwise the legacy path raises LookupError.
    legacy_name = f"{pure_model_name}@{provider_name}" if provider_name else pure_model_name
    provider_obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)
    if not provider_obj:
        return get_model_config_by_type_and_name(tenant_id, model_type, legacy_name)
    instance_obj = TenantModelInstanceService.get_by_provider_id_and_instance_name(provider_obj.id, instance_name)
    if not instance_obj:
        return get_model_config_by_type_and_name(tenant_id, model_type, legacy_name)
    model_obj = TenantModelService.get_by_provider_id_and_instance_id_and_model_type_and_model_name(provider_obj.id, instance_obj.id, model_type_val, pure_model_name)

    api_key, is_tool, api_key_payload = _decode_api_key_config(instance_obj.api_key)
    extra_fields = json.loads(instance_obj.extra) if instance_obj.extra else {}

    # CUSTOM B2B SaaS: LLMBundle / LLM4Tenant downstream still call
    # `TenantLLMService.increase_usage_by_id(self.model_config["id"], ...)`
    # to track tokens against `tenant_llm.used_tokens`. Upstream forgot to
    # include `id` when refactoring this function to return a dict instead
    # of an ORM row. Look up the matching legacy `tenant_llm` row by
    # (tenant, factory, llm_name) so token tracking keeps working. Once
    # upstream rewires the usage path to the new tables we can drop this.
    #
    # Don't go through `TenantLLMService.get_api_key`: in our production K8s
    # env `settings.FACTORY_LLM_INFOS` is None (we never call
    # `init_llm_factory` — by design, see CLAUDE.md), so
    # `split_model_name_and_factory` raises on the 2-part form and the
    # `___<factory>` suffix fallback never fires. The 3-part input format
    # `name@instance@factory` is also mishandled (`@instance` stays glued
    # to the model name). Query the legacy table directly with both
    # candidate names (some rows store the bare name, others the
    # `name___<factory>` form depending on when they were inserted).
    _TLLM = TenantLLMService.model
    candidate_names = [pure_model_name]
    if provider_name:
        candidate_names.append(f"{pure_model_name}___{provider_name}")
    legacy_q = _TLLM.select().where(
        (_TLLM.tenant_id == tenant_id)
        & (_TLLM.model_type == model_type_val)
        & (_TLLM.llm_name.in_(candidate_names))
    )
    if provider_name:
        legacy_q = legacy_q.where(_TLLM.llm_factory == provider_name)
    legacy_row = legacy_q.first()
    legacy_id = legacy_row.id if legacy_row else None

    if model_obj:
        if model_obj.status == ActiveStatusEnum.INACTIVE.value:
            raise LookupError(f"Model {model_name} is disabled.")
        if model_obj.status == ActiveStatusEnum.UNSUPPORTED.value:
            raise LookupError(f"Model {model_name} cannot be used as {model_type_val} model.")

        model_extra = json.loads(model_obj.extra) if model_obj.extra else {}
        model_config = {
            "id": legacy_id,
            "llm_factory": provider_obj.provider_name,
            "api_key": api_key,
            "llm_name": model_obj.model_name,
            "api_base": extra_fields.get("base_url", ""),
            "model_type": model_obj.model_type,
            "is_tools": model_extra.get("is_tools", is_tool),
            "max_tokens": model_extra.get("max_tokens", 8192),
        }
        if api_key_payload is not None:
            model_config["api_key_payload"] = api_key_payload

        return model_config
    else:
        region = extra_fields.get("region", "default")
        if region == "intl" and provider_name.lower() == "siliconflow":
            target_factory_name = "siliconflow_intl"
        else:
            target_factory_name = provider_name
        fac_list = [f for f in settings.FACTORY_LLM_INFOS if f["name"] == target_factory_name]
        if not fac_list:
            raise LookupError(f"Model provider config not found: {provider_name}")
        llm_list = [llm for llm in fac_list[0]["llm"] if llm["llm_name"] == pure_model_name]
        if not llm_list:
            raise LookupError(f"Model config not found: {model_name}")
        llm_info = llm_list[0]
        if model_type_val not in _factory_model_types(llm_info):
            raise LookupError(f"Model {model_name} is not a {model_type_val} model.")
        model_config = {
            "id": legacy_id,
            "llm_factory": provider_obj.provider_name,
            "api_key": api_key,
            "llm_name": llm_info["llm_name"],
            "api_base": extra_fields.get("base_url", ""),
            "model_type": model_type_val,
            "is_tools": llm_info.get("is_tools", is_tool),
            "max_tokens": llm_info.get("max_tokens", 8192),
        }
        if api_key_payload is not None:
            model_config["api_key_payload"] = api_key_payload
        return model_config


def get_api_key(tenant_id: str, model_name: str):
    _, instance_name, provider_name = split_model_name(model_name)

    if not provider_name:
        raise LookupError("Provider name is required.")
    provider_obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)
    if not provider_obj:
        raise LookupError(f"Provider {provider_name} not found.")
    instance_obj = TenantModelInstanceService.get_by_provider_id_and_instance_name(provider_obj.id, instance_name)
    if not instance_obj:
        raise LookupError(f"Instance {instance_name} not found.")
    return instance_obj.api_key


def get_model_type_by_name(tenant_id: str, model_name: str):
    pure_model_name, instance_name, provider_name = split_model_name(model_name)
    provider_obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)
    if not provider_obj:
        raise LookupError(f"Provider {provider_name} not found for model {model_name}.")
    instance_obj = TenantModelInstanceService.get_by_provider_id_and_instance_name(provider_obj.id, instance_name)
    if not instance_obj:
        raise LookupError(f"Instance {instance_name} not found for model {model_name}.")
    model_objs = TenantModelService.get_by_provider_id_and_instance_id_and_model_name(provider_obj.id, instance_obj.id, pure_model_name)
    types_in_json = []
    if not model_objs:
        extra_fields = json.loads(instance_obj.extra) if instance_obj.extra else {}
        region = extra_fields.get("region", "default")
        if region == "intl" and provider_name.lower() == "siliconflow":
            target_factory_name = "siliconflow_intl"
        else:
            target_factory_name = provider_name
        fac_list = [f for f in settings.FACTORY_LLM_INFOS if f["name"] == target_factory_name]
        if not fac_list:
            raise LookupError(f"Model provider config not found: {provider_name}")
        llm_list = [llm for llm in fac_list[0]["llm"] if llm["llm_name"] == pure_model_name]
        if not llm_list:
            raise LookupError(f"Model {pure_model_name} not found for model {model_name}.")
        types_in_json = _factory_model_types(llm_list[0])
    return list(set(types_in_json + [model_obj.model_type for model_obj in model_objs if model_obj.status != ActiveStatusEnum.UNSUPPORTED.value]) - {model_obj.model_type for model_obj in model_objs if model_obj.status == ActiveStatusEnum.UNSUPPORTED.value})


def delete_models_by_instance_ids(instance_ids: list[str]):
    return TenantModelService.delete_by_instance_ids(instance_ids)


def delete_instances_by_provider_ids(provider_ids: list[str]):
    return TenantModelInstanceService.delete_by_provider_ids(provider_ids)


def ensure_opendataloader_from_env(tenant_id: str) -> str | None:
    return _ensure_ocr_provider_from_env(
        tenant_id,
        "OpenDataLoader",
        "opendataloader-from-env",
        _collect_env_config(OPENDATALOADER_ENV_KEYS, OPENDATALOADER_DEFAULT_CONFIG),
    )


def get_models_by_tenant_and_provider_and_model_type(tenant_id: str, provider_name: str, model_type: str):
    """
    Query TenantModel records by tenant_id, provider_name and model_name.
    Returns all matching model records under all instances of the specified provider.
    """
    provider_obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)
    if not provider_obj:
        return []
    instances = TenantModelInstanceService.get_all_by_provider_id(provider_obj.id)
    if not instances:
        return []
    results = []
    for inst in instances:
        models = TenantModelService.get_by_provider_id_and_instance_id_and_model_type(provider_obj.id, inst.id, model_type)
        supported = [model for model in models if model.status != ActiveStatusEnum.UNSUPPORTED.value]
        if supported:
            results.extend(supported)
    return results
