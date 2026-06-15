"""
Dual-write helper: keep upstream's `tenant_model_provider/instance/model`
tables in sync with our admin panel's writes to the legacy `tenant_llm`.

Background
----------
Upstream 2026-06-02 introduced a 3-level model storage hierarchy:
  tenant_model_provider (per-tenant per-provider, no creds)
    └── tenant_model_instance (per-instance creds: api_key, base_url)
          └── tenant_model (per-model availability + status)

Inference code still reads `tenant_llm` (via `TenantLLMService.get_api_key`)
because the joint service falls back there. UI listings (/v1/models,
/v1/models/default) read the NEW tables exclusively. So both must agree.

This module provides a single function `sync_tenant_llm_to_new_tables` that
our admin panel calls after every mutation. The function is idempotent:
existing rows are updated, missing ones are inserted, removed rows are
deleted. The new tables are derived deterministically from `tenant_llm`.

Why not write only to new tables and read tenant_llm via a join?
- Upstream's `get_api_key` is heavily called and hard-codes `tenant_llm`.
- Until upstream stops using tenant_llm, deviating from it breaks inference.

Why not write only to tenant_llm and let a cron sync the new tables?
- Lag between admin write and UI refresh leaks bugs ("I just added a model
  but it doesn't show up"). Synchronous dual-write avoids it.
"""
import json
import logging
import time
import uuid

from api.db.db_models import DB
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.services.tenant_model_provider_service import TenantModelProviderService
from api.db.services.tenant_model_instance_service import TenantModelInstanceService
from api.db.services.tenant_model_service import TenantModelService

logger = logging.getLogger(__name__)

# Mirror upstream `add_model_to_instance` convention: tenant_model.model_name
# holds the bare model name (e.g. "gpt-oss-120b"), NOT the legacy
# `tenant_llm.llm_name` form that appends a factory suffix ("___OpenAI-API",
# "___VLLM", …) — the suffix is a tenant_llm-only quirk that the chat
# dropdown strips and the new lookup path (get_model_config_from_provider_
# instance) doesn't expect.
_FACTORY_SUFFIX = {
    "OpenAI-API-Compatible": "___OpenAI-API",
    "VLLM": "___VLLM",
    "LocalAI": "___LocalAI",
    "HuggingFace": "___HuggingFace",
}


def _bare_model_name(factory: str, llm_name: str) -> str:
    suffix = _FACTORY_SUFFIX.get(factory, "")
    if suffix and llm_name.endswith(suffix):
        return llm_name[: -len(suffix)]
    return llm_name


def _gen_id() -> str:
    return uuid.uuid1().hex


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ensure_provider(tenant_id: str, provider_name: str):
    """Get or create the (tenant_id, provider_name) row. Returns the row."""
    obj = TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)
    if obj:
        return obj
    row = {
        "id": _gen_id(),
        "tenant_id": tenant_id,
        "provider_name": provider_name,
    }
    TenantModelProviderService.save(**row)
    return TenantModelProviderService.get_by_tenant_id_and_provider_name(tenant_id, provider_name)


def _ensure_instance(provider_id: str, api_key: str, extra_json: str = "{}"):
    """Get or create the default instance for this provider, refresh api_key.

    Our admin panel models everything as a single 'default' instance per
    provider — upstream supports multiple but our UI doesn't expose that yet.
    """
    obj = TenantModelInstanceService.get_by_provider_id_and_instance_name(provider_id, "default")
    if obj:
        # Refresh api_key + extra if they drifted (e.g. admin changed the key).
        TenantModelInstanceService.filter_update(
            [TenantModelInstanceService.model.id == obj.id],
            {"api_key": api_key, "extra": extra_json},
        )
        return TenantModelInstanceService.get_by_provider_id_and_instance_name(provider_id, "default")
    TenantModelInstanceService.create_instance(provider_id, "default", api_key, extra_json)
    return TenantModelInstanceService.get_by_provider_id_and_instance_name(provider_id, "default")


