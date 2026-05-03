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
import datetime
import json
import logging
import pathlib
import re
from io import BytesIO

import xxhash
from peewee import OperationalError
from pydantic import BaseModel, Field, validator
from quart import request, send_file

from api.db import FileType
from api.db.db_models import APIToken, Document, File, Task
from api.db.joint_services.tenant_model_service import get_model_config_by_id, get_model_config_by_type_and_name, get_tenant_default_model_by_type
from api.db.services.doc_metadata_service import DocMetadataService
from api.db.services.document_service import DocumentService
from api.db.services.file2document_service import File2DocumentService
from api.db.services.file_service import FileService
from api.db.services.knowledgebase_service import KnowledgebaseService
from api.db.services.llm_service import LLMBundle
from api.db.services.task_service import TaskService, cancel_all_task_of, queue_tasks
from api.db.services.tenant_llm_service import TenantLLMService
from api.constants import FILE_NAME_LEN_LIMIT
from api.utils.api_utils import check_duplicate_ids, construct_json_result, get_error_data_result, get_parser_config, get_request_json, get_result, server_error_response, token_required
from api.utils.image_utils import store_chunk_image
from common import settings
from common.constants import FileSource, LLMType, ParserType, RetCode, TaskStatus
from common.metadata_utils import convert_conditions, meta_filter
from common.misc_utils import thread_pool_exec
from common.string_utils import is_content_empty, remove_redundant_spaces
from common.tag_feature_utils import validate_tag_features
from rag.app.qa import beAdoc, rmPrefix
from rag.app.tag import label_question
from rag.nlp import rag_tokenizer, search
from api.apps.extensions.rbac import require_permission, Permission
from rag.prompts.generator import cross_languages, keyword_extraction

MAXIMUM_OF_UPLOADING_FILES = 256


class Chunk(BaseModel):
    id: str = ""
    content: str = ""
    document_id: str = ""
    docnm_kwd: str = ""
    important_keywords: list = Field(default_factory=list)
    tag_kwd: list = Field(default_factory=list)
    questions: list = Field(default_factory=list)
    question_tks: str = ""
    image_id: str = ""
    available: bool = True
    positions: list[list[int]] = Field(default_factory=list)

    @validator("positions")
    def validate_positions(cls, value):
        for sublist in value:
            if len(sublist) != 5:
                raise ValueError("Each sublist in positions must have a length of 5")
        return value



