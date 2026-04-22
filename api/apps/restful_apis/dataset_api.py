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

from peewee import OperationalError
from quart import request
from common.constants import RetCode
from api.apps import login_required, current_user
from api.utils.api_utils import get_error_argument_result, get_error_data_result, get_result, add_tenant_id_to_kwargs
from api.utils.validation_utils import (
    CreateDatasetReq,
    DeleteDatasetReq,
    ListDatasetReq,
    UpdateDatasetReq,
    validate_and_parse_json_request,
    validate_and_parse_request_args,
)
from api.apps.services import dataset_api_service
from api.apps.extensions.rbac import require_permission, has_permission, Permission


@manager.route("/datasets", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_CREATE)
async def create(tenant_id: str=None):
    """
    Create a new dataset.
    ---
    tags:
      - Datasets
    security:
      - ApiKeyAuth: []
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
      - in: body
        name: body
        description: Dataset creation parameters.
        required: true
        schema:
          type: object
          required:
            - name
          properties:
            name:
              type: string
              description: Dataset name (required).
            avatar:
              type: string
              description: Optional base64-encoded avatar image.
            description:
              type: string
              description: Optional dataset description.
            embedding_model:
              type: string
              description: Optional embedding model name; if omitted, the tenant's default embedding model is used.
            permission:
              type: string
              enum: ['me', 'team']
              description: Visibility of the dataset (private to me or shared with team).
            chunk_method:
              type: string
              enum: ["naive", "book", "email", "laws", "manual", "one", "paper",
                     "picture", "presentation", "qa", "table", "tag"]
              description: Chunking method; if omitted, defaults to "naive".
            parser_config:
              type: object
              description: Optional parser configuration; server-side defaults will be applied.
    responses:
      200:
        description: Successful operation.
        schema:
          type: object
          properties:
            data:
              type: object
    """
    req, err = await validate_and_parse_json_request(request, CreateDatasetReq)
    if err is not None:
        return get_error_argument_result(err)

    # CUSTOM B2B SaaS: non-admins cannot force embedding_model at creation — silent drop, fallback to workspace default
    _effective_tenant = tenant_id or current_user.id
    if ("embd_id" in req or "embedding_model" in req) and not has_permission(
        current_user.id, _effective_tenant, Permission.LLM_CONFIGURE
    ):
        logging.warning("RBAC: non-admin %s attempted to set embedding_model on dataset creation — dropped", current_user.id)
        req.pop("embd_id", None)
        req.pop("embedding_model", None)

    try:
        if not tenant_id:
            from api.utils.tenant_context import maybe_active_tenant_id
            tenant_id = maybe_active_tenant_id() or current_user.id
        success, result = await dataset_api_service.create_dataset(tenant_id, req)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets", methods=["DELETE"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_DELETE)
