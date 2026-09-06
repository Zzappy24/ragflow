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
# =============================================================================
# CUSTOM B2B SaaS — service-to-service internal endpoints.
#
# These routes are called by sibling services running in the same K8s cluster
# (currently the management backend) when those services don't ship the heavy
# rag.llm stack themselves. Auth is by shared secret (INTERNAL_API_SECRET env
# var) NOT by user JWT, because the caller is a service account, not a logged-
# in user.
#
# Endpoint surface intentionally minimal — add a new route only if the
# alternative is for a sibling service to embed a multi-GB ML dependency just
# to call one function. Every endpoint here MUST validate the shared secret.
# =============================================================================
import asyncio
import logging
import os

from quart import request

from api.utils.api_utils import get_error_data_result, get_result
# CUSTOM B2B SaaS — helper partagé avec user_api.py (bridge/invite prepare),
# qui ne vérifiait rien jusqu'au 2026-09-06. Une seule implémentation.
from api.utils.internal_secret import check_internal_secret as _check_internal_secret
from common.constants import RetCode


@manager.route("/internal/llm/verify", methods=["POST"])  # noqa: F821
async def internal_llm_verify():
    """Test that a model endpoint is reachable and the credentials work.

    Service-to-service endpoint called by the slim management backend image
    which doesn't ship rag.llm itself. Mirrors the logic that used to live
    in management/server/routers/models.py::verify_workspace_model so that
    splitting the backend into two images doesn't break the verify feature.

    Auth: shared secret via X-Internal-Secret header.
    Body: {llm_factory, llm_name, api_key, api_base, model_type}
    Returns: {ok: bool, message: str}
    """
    ok, err = _check_internal_secret()
    if not ok:
        return get_error_data_result(message=err, code=401)

    body = await request.get_json(silent=True) or {}
    llm_factory = body.get("llm_factory")
    llm_name = body.get("llm_name")
    api_key = body.get("api_key") or "x"
    api_base = body.get("api_base") or ""
    model_type = body.get("model_type")

    if not llm_factory or not llm_name or not model_type:
        return get_error_data_result(
            message="llm_factory, llm_name, and model_type are required",
        )

    # Import here so the route module stays import-cheap and any
    # ImportError on rag.llm surfaces inside the route (not at app boot).
    try:
        from rag.llm import (
            EmbeddingModel,
            ChatModel,
            RerankModel,
            CvModel,
            TTSModel,
            Seq2txtModel,
        )
        from common.constants import LLMType
    except ImportError as e:
        logging.exception("rag.llm unavailable on this server")
        return get_error_data_result(message=f"verify backend unavailable: {e}")

    try:
        if model_type == LLMType.EMBEDDING.value:
            mdl = EmbeddingModel[llm_factory](api_key, llm_name, base_url=api_base)
            await asyncio.to_thread(mdl.encode, ["Test if the api key is available"])

        elif model_type == LLMType.RERANK.value:
            mdl = RerankModel[llm_factory](api_key, llm_name, base_url=api_base)
            await asyncio.to_thread(
                mdl.similarity, "What is RAGFlow?", ["RAGFlow is a RAG engine."]
            )

        elif model_type == LLMType.IMAGE2TEXT.value:
            from rag.utils.base64_image import test_image
            mdl = CvModel[llm_factory](api_key, llm_name, base_url=api_base)
            await asyncio.to_thread(mdl.describe, test_image)

        elif model_type == LLMType.TTS.value:
            mdl = TTSModel[llm_factory](api_key, llm_name, base_url=api_base)
            for _ in mdl.tts("Test"):
                break

        elif model_type == LLMType.SPEECH2TEXT.value:
            # RAGFlow itself has no verify for ASR — skip actual test
            pass

        else:
            # Default: chat — mirror api/apps/llm_app.py::set_api_key.
            # async_chat_streamly is the canonical chat entry point on Base.
            mdl = ChatModel[llm_factory](api_key, llm_name, base_url=api_base)
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
                return get_result(data={
                    "ok": False,
                    "message": (
                        f"Timeout after {timeout_s}s — endpoint unreachable "
                        "or model not warmed up."
                    ),
                })
            if error_text:
                return get_result(data={"ok": False, "message": error_text})
            if not received_chunk:
                return get_result(data={"ok": False, "message": "No response received from model."})

        return get_result(data={"ok": True, "message": "Connection successful"})

    except Exception as e:
        logging.exception("verify failed")
        return get_result(data={"ok": False, "message": str(e)})


# =============================================================================
# CIA-9 — Infinity storage usage per tenant/KB (service-to-service).
#
# The slim management image does not ship the infinity SDK. The actual scan
# lives in api/db/services/infinity_storage_scan.py (neutral module, shared
# with the upload quota enforcement in quota_service); this route only adds
# the X-Internal-Secret auth + TTL cache + thread offload.
# =============================================================================
import time as _time

from api.db.services import infinity_storage_scan as _storage_scan


@manager.route("/internal/storage/infinity", methods=["GET"])  # noqa: F821
async def internal_infinity_storage():
    """Per-tenant/KB Infinity storage usage. Auth: X-Internal-Secret."""
    ok, err = _check_internal_secret()
    if not ok:
        return get_error_data_result(message=err, code=401)

    ttl = int(os.environ.get("INTERNAL_STORAGE_SCAN_TTL", "900"))
    force = request.args.get("refresh") == "1"
    if not force:
        cached = _storage_scan.get_cached(max_age_s=ttl)
        if cached is not None:
            return get_result(data=cached)

    try:
        data = await asyncio.to_thread(_storage_scan.scan)
    except Exception as e:
        logging.exception("infinity storage scan failed")
        return get_error_data_result(message=f"scan failed: {e}")
    return get_result(data=data)


@manager.route("/internal/workspaces/<tenant_id>/purge-data", methods=["POST"])  # noqa: F821
async def internal_purge_workspace_data(tenant_id: str):
    """CUSTOM B2B SaaS — purge physique du contenu d'un tenant de workspace.

    Appelée par le panel (image slim, sans client ES ni MinIO) AVANT qu'il
    n'efface les lignes de structure. Refuse un workspace encore actif (409)
    ou inconnu (404) ; toute erreur physique remonte en 500 pour que le
    panel n'efface rien et que l'admin relance. Cf.
    api/db/joint_services/workspace_purge_service.py.
    """
    ok, err = _check_internal_secret()
    if not ok:
        return get_error_data_result(message=err, code=RetCode.AUTHENTICATION_ERROR)
    from api.db.joint_services.workspace_purge_service import PurgeRefused, purge_tenant_content
    from common.misc_utils import thread_pool_exec

    try:
        summary = await thread_pool_exec(purge_tenant_content, tenant_id)
    except PurgeRefused as e:
        code = RetCode.NOT_FOUND if "no workspace" in str(e) else RetCode.CONFLICT
        return get_error_data_result(message=str(e), code=code)
    except Exception as e:
        logging.exception("internal purge of tenant %s failed", tenant_id)
        return get_error_data_result(message=f"purge failed: {e}", code=RetCode.SERVER_ERROR)
    return get_result(data=summary)