@manager.route("/datasets/<dataset_id>/documents/<document_id>", methods=["PUT"])  # noqa: F821
@token_required
@require_permission(Permission.DOCUMENT_CREATE)
async def update_doc(tenant_id, dataset_id, document_id):
    """
    Update a document within a dataset.
    ---
    tags:
      - Documents
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: path
        name: document_id
        type: string
        required: true
        description: ID of the document to update.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
      - in: body
        name: body
        description: Document update parameters.
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
              description: New name of the document.
            parser_config:
              type: object
              description: Parser configuration.
            chunk_method:
              type: string
              description: Chunking method.
            enabled:
              type: boolean
              description: Document status.
    responses:
      200:
        description: Document updated successfully.
        schema:
          type: object
    """
    req = await get_request_json()
    if not KnowledgebaseService.query(id=dataset_id, tenant_id=tenant_id):
        return get_error_data_result(message="You don't own the dataset.")
    e, kb = KnowledgebaseService.get_by_id(dataset_id)
    if not e:
        return get_error_data_result(message="Can't find this dataset!")
    doc = DocumentService.query(kb_id=dataset_id, id=document_id)
    if not doc:
        return get_error_data_result(message="The dataset doesn't own the document.")
    doc = doc[0]
    if "chunk_count" in req:
        if req["chunk_count"] != doc.chunk_num:
            return get_error_data_result(message="Can't change `chunk_count`.")
    if "token_count" in req:
        if req["token_count"] != doc.token_num:
            return get_error_data_result(message="Can't change `token_count`.")
    if "progress" in req:
        if req["progress"] != doc.progress:
            return get_error_data_result(message="Can't change `progress`.")

    if "meta_fields" in req:
        if not isinstance(req["meta_fields"], dict):
            return get_error_data_result(message="meta_fields must be a dictionary")
        if not DocMetadataService.update_document_metadata(document_id, req["meta_fields"]):
            return get_error_data_result(message="Failed to update metadata")

    if "name" in req and req["name"] != doc.name:
        if not isinstance(req["name"], str):
            return server_error_response(AttributeError(f"'{type(req['name']).__name__}' object has no attribute 'encode'"))
        if len(req["name"].encode("utf-8")) > FILE_NAME_LEN_LIMIT:
            return get_result(
                message=f"File name must be {FILE_NAME_LEN_LIMIT} bytes or less.",
                code=RetCode.ARGUMENT_ERROR,
            )
        if pathlib.Path(req["name"].lower()).suffix != pathlib.Path(doc.name.lower()).suffix:
            return get_result(
                message="The extension of file can't be changed",
                code=RetCode.ARGUMENT_ERROR,
            )
        for d in DocumentService.query(name=req["name"], kb_id=doc.kb_id):
            if d.name == req["name"]:
                return get_error_data_result(message="Duplicated document name in the same dataset.")
        if not DocumentService.update_by_id(document_id, {"name": req["name"]}):
            return get_error_data_result(message="Database error (Document rename)!")

        informs = File2DocumentService.get_by_document_id(document_id)
        if informs:
            e, file = FileService.get_by_id(informs[0].file_id)
            FileService.update_by_id(file.id, {"name": req["name"]})

    if "parser_config" in req:
        DocumentService.update_parser_config(doc.id, req["parser_config"])
    if "chunk_method" in req:
        valid_chunk_method = {"naive", "manual", "qa", "table", "paper", "book", "laws", "presentation", "picture", "one", "knowledge_graph", "email", "tag"}
        if req.get("chunk_method") not in valid_chunk_method:
            return get_error_data_result(f"`chunk_method` {req['chunk_method']} doesn't exist")

        if doc.type == FileType.VISUAL or re.search(r"\.(ppt|pptx|pages)$", doc.name):
            return get_error_data_result(message="Not supported yet!")

        if doc.parser_id.lower() != req["chunk_method"].lower():
            e = DocumentService.update_by_id(
                doc.id,
                {
                    "parser_id": req["chunk_method"],
                    "progress": 0,
                    "progress_msg": "",
                    "run": TaskStatus.UNSTART.value,
                },
            )
            if not e:
                return get_error_data_result(message="Document not found!")
        if not req.get("parser_config"):
            req["parser_config"] = get_parser_config(req["chunk_method"], req.get("parser_config"))
            DocumentService.update_parser_config(doc.id, req["parser_config"])
        if doc.token_num > 0:
            e = DocumentService.increment_chunk_num(
                doc.id,
                doc.kb_id,
                doc.token_num * -1,
                doc.chunk_num * -1,
                doc.process_duration * -1,
            )
            if not e:
                return get_error_data_result(message="Document not found!")
            settings.docStoreConn.delete({"doc_id": doc.id}, search.index_name(tenant_id), dataset_id)

    if "enabled" in req:
        status = int(req["enabled"])
        if doc.status != req["enabled"]:
            try:
                if not DocumentService.update_by_id(doc.id, {"status": str(status)}):
                    return get_error_data_result(message="Database error (Document update)!")
                settings.docStoreConn.update({"doc_id": doc.id}, {"available_int": status}, search.index_name(kb.tenant_id), doc.kb_id)
            except Exception as e:
                return server_error_response(e)

    try:
        ok, doc = DocumentService.get_by_id(doc.id)
        if not ok:
            return get_error_data_result(message="Dataset created failed")
    except OperationalError as e:
        logging.exception(e)
        return get_error_data_result(message="Database operation failed")

    key_mapping = {
        "chunk_num": "chunk_count",
        "kb_id": "dataset_id",
        "token_num": "token_count",
        "parser_id": "chunk_method",
    }
    run_mapping = {
        "0": "UNSTART",
        "1": "RUNNING",
        "2": "CANCEL",
        "3": "DONE",
        "4": "FAIL",
    }
    renamed_doc = {}
    for key, value in doc.to_dict().items():
        new_key = key_mapping.get(key, key)
        renamed_doc[new_key] = value
        if key == "run":
            renamed_doc["run"] = run_mapping.get(str(value))

    return get_result(data=renamed_doc)


