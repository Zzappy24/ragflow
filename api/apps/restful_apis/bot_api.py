#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
import copy
import json
import re
import time
import os
import tempfile

import logging

from quart import Response, request

from agent.canvas import Canvas
from api.apps import AUTH_BETA, login_required
from api.db.services.api_service import API4ConversationService
from api.db.services.canvas_service import UserCanvasService, completion_openai
from api.db.services.user_canvas_version import UserCanvasVersionService
from api.db.services.canvas_service import completion as agent_completion
from api.db.services.conversation_service import async_iframe_completion as iframe_completion
from api.db.services.dialog_service import DialogService, async_ask, gen_mindmap
from api.db.services.doc_metadata_service import DocMetadataService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from api.db.services.user_service import TenantService
from common.metadata_utils import apply_meta_data_filter
from api.db.services.search_service import SearchService
from api.db.services.user_service import UserTenantService
from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type, get_model_config_from_provider_instance
from common.misc_utils import get_uuid, thread_pool_exec
from api.utils.api_utils import check_duplicate_ids, get_data_openai, get_error_data_result, get_json_result, \
    add_tenant_id_to_kwargs, get_result, get_request_json, server_error_response, token_required, validate_request
from rag.app.tag import label_question
from rag.prompts.template import load_prompt
from rag.prompts.generator import chunks_format, cross_languages, keyword_extraction
from common.constants import RetCode, LLMType, StatusEnum
from common import settings
from api.apps.extensions.rbac import require_permission, Permission
from api.utils.reference_metadata_utils import (
    enrich_chunks_with_document_metadata,
    resolve_reference_metadata_preferences,
)

logger = logging.getLogger(__name__)


def _get_sdk_authorization_token():
    token = request.headers.get("Authorization", "").split()
    if len(token) != 2:
        return None
    return token[1]


@token_required
@require_permission(Permission.CHAT_USE)
async def create_agent_session(tenant_id, agent_id):
    req = await get_request_json()
    user_id = req.get("user_id") or request.args.get("user_id", tenant_id)
    release_mode = bool(req.get("release", request.args.get("release", False)))

    if not await thread_pool_exec(UserCanvasService.query, user_id=tenant_id, id=agent_id):
        return get_error_data_result("You cannot access the agent.")

    try:
        cvs, dsl = await thread_pool_exec(UserCanvasService.get_agent_dsl_with_release, agent_id, release_mode, tenant_id)
    except LookupError:
        return get_error_data_result("Agent not found.")
    except PermissionError as e:
        return get_error_data_result(str(e))

    session_id = get_uuid()
    canvas = Canvas(dsl, tenant_id, agent_id, canvas_id=cvs.id)
    canvas.reset()

    cvs.dsl = json.loads(str(canvas))
    # Get the version title based on release_mode
    version_title = await thread_pool_exec(UserCanvasVersionService.get_latest_version_title, cvs.id, release_mode=release_mode)
    conv = {
        "id": session_id,
        "dialog_id": cvs.id,
        "user_id": user_id,
        "message": [{"role": "assistant", "content": canvas.get_prologue()}],
        "source": "agent",
        "dsl": cvs.dsl,
        "version_title": version_title
    }
    await thread_pool_exec(API4ConversationService.save, **conv)
    conv["agent_id"] = conv.pop("dialog_id")
    return get_result(data=conv)


