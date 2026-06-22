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


def _check_internal_secret() -> tuple[bool, str]:
    """Return (ok, error_message). Compare header against env var in
    constant time to avoid timing leaks."""
    expected = os.environ.get("INTERNAL_API_SECRET", "")
    if not expected:
        return False, "INTERNAL_API_SECRET not configured on this server"
    provided = request.headers.get("X-Internal-Secret", "")
    if not provided:
        return False, "Missing X-Internal-Secret header"
    # hmac.compare_digest is constant-time; secrets.compare_digest is an alias
    import hmac
    if not hmac.compare_digest(expected, provided):
        return False, "Invalid X-Internal-Secret"
    return True, ""


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