async def delete(tenant_id):
    """
    Delete datasets.
    ---
    tags:
      - Datasets
    security:
      - ApiKeyAuth: []
    parameters:
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
      - in: body
        name: body
        description: Dataset deletion parameters.
        required: true
        schema:
          type: object
          required:
            - ids
          properties:
            ids:
              type: array or null
              items:
                type: string
              description: |
                Specifies the datasets to delete:
                - If `null`, all datasets will be deleted.
                - If an array of IDs, only the specified datasets will be deleted.
                - If an empty array, no datasets will be deleted.
    responses:
      200:
        description: Successful operation.
        schema:
          type: object
    """
    req, err = await validate_and_parse_json_request(request, DeleteDatasetReq)
    if err is not None:
        return get_error_argument_result(err)

    try:
        success, result = await dataset_api_service.delete_datasets(tenant_id, req.get("ids"), req.get("delete_all", False))
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except OperationalError as e:
        logging.exception(e)
        return get_error_data_result(message="Database operation failed")
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets/<dataset_id>", methods=["PUT"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_UPDATE)
async def update(tenant_id, dataset_id):
    """
    Update a dataset.
    ---
    tags:
      - Datasets
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset to update.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
      - in: body
        name: body
        description: Dataset update parameters.
        required: true
        schema:
          type: object
          properties:
            name:
              type: string
              description: New name of the dataset.
            avatar:
              type: string
              description: Updated base64 encoding of the avatar.
            description:
              type: string
              description: Updated description of the dataset.
            embedding_model:
              type: string
              description: Updated embedding model Name.
            permission:
              type: string
              enum: ['me', 'team']
              description: Updated dataset permission.
            chunk_method:
              type: string
              enum: ["naive", "book", "email", "laws", "manual", "one", "paper",
                     "picture", "presentation", "qa", "table", "tag"
                     ]
              description: Updated chunking method.
            pagerank:
              type: integer
              description: Updated page rank.
            parser_config:
              type: object
              description: Updated parser configuration.
    responses:
      200:
        description: Successful operation.
        schema:
          type: object
    """
    # Field name transformations during model dump:
    # | Original       | Dump Output  |
    # |----------------|-------------|
    # | embedding_model| embd_id     |
    # | chunk_method   | parser_id   |
    # CUSTOM B2B SaaS: block embedding_model changes for non-admins (FinOps — GPU cost control)
    raw_body = await request.get_json() or {}
    if "embedding_model" in raw_body and not has_permission(
        current_user.id, tenant_id, Permission.LLM_CONFIGURE
    ):
        return get_error_argument_result("Only workspace admins can change the embedding model.")

    extras = {"dataset_id": dataset_id}
    req, err = await validate_and_parse_json_request(request, UpdateDatasetReq, extras=extras, exclude_unset=True)
    if err is not None:
        return get_error_argument_result(err)

    try:
        success, result = await dataset_api_service.update_dataset(tenant_id, dataset_id, req)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except OperationalError as e:
        logging.exception(e)
        return get_error_data_result(message="Database operation failed")
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_READ)
def list_datasets(tenant_id):
    """
    List datasets.
    ---
    tags:
      - Datasets
    security:
      - ApiKeyAuth: []
    parameters:
      - in: query
        name: id
        type: string
        required: false
        description: Dataset ID to filter.
      - in: query
        name: name
        type: string
        required: false
        description: Dataset name to filter.
      - in: query
        name: page
        type: integer
        required: false
        default: 1
        description: Page number.
      - in: query
        name: page_size
        type: integer
        required: false
        default: 30
        description: Number of items per page.
      - in: query
        name: orderby
        type: string
        required: false
        default: "create_time"
        description: Field to order by.
      - in: query
        name: desc
        type: boolean
        required: false
        default: true
        description: Order in descending.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Successful operation.
        schema:
          type: array
          items:
            type: object
    """
    args, err = validate_and_parse_request_args(request, ListDatasetReq)
    if err is not None:
        return get_error_argument_result(err)

    try:
        success, result = dataset_api_service.list_datasets(tenant_id, args)
        if success:
            return get_result(data=result.get("data"), total=result.get("total"))
        else:
            return get_error_data_result(message=result)
    except OperationalError as e:
        logging.exception(e)
        return get_error_data_result(message="Database operation failed")
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route('/datasets/<dataset_id>/knowledge_graph', methods=['GET'])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_READ)
async def knowledge_graph(tenant_id, dataset_id):
    try:
        success, result = await dataset_api_service.get_knowledge_graph(dataset_id, tenant_id)
        if success:
            return get_result(data=result)
        else:
            return get_result(
                data=False,
                message=result,
                code=RetCode.AUTHENTICATION_ERROR
            )
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route('/datasets/<dataset_id>/knowledge_graph', methods=['DELETE'])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_DELETE)
def delete_knowledge_graph(tenant_id, dataset_id):
    try:
        success, result = dataset_api_service.delete_knowledge_graph(dataset_id, tenant_id)
        if success:
            return get_result(data=result)
        else:
            return get_result(
                data=False,
                message=result,
                code=RetCode.AUTHENTICATION_ERROR
            )
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets/<dataset_id>/run_graphrag", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_UPDATE)
async def run_graphrag(tenant_id, dataset_id):
    try:
        success, result = dataset_api_service.run_graphrag(dataset_id, tenant_id)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets/<dataset_id>/trace_graphrag", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_READ)
def trace_graphrag(tenant_id, dataset_id):
    try:
        success, result = dataset_api_service.trace_graphrag(dataset_id, tenant_id)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets/<dataset_id>/run_raptor", methods=["POST"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_UPDATE)
async def run_raptor(tenant_id, dataset_id):
    try:
        success, result = dataset_api_service.run_raptor(dataset_id, tenant_id)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets/<dataset_id>/trace_raptor", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
@require_permission(Permission.DATASET_READ)
def trace_raptor(tenant_id, dataset_id):
    try:
        success, result = dataset_api_service.trace_raptor(dataset_id, tenant_id)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets/<dataset_id>/auto_metadata", methods=["GET"])  # noqa: F821
@login_required
@require_permission(Permission.DATASET_READ)
@add_tenant_id_to_kwargs
def get_auto_metadata(tenant_id, dataset_id):
    """
    Get auto-metadata configuration for a dataset.
    ---
    tags:
      - Datasets
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
    responses:
      200:
        description: Successful operation.
        schema:
          type: object
    """
    try:
        success, result = dataset_api_service.get_auto_metadata(dataset_id, tenant_id)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")


@manager.route("/datasets/<dataset_id>/auto_metadata", methods=["PUT"])  # noqa: F821
@login_required
@require_permission(Permission.DATASET_UPDATE)
@add_tenant_id_to_kwargs
async def update_auto_metadata(tenant_id, dataset_id):
    """
    Update auto-metadata configuration for a dataset.
    ---
    tags:
      - Datasets
    security:
      - ApiKeyAuth: []
    parameters:
      - in: path
        name: dataset_id
        type: string
        required: true
        description: ID of the dataset.
      - in: header
        name: Authorization
        type: string
        required: true
        description: Bearer token for authentication.
      - in: body
        name: body
        description: Auto-metadata configuration.
        required: true
        schema:
          type: object
    responses:
      200:
        description: Successful operation.
        schema:
          type: object
    """
    from api.utils.validation_utils import AutoMetadataConfig
    cfg, err = await validate_and_parse_json_request(request, AutoMetadataConfig)
    if err is not None:
        return get_error_argument_result(err)

    try:
        success, result = await dataset_api_service.update_auto_metadata(dataset_id, tenant_id, cfg)
        if success:
            return get_result(data=result)
        else:
            return get_error_data_result(message=result)
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")