def _upsert_model(provider_id: str, instance_id: str, model_name: str, model_type: str, status: str):
    """Upsert one tenant_model row keyed by (provider, instance, type, name)."""
    obj = TenantModelService.get_by_provider_id_and_instance_id_and_model_type_and_model_name(
        provider_id, instance_id, model_type, model_name
    )
    if obj:
        if obj.status != status:
            TenantModelService.filter_update(
                [TenantModelService.model.id == obj.id],
                {"status": status},
            )
        return obj
    row = {
        "id": _gen_id(),
        "provider_id": provider_id,
        "instance_id": instance_id,
        "model_name": model_name,
        "model_type": model_type,
        "status": status,
    }
    TenantModelService.save(**row)
    return TenantModelService.get_by_provider_id_and_instance_id_and_model_type_and_model_name(
        provider_id, instance_id, model_type, model_name
    )


@DB.connection_context()
def sync_tenant_llm_to_new_tables(tenant_id: str, llm_factory: str | None = None) -> None:
    """Reflect `tenant_llm` state into the new tables for one tenant.

    If `llm_factory` is given, only that provider is synced (cheaper after a
    single-model add). Otherwise the whole tenant is re-synced (used on
    delete / bulk operations).

    The new tables NEVER contain rows that don't exist in `tenant_llm` for
    this tenant — orphans are deleted. This makes the function safe to call
    after any mutation.
    """
    query_kwargs = {"tenant_id": tenant_id}
    if llm_factory:
        query_kwargs["llm_factory"] = llm_factory
    llm_rows = TenantLLMService.query(**query_kwargs)

    # Group by factory: each unique (tenant, factory) becomes one provider.
    by_factory: dict[str, list] = {}
    for row in llm_rows:
        by_factory.setdefault(row.llm_factory, []).append(row)

    for factory, rows in by_factory.items():
        # api_key is identical across rows of the same (tenant, factory) in
        # our model — the admin panel writes the same key everywhere.
        api_key = rows[0].api_key or "x"
        base_url = rows[0].api_base or ""
        extra_json = json.dumps({"base_url": base_url}) if base_url else "{}"

        provider = _ensure_provider(tenant_id, factory)
        instance = _ensure_instance(provider.id, api_key, extra_json)

        # Active/inactive: tenant_llm.status == "1" → active, else inactive.
        # The model_name written here is the BARE name (suffix stripped) to
        # match upstream's add_model_to_instance convention. tenant_llm keeps
        # the suffixed form for legacy compat (get_api_key knows the fallback).
        desired_model_keys = set()
        for row in rows:
            status = "active" if (row.status or "1") == "1" else "inactive"
            bare_name = _bare_model_name(factory, row.llm_name)
            _upsert_model(provider.id, instance.id, bare_name, row.model_type, status)
            desired_model_keys.add((bare_name, row.model_type))

        # Prune tenant_model rows that no longer exist in tenant_llm for
        # this (provider, instance). Otherwise deleted-from-admin models
        # would keep surfacing via the new endpoints.
        existing = TenantModelService.get_models_by_provider_ids_and_instance_ids(
            [provider.id], [instance.id]
        )
        for m in existing:
            if (m.model_name, m.model_type) not in desired_model_keys:
                TenantModelService.delete_by_id(m.id)

    # If a whole-tenant sync was requested, also prune providers/instances
    # that no longer have any tenant_llm rows for this tenant.
    if not llm_factory:
        live_factories = {r.llm_factory for r in TenantLLMService.query(tenant_id=tenant_id)}
        for p in TenantModelProviderService.get_by_tenant_id(tenant_id):
            if p.provider_name not in live_factories:
                # Delete cascade: models → instances → provider.
                instances = TenantModelInstanceService.get_all_by_provider_id(p.id)
                inst_ids = [i.id for i in instances]
                if inst_ids:
                    TenantModelService.delete_by_instance_ids(inst_ids)
                    TenantModelInstanceService.delete_by_provider_ids([p.id])
                TenantModelProviderService.delete_by_tenant_id_and_provider_name(tenant_id, p.provider_name)