from api.utils.reference_metadata_utils import (
    enrich_chunks_with_document_metadata,
    resolve_reference_metadata_preferences,
)


def _resolve_reference_metadata(req: dict, search_config: dict | None = None):
    return resolve_reference_metadata_preferences(req, search_config)


def _enrich_chunks_with_document_metadata(chunks: list[dict], metadata_fields=None) -> None:
    enrich_chunks_with_document_metadata(chunks, metadata_fields)


@manager.route("/datasets/<dataset_id>/documents/<document_id>", methods=["GET"])  # noqa: F821
@token_required
@require_permission(Permission.DOCUMENT_READ)
async def download(tenant_id, dataset_id, document_id):
    """
    Download a document from a dataset.
    ---
    tags:
      - Documents
    security:
      - ApiKeyAuth: []
    produces:
      - application/octet-stream
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: path
        name: document_id
        type: string
        required: true
        description: ID of the document to download.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Document file stream.
        schema:
          type: file
      400:
        description: Error message.
        schema:
          type: object
    """
    if not document_id:
        return get_error_data_result(message="Specify document_id please.")
    if not KnowledgebaseService.query(id=dataset_id, tenant_id=tenant_id):
        return get_error_data_result(message=f"You do not own the dataset {dataset_id}.")
    doc = DocumentService.query(kb_id=dataset_id, id=document_id)
    if not doc:
        return get_error_data_result(message=f"The dataset not own the document {document_id}.")
    # The process of downloading
    doc_id, doc_location = File2DocumentService.get_storage_address(doc_id=document_id)  # minio address
    file_stream = settings.STORAGE_IMPL.get(doc_id, doc_location)
    if not file_stream:
        return construct_json_result(message="This file is empty.", code=RetCode.DATA_ERROR)
    file = BytesIO(file_stream)
    # Use send_file with a proper filename and MIME type
    return await send_file(
        file,
        as_attachment=True,
        attachment_filename=doc[0].name,
        mimetype="application/octet-stream",  # Set a default MIME type
    )


@manager.route("/documents/<document_id>", methods=["GET"])  # noqa: F821
async def download_doc(document_id):
    token = request.headers.get("Authorization").split()
    if len(token) != 2:
        return get_error_data_result(message="Authorization is not valid!")
    token = token[1]
    objs = APIToken.query(beta=token)
    if not objs:
        return get_error_data_result(message='Authentication error: API key is invalid!"')
    api_tenant_id = objs[0].tenant_id

    if not document_id:
        return get_error_data_result(message="Specify document_id please.")
    doc = DocumentService.query(id=document_id)
    if not doc:
        return get_error_data_result(message=f"The dataset not own the document {document_id}.")
    # CUSTOM B2B SaaS: CRITICAL — verify document's KB belongs to this API token's tenant
    # Without this, any valid API key can download any document by guessing its ID.
    if not KnowledgebaseService.query(tenant_id=api_tenant_id, id=doc[0].kb_id):
        return get_error_data_result(message=f"The dataset not own the document {document_id}.")
    # The process of downloading
    doc_id, doc_location = File2DocumentService.get_storage_address(doc_id=document_id)  # minio address
    file_stream = settings.STORAGE_IMPL.get(doc_id, doc_location)
    if not file_stream:
        return construct_json_result(message="This file is empty.", code=RetCode.DATA_ERROR)
    file = BytesIO(file_stream)
    # Use send_file with a proper filename and MIME type
    return await send_file(
        file,
        as_attachment=True,
        attachment_filename=doc[0].name,
        mimetype="application/octet-stream",  # Set a default MIME type
    )





DOC_STOP_PARSING_INVALID_STATE_MESSAGE = "Can't stop parsing document that has not started or already completed"
DOC_STOP_PARSING_INVALID_STATE_ERROR_CODE = "DOC_STOP_PARSING_INVALID_STATE"