@manager.route("/agents/<agent_id>/sessions", methods=["DELETE"])  # noqa: F821
@token_required
@require_permission(Permission.CHAT_DELETE)
async def delete_agent_session(tenant_id, agent_id):
    errors = []
    success_count = 0
    req = await get_request_json()
    cvs = await thread_pool_exec(UserCanvasService.query, user_id=tenant_id, id=agent_id)
    if not cvs:
        return get_error_data_result(f"You don't own the agent {agent_id}")

    if not req:
        return get_result()

    ids = req.get("ids")
    if not ids:
        if req.get("delete_all") is True:
            # CUSTOM B2B SaaS: in API-key context tenant_id == user_id — scope delete_all to token owner's sessions only
            ids = [conv.id for conv in await thread_pool_exec(API4ConversationService.query, dialog_id=agent_id, user_id=tenant_id)]
            if not ids:
                return get_result()
        else:
            return get_result()

    conv_list = ids

    unique_conv_ids, duplicate_messages = check_duplicate_ids(conv_list, "session")
    conv_list = unique_conv_ids

    for session_id in conv_list:
        # CUSTOM B2B SaaS: verify session belongs to this agent AND to the token owner
        conv = await thread_pool_exec(API4ConversationService.query, id=session_id, dialog_id=agent_id, user_id=tenant_id)
        if not conv:
            errors.append(f"The agent doesn't own the session {session_id}")
            continue
        await thread_pool_exec(API4ConversationService.delete_by_id, session_id)
        success_count += 1

    if errors:
        if success_count > 0:
            return get_result(data={"success_count": success_count, "errors": errors},
                              message=f"Partially deleted {success_count} sessions with {len(errors)} errors")
        else:
            return get_error_data_result(message="; ".join(errors))

    if duplicate_messages:
        if success_count > 0:
            return get_result(
                message=f"Partially deleted {success_count} sessions with {len(duplicate_messages)} errors",
                data={"success_count": success_count, "errors": duplicate_messages})
        else:
            return get_error_data_result(message=";".join(duplicate_messages))

    return get_result()


@manager.route("/chatbots/<dialog_id>/completions", methods=["POST"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
async def chatbot_completions(dialog_id, tenant_id=None):
    req = await get_request_json()

    exists, dialog = DialogService.get_by_id(dialog_id)
    if (not exists
            or getattr(dialog, "tenant_id", None) != tenant_id
            or str(getattr(dialog, "status", "")) != StatusEnum.VALID.value):
        logger.warning(
            "Denied chatbot access: reason=%s tenant_id=%s dialog_id=%s user_id=%s session_id=%s",
            "no access to this chatbot",
            tenant_id,
            dialog_id,
            req.get("user_id"),
            req.get("session_id"),
        )
        return get_error_data_result(message="Authentication error: no access to this chatbot!")
    # CUSTOM B2B SaaS — un jeton beta frappé pour un chatbot précis ne sert
    # que lui : embarquer la FAQ publique ne doit pas ouvrir le bot RH du même
    # workspace (audit 2026-09-06, cf. api/utils/beta_scope.py).
    from api.utils.beta_scope import beta_denies
    if beta_denies(dialog_id):
        return get_error_data_result(message="Authentication error: no access to this chatbot!")

    if "quote" not in req:
        req["quote"] = False

    def _validate_iframe_access():
        if req.get("session_id"):
            exists, conv = API4ConversationService.get_by_id(req.get("session_id"))
            if not exists:
                raise AssertionError("Session not found!")
            if conv.dialog_id != dialog_id:
                raise AssertionError("Session does not belong to this dialog")
            if tenant_id and conv.user_id and conv.user_id != tenant_id:
                raise AssertionError("Session does not belong to this tenant")

    if req.get("stream", True):
        try:
            _validate_iframe_access()
        except AssertionError:
            logger.warning(
                "Denied chatbot completion stream: reason=%s tenant_id=%s dialog_id=%s user_id=%s session_id=%s",
                "no access to this chatbot",
                tenant_id,
                dialog_id,
                req.get("user_id"),
                req.get("session_id"),
            )
            return get_error_data_result(message="Authentication error: no access to this chatbot!")

        resp = Response(iframe_completion(dialog_id, tenant_id=tenant_id, **req), mimetype="text/event-stream")
        resp.headers.add_header("Cache-control", "no-cache")
        resp.headers.add_header("Connection", "keep-alive")
        resp.headers.add_header("X-Accel-Buffering", "no")
        resp.headers.add_header("Content-Type", "text/event-stream; charset=utf-8")
        return resp

    try:
        _validate_iframe_access()
        async for answer in iframe_completion(dialog_id, tenant_id=tenant_id, **req):
            return get_result(data=answer)
    except AssertionError:
        logger.warning(
            "Denied chatbot completion: reason=%s tenant_id=%s dialog_id=%s user_id=%s session_id=%s",
            "no access to this chatbot",
            tenant_id,
            dialog_id,
            req.get("user_id"),
            req.get("session_id"),
        )
        return get_error_data_result(message="Authentication error: no access to this chatbot!")

    return None

@manager.route("/chatbots/<dialog_id>/info", methods=["GET"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
async def chatbots_inputs(dialog_id, tenant_id=None):
    exists, dialog = await thread_pool_exec(DialogService.get_by_id, dialog_id)
    if (not exists
            or getattr(dialog, "tenant_id", None) != tenant_id
            or str(getattr(dialog, "status", "")) != StatusEnum.VALID.value):
        request_args = getattr(request, "args", {}) or {}
        request_user_id = request_args.get("user_id") if hasattr(request_args, "get") else None
        request_session_id = request_args.get("session_id") if hasattr(request_args, "get") else None
        logger.warning(
            "Denied chatbot access: reason=%s tenant_id=%s dialog_id=%s user_id=%s session_id=%s",
            "no access to this chatbot",
            tenant_id,
            dialog_id,
            request_user_id,
            request_session_id,
        )
        return get_error_data_result(message="Authentication error: no access to this chatbot!")
    return get_result(
        data={
            "title": dialog.name,
            "avatar": dialog.icon,
            "prologue": dialog.prompt_config.get("prologue", ""),
            "has_tavily_key": bool(dialog.prompt_config.get("tavily_api_key", "").strip()),
            "llm_id": dialog.llm_id or "",
        }
    )


@manager.route("/agentbots/<agent_id>/completions", methods=["POST"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
async def agent_bot_completions(agent_id, tenant_id=None):
    req = await get_request_json()
    # CUSTOM B2B SaaS — l'agent doit appartenir au tenant du jeton (un jeton
    # beta public lançait N'IMPORTE QUEL agent de la plateforme : outils SQL,
    # SMTP, HTTP avec leurs credentials stockés — audit 2026-09-06), et un
    # jeton frappé pour un agent précis ne sert que lui.
    from api.utils.beta_scope import beta_denies
    if beta_denies(agent_id) or not await thread_pool_exec(UserCanvasService.accessible, agent_id, tenant_id):
        return get_error_data_result(message="Authentication error: no access to this agent!")

    if req.get("stream", True):
        async def stream():
            try:
                async for answer in agent_completion(tenant_id, agent_id, **req):
                    yield answer
            except Exception as e:
                logging.exception(e)
                error_result = get_error_data_result(message=str(e) or "Unknown error")
                yield "data:" + json.dumps(
                    {
                        "event": "message",
                        "data": {"content": f"Error {error_result['code']}: {error_result['message']}\n\n"},
                        **error_result,
                    },
                    ensure_ascii=False,
                ) + "\n\n"

        resp = Response(stream(), mimetype="text/event-stream")
        resp.headers.add_header("Cache-control", "no-cache")
        resp.headers.add_header("Connection", "keep-alive")
        resp.headers.add_header("X-Accel-Buffering", "no")
        resp.headers.add_header("Content-Type", "text/event-stream; charset=utf-8")
        return resp

    try:
        full_content = ""
        reference = {}
        structured_output = {}
        final_ans = {}
        async for answer in agent_completion(tenant_id, agent_id, **req):
            # agent_completion yields SSE-formatted strings. A single yielded
            # chunk can contain multiple "data:..." frames separated by "\n\n"
            # plus blank or comment lines, so parse line-by-line rather than
            # assuming one frame per chunk.
            if not isinstance(answer, str):
                continue
            for line in answer.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    ans = json.loads(payload)
                except Exception as e:
                    logging.debug("agent_bot_completions: skipping malformed SSE frame: %s", e)
                    continue
                event = ans.get("event")
                if event == "message":
                    full_content += ans.get("data", {}).get("content", "") or ""
                if ans.get("data", {}).get("reference"):
                    reference.update(ans["data"]["reference"])
                if event == "node_finished":
                    data = ans.get("data", {})
                    node_out = data.get("outputs") or {}
                    component_id = data.get("component_id")
                    if component_id is not None and "structured" in node_out:
                        structured_output[component_id] = copy.deepcopy(node_out["structured"])
                final_ans = ans

        if not final_ans:
            return get_result(data={})

        if "data" not in final_ans or not isinstance(final_ans["data"], dict):
            final_ans["data"] = {}
        final_ans["data"]["content"] = full_content
        final_ans["data"]["reference"] = reference
        if structured_output:
            final_ans["data"]["structured"] = structured_output
        return get_result(data=final_ans)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message=str(e) or "Unknown error")


@manager.route("/agentbots/<agent_id>/inputs", methods=["GET"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
async def begin_inputs(agent_id, tenant_id=None):
    # CUSTOM B2B SaaS — même scope que agent_bot_completions (audit 2026-09-06).
    from api.utils.beta_scope import beta_denies
    if beta_denies(agent_id) or not await thread_pool_exec(UserCanvasService.accessible, agent_id, tenant_id):
        return get_error_data_result(f"Can't find agent by ID: {agent_id}")
    e, cvs = await thread_pool_exec(UserCanvasService.get_by_id, agent_id)
    if not e:
        return get_error_data_result(f"Can't find agent by ID: {agent_id}")

    canvas = Canvas(json.dumps(cvs.dsl), tenant_id, canvas_id=cvs.id)
    return get_result(
        data={"title": cvs.title, "avatar": cvs.avatar, "inputs": canvas.get_component_input_form("begin"),
              "prologue": canvas.get_prologue(), "mode": canvas.get_mode()})


@manager.route("/searchbots/ask", methods=["POST"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
@validate_request("question", "kb_ids")
async def ask_about_embedded(tenant_id=None):
    req = await get_request_json()
    uid = tenant_id

    # CUSTOM B2B SaaS — kb_ids et search_id fournis par le client : chaque base
    # doit appartenir au tenant du jeton et rester dans le périmètre de l'objet
    # lié au jeton ; la search app aussi. Sans cela, un jeton beta public lisait
    # les chunks de N'IMPORTE QUELLE base de la plateforme (audit 2026-09-06).
    from api.utils.beta_scope import beta_allowed_kb_ids, beta_denies, kb_ids_owned_by
    search_id = req.get("search_id", "")
    search_config = {}
    if search_id:
        if beta_denies(search_id) or not await thread_pool_exec(SearchService.query, id=search_id, tenant_id=uid):
            return get_error_data_result(message="Authentication error: no access to this search app!")
        if search_app := await thread_pool_exec(SearchService.get_detail, search_id):
            search_config = search_app.get("search_config", {})
    kb_ids = req["kb_ids"] if isinstance(req["kb_ids"], list) else [req["kb_ids"]]
    allowed = await thread_pool_exec(beta_allowed_kb_ids, uid)
    if not await thread_pool_exec(kb_ids_owned_by, uid, kb_ids) or (allowed is not None and not set(kb_ids) <= allowed):
        return get_error_data_result(message="Authentication error: no access to these datasets!")

    chat_llm_name = ""
    if not search_config or not search_config.get("chat_id"):
        _, tenant_info = TenantService.get_by_id(uid)
        chat_llm_name = tenant_info.llm_id

    async def stream():
        nonlocal req, uid
        try:
            async for ans in async_ask(req["question"], kb_ids, uid, chat_llm_name=chat_llm_name, search_config=search_config):
                yield "data:" + json.dumps({"code": 0, "message": "", "data": ans}, ensure_ascii=False) + "\n\n"
        except Exception as e:
            yield "data:" + json.dumps(
                {"code": 500, "message": str(e), "data": {"answer": "**ERROR**: " + str(e), "reference": []}},
                ensure_ascii=False) + "\n\n"
        yield "data:" + json.dumps({"code": 0, "message": "", "data": True}, ensure_ascii=False) + "\n\n"

    resp = Response(stream(), mimetype="text/event-stream")
    resp.headers.add_header("Cache-control", "no-cache")
    resp.headers.add_header("Connection", "keep-alive")
    resp.headers.add_header("X-Accel-Buffering", "no")
    resp.headers.add_header("Content-Type", "text/event-stream; charset=utf-8")
    return resp


@manager.route("/searchbots/retrieval_test", methods=["POST"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
@validate_request("kb_id", "question")
async def retrieval_test_embedded(tenant_id=None):
    req = await get_request_json()
    page = int(req.get("page", 1))
    size = int(req.get("size", 30))
    question = req["question"]
    kb_ids = req["kb_id"]
    if isinstance(kb_ids, str):
        kb_ids = [kb_ids]
    if not kb_ids:
        return get_json_result(data=False, message='Please specify dataset firstly.',
                               code=RetCode.DATA_ERROR)
    doc_ids = req.get("doc_ids", [])
    similarity_threshold = float(req.get("similarity_threshold", 0.0))
    vector_similarity_weight = float(req.get("vector_similarity_weight", 0.3))
    use_kg = req.get("use_kg", False)
    top = int(req.get("top_k", 1024))
    if top <= 0:
        return get_error_data_result("`top_k` must be greater than 0")
    langs = req.get("cross_languages", [])
    rerank_id = req.get("rerank_id", "")
    if not tenant_id:
        return get_error_data_result(message="permission denined.")
    search_config = {}
    # CUSTOM B2B SaaS — la search app doit appartenir au tenant du jeton et
    # rester celle pour laquelle le jeton a été frappé (audit 2026-09-06).
    if req.get("search_id", ""):
        from api.utils.beta_scope import beta_denies
        if beta_denies(req["search_id"]) or not await thread_pool_exec(SearchService.query, id=req["search_id"], tenant_id=tenant_id):
            return get_error_data_result(message="Authentication error: no access to this search app!")

    async def _retrieval():
        nonlocal similarity_threshold, vector_similarity_weight, top, rerank_id, size
        local_doc_ids = list(doc_ids) if doc_ids else []
        tenant_ids = []
        _question = question

        meta_data_filter = {}
        chat_mdl = None
        if req.get("search_id", ""):
            nonlocal search_config
            detail = await thread_pool_exec(SearchService.get_detail, req.get("search_id", ""))
            if detail:
                search_config = detail.get("search_config", {})
                meta_data_filter = search_config.get("meta_data_filter", {})
            if meta_data_filter.get("method") in ["auto", "semi_auto"]:
                chat_id = search_config.get("chat_id", "")
                if chat_id:
                    chat_model_config = await thread_pool_exec(get_model_config_from_provider_instance, tenant_id, LLMType.CHAT, chat_id)
                else:
                    chat_model_config = await thread_pool_exec(get_tenant_default_model_by_type, tenant_id, LLMType.CHAT)
                chat_mdl = LLMBundle(tenant_id, chat_model_config)
            # Apply search_config settings if not explicitly provided in request
            if not req.get("similarity_threshold"):
                similarity_threshold = float(search_config.get("similarity_threshold", similarity_threshold))
            if not req.get("vector_similarity_weight"):
                vector_similarity_weight = float(search_config.get("vector_similarity_weight", vector_similarity_weight))
            if not req.get("top_k"):
                top = int(search_config.get("top_k", top))
            if not req.get("rerank_id"):
                rerank_id = search_config.get("rerank_id", "")
        else:
            meta_data_filter = req.get("meta_data_filter") or {}
            if meta_data_filter.get("method") in ["auto", "semi_auto"]:
                chat_model_config = await thread_pool_exec(get_tenant_default_model_by_type, tenant_id, LLMType.CHAT)
                chat_mdl = LLMBundle(tenant_id, chat_model_config)

        if meta_data_filter:
            local_doc_ids = await apply_meta_data_filter(
                meta_data_filter,
                None,
                _question,
                chat_mdl,
                local_doc_ids,
                kb_ids=kb_ids,
                metas_loader=lambda: DocMetadataService.get_flatted_meta_by_kbs(kb_ids),
            )

        # CUSTOM B2B SaaS — scope strict au tenant du jeton ET au périmètre de
        # l'objet lié (dialog/search app) : l'ancien test passait par
        # UserTenant de l'utilisateur technique, donc TOUT le workspace, et
        # rien ne limitait size/top_k (export de bases entières par un
        # visiteur anonyme — audit 2026-09-06).
        from api.utils.beta_scope import beta_allowed_kb_ids, kb_ids_owned_by
        allowed = await thread_pool_exec(beta_allowed_kb_ids, tenant_id)
        if not await thread_pool_exec(kb_ids_owned_by, tenant_id, kb_ids) or (allowed is not None and not set(kb_ids) <= allowed):
            return get_json_result(data=False, message="Only owner of dataset authorized for this operation.",
                                   code=RetCode.OPERATING_ERROR)
        tenant_ids = [tenant_id]
        size = min(size, 100)      # page servie au visiteur : c'est elle qui permettait l'export
        top = min(top, 1024)       # candidats avant rerank : valeur par défaut, résultats inchangés

        e, kb = await thread_pool_exec(KnowledgebaseService.get_by_id, kb_ids[0])
        if not e:
            return get_error_data_result(message="Knowledgebase not found!")

        if langs:
            _question = await cross_languages(kb.tenant_id, None, _question, langs)
        embd_model_config = await thread_pool_exec(get_model_config_from_provider_instance, kb.tenant_id, LLMType.EMBEDDING, kb.embd_id)
        embd_mdl = LLMBundle(kb.tenant_id, embd_model_config)

        rerank_mdl = None
        if rerank_id:
            rerank_model_config = await thread_pool_exec(get_model_config_from_provider_instance, tenant_id, LLMType.RERANK, rerank_id)
            rerank_mdl = LLMBundle(kb.tenant_id, rerank_model_config)

        if req.get("keyword", False):
            default_chat_model = await thread_pool_exec(get_tenant_default_model_by_type, kb.tenant_id, LLMType.CHAT)
            chat_mdl = LLMBundle(kb.tenant_id, default_chat_model)
            _question += await keyword_extraction(chat_mdl, _question)

        labels = label_question(_question, [kb])
        ranks = await settings.retriever.retrieval(
            _question, embd_mdl, tenant_ids, kb_ids, page, size, similarity_threshold, vector_similarity_weight, top,
            local_doc_ids, rerank_mdl=rerank_mdl, highlight=req.get("highlight"), rank_feature=labels
        )
        if use_kg:
            default_chat_model = await thread_pool_exec(get_tenant_default_model_by_type, kb.tenant_id, LLMType.CHAT)
            ck = await settings.kg_retriever.retrieval(_question, tenant_ids, kb_ids, embd_mdl,
                                                 LLMBundle(kb.tenant_id, default_chat_model))
            if ck["content_with_weight"]:
                ranks["chunks"].insert(0, ck)

        for c in ranks["chunks"]:
            c.pop("vector", None)

        include_metadata, metadata_fields = _resolve_reference_metadata(req, search_config)
        if include_metadata:
            enrich_chunks_with_document_metadata(ranks["chunks"], metadata_fields)

        ranks["labels"] = labels

        return get_json_result(data=ranks)

    try:
        return await _retrieval()
    except Exception as e:
        if "not_found" in str(e):
            return get_json_result(data=False, message="No chunk found! Check the chunk status please!",
                                   code=RetCode.DATA_ERROR)
        return server_error_response(e)


@manager.route("/searchbots/related_questions", methods=["POST"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
@validate_request("question")
async def related_questions_embedded(tenant_id=None):
    req = await get_request_json()
    if not tenant_id:
        return get_error_data_result(message="permission denined.")

    search_id = req.get("search_id", "")
    search_config = {}
    if search_id:
        if search_app := await thread_pool_exec(SearchService.get_detail, search_id):
            search_config = search_app.get("search_config", {})

    question = req["question"]

    chat_id = search_config.get("chat_id", "")
    if chat_id:
        chat_model_config = await thread_pool_exec(get_model_config_from_provider_instance, tenant_id, LLMType.CHAT, chat_id)
    else:
        chat_model_config = await thread_pool_exec(get_tenant_default_model_by_type, tenant_id, LLMType.CHAT)
    chat_mdl = LLMBundle(tenant_id, chat_model_config)

    gen_conf = search_config.get("llm_setting", {"temperature": 0.9})
    prompt = load_prompt("related_question")
    ans = await chat_mdl.async_chat(
        prompt,
        [
            {
                "role": "user",
                "content": f"""
Keywords: {question}
Related search terms:
    """,
            }
        ],
        gen_conf,
    )
    return get_json_result(data=[re.sub(r"^[0-9]\. ", "", a) for a in ans.split("\n") if re.match(r"^[0-9]\. ", a)])


@manager.route("/searchbots/detail", methods=["GET"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
async def detail_share_embedded(tenant_id=None):
    search_id = request.args["search_id"]
    if not tenant_id:
        return get_error_data_result(message="permission denined.")
    try:
        tenants = await thread_pool_exec(UserTenantService.query, user_id=tenant_id)
        for tenant in tenants:
            if await thread_pool_exec(SearchService.query, tenant_id=tenant.tenant_id, id=search_id):
                break
        else:
            return get_json_result(data=False, message="Has no permission for this operation.",
                                   code=RetCode.OPERATING_ERROR)

        search = await thread_pool_exec(SearchService.get_detail, search_id)
        if not search:
            return get_error_data_result(message="Can't find this Search App!")
        return get_json_result(data=search)
    except Exception as e:
        return server_error_response(e)


@manager.route("/searchbots/mindmap", methods=["POST"])  # noqa: F821
@login_required(auth_types=AUTH_BETA)
@add_tenant_id_to_kwargs
@validate_request("question", "kb_ids")
async def mindmap(tenant_id=None):
    req = await get_request_json()

    # CUSTOM B2B SaaS — même scope que ask_about_embedded (audit 2026-09-06).
    from api.utils.beta_scope import beta_allowed_kb_ids, beta_denies, kb_ids_owned_by
    search_id = req.get("search_id", "")
    if search_id and (beta_denies(search_id) or not await thread_pool_exec(SearchService.query, id=search_id, tenant_id=tenant_id)):
        return get_error_data_result(message="Authentication error: no access to this search app!")
    kb_ids = req["kb_ids"] if isinstance(req["kb_ids"], list) else [req["kb_ids"]]
    allowed = await thread_pool_exec(beta_allowed_kb_ids, tenant_id)
    if not await thread_pool_exec(kb_ids_owned_by, tenant_id, kb_ids) or (allowed is not None and not set(kb_ids) <= allowed):
        return get_error_data_result(message="Authentication error: no access to these datasets!")
    search_app = await thread_pool_exec(SearchService.get_detail, search_id) if search_id else {}

    mind_map =await gen_mindmap(req["question"], kb_ids, tenant_id, search_app.get("search_config", {}))
    if "error" in mind_map:
        return server_error_response(Exception(mind_map["error"]))
    return get_json_result(data=mind_map)

@manager.route("/sequence2txt", methods=["POST"])  # noqa: F821
@token_required
@require_permission(Permission.CHAT_USE)
async def sequence2txt(tenant_id):
    req = await request.form
    stream_mode = req.get("stream", "false").lower() == "true"
    files = await request.files
    if "file" not in files:
        return get_error_data_result(message="Missing 'file' in multipart form-data")

    uploaded = files["file"]

    ALLOWED_EXTS = {
        ".wav", ".mp3", ".m4a", ".aac",
        ".flac", ".ogg", ".webm",
        ".opus", ".wma"
    }

    filename = uploaded.filename or ""
    suffix = os.path.splitext(filename)[-1].lower()
    if suffix not in ALLOWED_EXTS:
        return get_error_data_result(message=
            f"Unsupported audio format: {suffix}. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTS))}"
        )
    fd, temp_audio_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    await uploaded.save(temp_audio_path)

    try:
        default_asr_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.SPEECH2TEXT)
    except Exception as e:
        return get_error_data_result(message=str(e))
    asr_mdl=LLMBundle(tenant_id, default_asr_model_config)
    if not stream_mode:
        text = await thread_pool_exec(asr_mdl.transcription, temp_audio_path)
        try:
            os.remove(temp_audio_path)
        except Exception as e:
            logging.error(f"Failed to remove temp audio file: {str(e)}")
        return get_json_result(data={"text": text})
    async def event_stream():
        try:
            for evt in asr_mdl.stream_transcription(temp_audio_path):
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:
            err = {"event": "error", "text": str(e)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        finally:
            try:
                os.remove(temp_audio_path)
            except Exception as e:
                logging.error(f"Failed to remove temp audio file: {str(e)}")

    return Response(event_stream(), content_type="text/event-stream")

@manager.route("/tts", methods=["POST"])  # noqa: F821
@token_required
@require_permission(Permission.CHAT_USE)
async def tts(tenant_id):
    req = await get_request_json()
    text = req["text"]

    try:
        default_tts_model_config = get_tenant_default_model_by_type(tenant_id, LLMType.TTS)
    except Exception as e:
        return get_error_data_result(message=str(e))
    tts_mdl = LLMBundle(tenant_id, default_tts_model_config)

    def stream_audio():
        try:
            for txt in re.split(r"[，。/《》？；：！\n\r:;]+", text):
                for chunk in tts_mdl.tts(txt):
                    yield chunk
        except Exception as e:
            yield ("data:" + json.dumps({"code": 500, "message": str(e), "data": {"answer": "**ERROR**: " + str(e)}}, ensure_ascii=False)).encode("utf-8")

    resp = Response(stream_audio(), mimetype="audio/mpeg")
    resp.headers.add_header("Cache-Control", "no-cache")
    resp.headers.add_header("Connection", "keep-alive")
    resp.headers.add_header("X-Accel-Buffering", "no")

    return resp


def _build_reference_chunks(reference, include_metadata=False, metadata_fields=None):
    chunks = chunks_format(reference)
    if not include_metadata:
        return chunks

    doc_ids_by_kb = {}
    for chunk in chunks:
        kb_id = chunk.get("dataset_id")
        doc_id = chunk.get("document_id")
        if not kb_id or not doc_id:
            continue
        doc_ids_by_kb.setdefault(kb_id, set()).add(doc_id)

    if not doc_ids_by_kb:
        return chunks

    meta_by_doc = {}
    for kb_id, doc_ids in doc_ids_by_kb.items():
        meta_map = DocMetadataService.get_metadata_for_documents(list(doc_ids), kb_id)
        if meta_map:
            meta_by_doc.update(meta_map)

    if metadata_fields is not None:
        metadata_fields = {f for f in metadata_fields if isinstance(f, str)}
        if not metadata_fields:
            return chunks

    for chunk in chunks:
        doc_id = chunk.get("document_id")
        if not doc_id:
            continue
        meta = meta_by_doc.get(doc_id)
        if not meta:
            continue
        if metadata_fields is not None:
            meta = {k: v for k, v in meta.items() if k in metadata_fields}
        if meta:
            chunk["document_metadata"] = meta

    return chunks


def _resolve_reference_metadata(req, search_config=None):
    return resolve_reference_metadata_preferences(req, search_config)