@manager.route("/datasets/<dataset_id>/chunks", methods=["POST"])  # noqa: F821
@token_required
@require_permission(Permission.DOCUMENT_CREATE)
async def parse(tenant_id, dataset_id):
    """
    Start parsing documents into chunks.
    ---
    tags:
      - Chunks
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: body
        name: body
        description: Parsing parameters.
        required: true
        schema:
          type: object
          properties:
            document_ids:
              type: array
              items:
                type: string
              description: List of document IDs to parse.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Parsing started successfully.
        schema:
          type: object
    """
    # CUSTOM B2B SaaS: direct workspace scope — accessible() uses multi-workspace JOIN that leaks across workspaces
    if not KnowledgebaseService.query(tenant_id=tenant_id, id=dataset_id):
        return get_error_data_result(message=f"You don't own the dataset {dataset_id}.")
    req = await get_request_json()
    if not req.get("document_ids"):
        return get_error_data_result("`document_ids` is required")
    doc_list = req.get("document_ids")
    unique_doc_ids, duplicate_messages = check_duplicate_ids(doc_list, "document")
    doc_list = unique_doc_ids

    not_found = []
    success_count = 0
    for id in doc_list:
        doc = DocumentService.query(id=id, kb_id=dataset_id)
        if not doc:
            not_found.append(id)
            continue
        if not doc:
            return get_error_data_result(message=f"You don't own the document {id}.")
        info = {"run": "1", "progress": 0, "progress_msg": "", "chunk_num": 0, "token_num": 0}
        if (
            DocumentService.filter_update(
                [
                    Document.id == id,
                    ((Document.run.is_null(True)) | (Document.run != TaskStatus.RUNNING.value)),
                ],
                info,
            )
            == 0
        ):
            return get_error_data_result("Can't parse document that is currently being processed")
        settings.docStoreConn.delete({"doc_id": id}, search.index_name(tenant_id), dataset_id)
        TaskService.filter_delete([Task.doc_id == id])
        e, doc = DocumentService.get_by_id(id)
        doc = doc.to_dict()
        doc["tenant_id"] = tenant_id
        bucket, name = File2DocumentService.get_storage_address(doc_id=doc["id"])
        queue_tasks(doc, bucket, name, 0)
        success_count += 1
    if not_found:
        return get_result(message=f"Documents not found: {not_found}", code=RetCode.DATA_ERROR)
    if duplicate_messages:
        if success_count > 0:
            return get_result(
                message=f"Partially parsed {success_count} documents with {len(duplicate_messages)} errors",
                data={"success_count": success_count, "errors": duplicate_messages},
            )
        else:
            return get_error_data_result(message=";".join(duplicate_messages))

    return get_result()


@manager.route("/datasets/<dataset_id>/chunks", methods=["DELETE"])  # noqa: F821
@token_required
@require_permission(Permission.DOCUMENT_DELETE)
async def stop_parsing(tenant_id, dataset_id):
    """
    Stop parsing documents into chunks.
    ---
    tags:
      - Chunks
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: body
        name: body
        description: Stop parsing parameters.
        required: true
        schema:
          type: object
          properties:
            document_ids:
              type: array
              items:
                type: string
              description: List of document IDs to stop parsing.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Parsing stopped successfully.
        schema:
          type: object
    """
    # CUSTOM B2B SaaS: direct workspace scope — accessible() uses multi-workspace JOIN that leaks across workspaces
    if not KnowledgebaseService.query(tenant_id=tenant_id, id=dataset_id):
        return get_error_data_result(message=f"You don't own the dataset {dataset_id}.")
    req = await get_request_json()

    if not req.get("document_ids"):
        return get_error_data_result("`document_ids` is required")
    doc_list = req.get("document_ids")
    unique_doc_ids, duplicate_messages = check_duplicate_ids(doc_list, "document")
    doc_list = unique_doc_ids

    success_count = 0
    for id in doc_list:
        doc = DocumentService.query(id=id, kb_id=dataset_id)
        if not doc:
            return get_error_data_result(message=f"You don't own the document {id}.")
        if doc[0].run != TaskStatus.RUNNING.value:
            return construct_json_result(
                code=RetCode.DATA_ERROR,
                message=DOC_STOP_PARSING_INVALID_STATE_MESSAGE,
                data={"error_code": DOC_STOP_PARSING_INVALID_STATE_ERROR_CODE},
            )
        # Send cancellation signal via Redis to stop background task
        cancel_all_task_of(id)
        info = {"run": "2", "progress": 0, "chunk_num": 0}
        DocumentService.update_by_id(id, info)
        settings.docStoreConn.delete({"doc_id": doc[0].id}, search.index_name(tenant_id), dataset_id)
        success_count += 1
    if duplicate_messages:
        if success_count > 0:
            return get_result(
                message=f"Partially stopped {success_count} documents with {len(duplicate_messages)} errors",
                data={"success_count": success_count, "errors": duplicate_messages},
            )
        else:
            return get_error_data_result(message=";".join(duplicate_messages))
    return get_result()



    # return get_result(data={"chunk_id": chunk_id})



@manager.route(  # noqa: F821
    "/datasets/<dataset_id>/documents/<document_id>/chunks/<chunk_id>", methods=["PUT"]
)
@token_required
@require_permission(Permission.DOCUMENT_CREATE)
async def update_chunk(tenant_id, dataset_id, document_id, chunk_id):
    """
    Update a chunk within a document.
    ---
    tags:
      - Chunks
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: path
        name: document_id
        type: string
        required: true
        description: ID of the document.
      - in: path
        name: chunk_id
        type: string
        required: true
        description: ID of the chunk to update.
      - in: body
        name: body
        description: Chunk update parameters.
        required: true
        schema:
          type: object
          properties:
            content:
              type: string
              description: Updated content of the chunk.
            important_keywords:
              type: array
              items:
                type: string
              description: Updated important keywords.
            tag_kwd:
              type: array
              items:
                type: string
              description: Updated tag keywords.
            available:
              type: boolean
              description: Availability status of the chunk.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Chunk updated successfully.
        schema:
          type: object
    """
    chunk = settings.docStoreConn.get(chunk_id, search.index_name(tenant_id), [dataset_id])
    if chunk is None:
        return get_error_data_result(f"Can't find this chunk {chunk_id}")
    # CUSTOM B2B SaaS: direct workspace scope — accessible() uses multi-workspace JOIN that leaks across workspaces
    if not KnowledgebaseService.query(tenant_id=tenant_id, id=dataset_id):
        return get_error_data_result(message=f"You don't own the dataset {dataset_id}.")
    doc = DocumentService.query(id=document_id, kb_id=dataset_id)
    if not doc:
        return get_error_data_result(message=f"You don't own the document {document_id}.")
    doc = doc[0]
    req = await get_request_json()
    content = req.get("content")
    if content is not None:
        if is_content_empty(content):
            return get_error_data_result(message="`content` is required")
    else:
        content = chunk.get("content_with_weight", "")
    d = {"id": chunk_id, "content_with_weight": content}
    d["content_ltks"] = rag_tokenizer.tokenize(d["content_with_weight"])
    d["content_sm_ltks"] = rag_tokenizer.fine_grained_tokenize(d["content_ltks"])
    if "important_keywords" in req:
        if not isinstance(req["important_keywords"], list):
            return get_error_data_result("`important_keywords` should be a list")
        d["important_kwd"] = req.get("important_keywords", [])
        d["important_tks"] = rag_tokenizer.tokenize(" ".join(req["important_keywords"]))
    if "questions" in req:
        if not isinstance(req["questions"], list):
            return get_error_data_result("`questions` should be a list")
        d["question_kwd"] = [str(q).strip() for q in req.get("questions", []) if str(q).strip()]
        d["question_tks"] = rag_tokenizer.tokenize("\n".join(req["questions"]))
    if "available" in req:
        d["available_int"] = int(req["available"])
    if "positions" in req:
        if not isinstance(req["positions"], list):
            return get_error_data_result("`positions` should be a list")
        d["position_int"] = req["positions"]
    if "tag_kwd" in req:
        if not isinstance(req["tag_kwd"], list):
            return get_error_data_result("`tag_kwd` should be a list")
        if not all(isinstance(t, str) for t in req["tag_kwd"]):
            return get_error_data_result("`tag_kwd` must be a list of strings")
        d["tag_kwd"] = req["tag_kwd"]
    if "tag_feas" in req:
        try:
            d["tag_feas"] = validate_tag_features(req["tag_feas"])
        except ValueError as exc:
            return get_error_data_result(f"`tag_feas` {exc}")
    tenant_embd_id = DocumentService.get_tenant_embd_id(document_id)
    if tenant_embd_id:
        model_config = get_model_config_by_id(tenant_embd_id)
    else:
        embd_id = DocumentService.get_embd_id(document_id)
        model_config = get_model_config_by_type_and_name(tenant_id, LLMType.EMBEDDING.value, embd_id)
    embd_mdl = TenantLLMService.model_instance(model_config)
    if doc.parser_id == ParserType.QA:
        arr = [t for t in re.split(r"[\n\t]", d["content_with_weight"]) if len(t) > 1]
        if len(arr) != 2:
            return get_error_data_result(message="Q&A must be separated by TAB/ENTER key.")
        q, a = rmPrefix(arr[0]), rmPrefix(arr[1])
        d = beAdoc(d, arr[0], arr[1], not any([rag_tokenizer.is_chinese(t) for t in q + a]))

    v, c = embd_mdl.encode([doc.name, d["content_with_weight"] if not d.get("question_kwd") else "\n".join(d["question_kwd"])])
    v = 0.1 * v[0] + 0.9 * v[1] if doc.parser_id != ParserType.QA else v[1]
    d["q_%d_vec" % len(v)] = v.tolist()
    settings.docStoreConn.update({"id": chunk_id}, d, search.index_name(tenant_id), dataset_id)
    return get_result()


@manager.route(  # noqa: F821
    "/datasets/<dataset_id>/documents/<document_id>/chunks/switch", methods=["POST"]
)
@token_required
@require_permission(Permission.DOCUMENT_CREATE)
async def switch_chunks(tenant_id, dataset_id, document_id):
    """
    Switch availability of specified chunks (same as chunk_app switch).
    ---
    tags:
      - Chunks
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: path
        name: document_id
        type: string
        required: true
        description: ID of the document.
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            chunk_ids:
              type: array
              items:
                type: string
              description: List of chunk IDs to switch.
            available_int:
              type: integer
              description: 1 for available, 0 for unavailable.
            available:
              type: boolean
              description: Availability status (alternative to available_int).
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Chunks availability switched successfully.
    """
    # CUSTOM B2B SaaS: direct workspace scope — accessible() uses multi-workspace JOIN that leaks across workspaces
    if not KnowledgebaseService.query(tenant_id=tenant_id, id=dataset_id):
        return get_error_data_result(message=f"You don't own the dataset {dataset_id}.")
    req = await get_request_json()
    if not req.get("chunk_ids"):
        return get_error_data_result(message="`chunk_ids` is required.")
    if "available_int" not in req and "available" not in req:
        return get_error_data_result(message="`available_int` or `available` is required.")
    available_int = int(req["available_int"]) if "available_int" in req else (1 if req.get("available") else 0)
    try:

        def _switch_sync():
            e, doc = DocumentService.get_by_id(document_id)
            if not e:
                return get_error_data_result(message="Document not found!")
            if not doc or str(doc.kb_id) != str(dataset_id):
                return get_error_data_result(message="Document not found!")
            for cid in req["chunk_ids"]:
                if not settings.docStoreConn.update(
                    {"id": cid},
                    {"available_int": available_int},
                    search.index_name(tenant_id),
                    doc.kb_id,
                ):
                    return get_error_data_result(message="Index updating failure")
            return get_result(data=True)

        return await thread_pool_exec(_switch_sync)
    except Exception as e:
        return server_error_response(e)


@manager.route("/retrieval", methods=["POST"])  # noqa: F821
@token_required
@require_permission(Permission.DATASET_READ)
async def retrieval_test(tenant_id):
    """
    Retrieve chunks based on a query.
    ---
    tags:
      - Retrieval
    security:
      - ApiKeyAuth: []
    parameters:
      - in: body
        name: body
        description: Retrieval parameters.
        required: true
        schema:
          type: object
          properties:
            dataset_ids:
              type: array
              items:
                type: string
              required: true
              description: List of dataset IDs to search in.
            question:
              type: string
              required: true
              description: Query string.
            document_ids:
              type: array
              items:
                type: string
              description: List of document IDs to filter.
            similarity_threshold:
              type: number
              format: float
              description: Similarity threshold.
            vector_similarity_weight:
              type: number
              format: float
              description: Vector similarity weight.
            top_k:
              type: integer
              description: Maximum number of chunks to return.
            highlight:
              type: boolean
              description: Whether to highlight matched content.
            metadata_condition:
              type: object
              description: metadata filter condition.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Retrieval results.
        schema:
          type: object
          properties:
            chunks:
              type: array
              items:
                type: object
                properties:
                  id:
                    type: string
                    description: Chunk ID.
                  content:
                    type: string
                    description: Chunk content.
                  document_id:
                    type: string
                    description: ID of the document.
                  dataset_id:
                    type: string
                    description: ID of the dataset.
                  similarity:
                    type: number
                    format: float
                    description: Similarity score.
    """
    req = await get_request_json()
    if not req.get("dataset_ids"):
        return get_error_data_result("`dataset_ids` is required.")
    kb_ids = req["dataset_ids"]
    if not isinstance(kb_ids, list):
        return get_error_data_result("`dataset_ids` should be a list")
    for id in kb_ids:
        # CUSTOM B2B SaaS: direct workspace scope — accessible() uses multi-workspace JOIN that leaks across workspaces
        if not KnowledgebaseService.query(tenant_id=tenant_id, id=id):
            return get_error_data_result(f"You don't own the dataset {id}.")
    kbs = KnowledgebaseService.get_by_ids(kb_ids)
    embd_nms = list(set([TenantLLMService.split_model_name_and_factory(kb.embd_id)[0] for kb in kbs]))  # remove vendor suffix for comparison
    if len(embd_nms) != 1:
        return get_result(
            message='Datasets use different embedding models."',
            code=RetCode.DATA_ERROR,
        )
    if "question" not in req:
        return get_error_data_result("`question` is required.")
    page = int(req.get("page", 1))
    size = int(req.get("page_size", 30))
    question = req["question"]
    # Trim whitespace and validate question
    if isinstance(question, str):
        question = question.strip()
    # Return empty result if question is empty or whitespace-only
    if not question:
        return get_result(data={"total": 0, "chunks": [], "doc_aggs": {}})
    doc_ids = req.get("document_ids", [])
    use_kg = req.get("use_kg", False)
    toc_enhance = req.get("toc_enhance", False)
    langs = req.get("cross_languages", [])
    if not isinstance(doc_ids, list):
        return get_error_data_result("`documents` should be a list")
    if doc_ids:
        doc_ids_list = KnowledgebaseService.list_documents_by_ids(kb_ids)
        for doc_id in doc_ids:
            if doc_id not in doc_ids_list:
                return get_error_data_result(f"The datasets don't own the document {doc_id}")
    if not doc_ids:
        metadata_condition = req.get("metadata_condition")
        if metadata_condition:
            metas = DocMetadataService.get_flatted_meta_by_kbs(kb_ids)
            doc_ids = meta_filter(metas, convert_conditions(metadata_condition), metadata_condition.get("logic", "and"))
            # If metadata_condition has conditions but no docs match, return empty result
            if not doc_ids and metadata_condition.get("conditions"):
                return get_result(data={"total": 0, "chunks": [], "doc_aggs": {}})
            if metadata_condition and not doc_ids:
                doc_ids = ["-999"]
        else:
            # If doc_ids is None all documents of the datasets are used
            doc_ids = None
    similarity_threshold = float(req.get("similarity_threshold", 0.2))
    vector_similarity_weight = float(req.get("vector_similarity_weight", 0.3))
    top = int(req.get("top_k", 1024))
    if top <= 0:
        return get_error_data_result("`top_k` must be greater than 0")
    highlight_val = req.get("highlight", None)
    if highlight_val is None:
        highlight = False
    elif isinstance(highlight_val, bool):
        highlight = highlight_val
    elif isinstance(highlight_val, str):
        if highlight_val.lower() in ["true", "false"]:
            highlight = highlight_val.lower() == "true"
        else:
            return get_error_data_result("`highlight` should be a boolean")
    else:
        return get_error_data_result("`highlight` should be a boolean")
    include_metadata, metadata_fields = _resolve_reference_metadata(req)
    try:
        tenant_ids = list(set([kb.tenant_id for kb in kbs]))
        e, kb = KnowledgebaseService.get_by_id(kb_ids[0])
        if not e:
            return get_error_data_result(message="Dataset not found!")
        if kb.tenant_embd_id:
            embd_model_config = get_model_config_by_id(kb.tenant_embd_id)
        else:
            embd_model_config = get_model_config_by_type_and_name(kb.tenant_id, LLMType.EMBEDDING, kb.embd_id)
        embd_mdl = LLMBundle(kb.tenant_id, embd_model_config)

        rerank_mdl = None
        if req.get("tenant_rerank_id"):
            rerank_model_config = get_model_config_by_id(req["tenant_rerank_id"])
            rerank_mdl = LLMBundle(kb.tenant_id, rerank_model_config)
        elif req.get("rerank_id"):
            rerank_model_config = get_model_config_by_type_and_name(kb.tenant_id, LLMType.RERANK, req["rerank_id"])
            rerank_mdl = LLMBundle(kb.tenant_id, rerank_model_config)

        if langs:
            question = await cross_languages(kb.tenant_id, None, question, langs)

        if req.get("keyword", False):
            chat_model_config = get_tenant_default_model_by_type(kb.tenant_id, LLMType.CHAT)
            chat_mdl = LLMBundle(kb.tenant_id, chat_model_config)
            question += await keyword_extraction(chat_mdl, question)

        ranks = await settings.retriever.retrieval(
            question,
            embd_mdl,
            tenant_ids,
            kb_ids,
            page,
            size,
            similarity_threshold,
            vector_similarity_weight,
            top,
            doc_ids,
            rerank_mdl=rerank_mdl,
            highlight=highlight,
            rank_feature=label_question(question, kbs),
        )
        if toc_enhance:
            chat_model_config = get_tenant_default_model_by_type(kb.tenant_id, LLMType.CHAT)
            chat_mdl = LLMBundle(kb.tenant_id, chat_model_config)
            cks = await settings.retriever.retrieval_by_toc(question, ranks["chunks"], tenant_ids, chat_mdl, size)
            if cks:
                ranks["chunks"] = cks
        ranks["chunks"] = settings.retriever.retrieval_by_children(ranks["chunks"], tenant_ids)
        if use_kg:
            chat_model_config = get_tenant_default_model_by_type(kb.tenant_id, LLMType.CHAT)
            ck = await settings.kg_retriever.retrieval(question, [k.tenant_id for k in kbs], kb_ids, embd_mdl, LLMBundle(kb.tenant_id, chat_model_config))
            if ck["content_with_weight"]:
                ranks["chunks"].insert(0, ck)

        for c in ranks["chunks"]:
            c.pop("vector", None)

        if include_metadata:
            logging.info(
                "sdk.retrieval reference_metadata enabled dataset_ids=%s fields=%s chunks=%s",
                kb_ids,
                sorted(metadata_fields) if metadata_fields else None,
                len(ranks["chunks"]),
            )
            enrich_chunks_with_document_metadata(ranks["chunks"], metadata_fields)

        ##rename keys
        renamed_chunks = []
        for chunk in ranks["chunks"]:
            key_mapping = {
                "chunk_id": "id",
                "content_with_weight": "content",
                "doc_id": "document_id",
                "important_kwd": "important_keywords",
                "question_kwd": "questions",
                "docnm_kwd": "document_keyword",
                "kb_id": "dataset_id",
            }
            rename_chunk = {}
            for key, value in chunk.items():
                new_key = key_mapping.get(key, key)
                rename_chunk[new_key] = value
            renamed_chunks.append(rename_chunk)
        ranks["chunks"] = renamed_chunks
        return get_result(data=ranks)
    except Exception as e:
        if str(e).find("not_found") > 0:
            return get_result(
                message="No chunk found! Check the chunk status please!",
                code=RetCode.DATA_ERROR,
            )
        return server_error_response(e)
