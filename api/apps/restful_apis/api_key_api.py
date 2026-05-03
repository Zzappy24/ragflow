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
"""
CUSTOM B2B SaaS — per-user API keys.

Any workspace member can mint a personal API key, scoped to a subset of the
permissions they themselves currently hold in the active workspace. The key
inherits the user's identity for RBAC (cf. ``_load_user`` in ``api/apps/__init__.py``):
when a request authenticates with the token, ``g.user`` is set to the human
creator and downstream ``@require_permission`` runs against THEIR live role —
so a role downgrade revokes the key's effective power immediately.

Routes:
  GET    /api/v1/api_keys              — list MY keys in the active workspace
  POST   /api/v1/api_keys              — mint a key (returns token ONCE)
  DELETE /api/v1/api_keys/<scope_id>   — revoke MY key

Org / workspace-wide *service* keys (shared across users) are managed in the
admin panel — those are a different surface and live under
``management/server/routers/api_keys.py``.

Each key is materialised as TWO rows:
  - ``APIToken``       — used by ``_load_user`` to authenticate the bearer.
  - ``ApiKeyScope``    — binds the token to a workspace + permission set and
                         remembers the human creator (for audit + filtering).

Both rows share the same ``token`` string.
"""
import secrets
from datetime import datetime

from quart import g, request

from api.apps import current_user, login_required
from api.apps.extensions.rbac import (
    OrgRole,
    Permission,
    ROLE_PERMISSIONS,
    _resolve_rbac_context_cached,
)
from api.db.db_models import APIToken, ApiKeyScope
from api.db.services.audit_service import AuditService
from api.db.services.workspace_service import ApiKeyScopeService, WorkspaceService
from api.utils.api_utils import (
    get_data_error_result,
    get_json_result,
    get_request_json,
    server_error_response,
)
from api.utils.tenant_context import active_tenant_id
from common.misc_utils import get_uuid


# Valid permission strings — derived from the Permission enum so the UI and
# the backend stay in sync without a hard-coded duplicate list.
_VALID_PERMISSIONS = {p.value for p in Permission}


def _serialize(scope: ApiKeyScope, *, include_token: bool = False) -> dict:
    return {
        "id": scope.id,
        "name": scope.name,
        "workspace_id": scope.workspace_id,
        "permissions": list(scope.permissions or []),
        "created_by": scope.created_by,
        "create_time": scope.create_time,
        "update_time": scope.update_time,
        "expires_at": scope.expires_at.isoformat() if scope.expires_at else None,
        "last_used_at": scope.last_used_at.isoformat() if scope.last_used_at else None,
        "status": scope.status,
        # Tail of the token only — we never re-display the full secret.
        "token_preview": (scope.token[:10] + "…" + scope.token[-4:]) if scope.token else None,
        **({"token": scope.token} if include_token else {}),
    }


def _resolve_active_workspace_id() -> str | None:
    """The workspace id is set by the X-Workspace-Id middleware on g._ws_header."""
    return getattr(g, "_ws_header", None)


def _user_effective_permissions(user_id: str, tenant_id: str) -> set[str]:
    """
    Permissions the caller actually holds in the active workspace right now.
    Used to clamp the scope of a freshly-minted API key — a member with only
    ``dataset.read`` cannot self-elevate by minting a key with
    ``dataset.write``. Superusers and org admins of the workspace's parent org
    receive the full set.
    """
    ctx = _resolve_rbac_context_cached(user_id, tenant_id)
    if ctx is None:
        return set()
    if ctx["is_superuser"]:
        return _VALID_PERMISSIONS
    if ctx["org_role"] == OrgRole.ORG_ADMIN:
        return _VALID_PERMISSIONS
    ws_role = ctx["ws_role"]
    if ws_role is None:
        return set()
    return {p.value for p in ROLE_PERMISSIONS.get(ws_role, set())}


@manager.route("/api_keys", methods=["GET"])  # noqa: F821
@login_required
async def list_my_api_keys():
    workspace_id = _resolve_active_workspace_id()
    if not workspace_id:
        return get_data_error_result(message="X-Workspace-Id header is required")
    try:
        keys = ApiKeyScopeService.list_by_creator(workspace_id, current_user.id)
        return get_json_result(data={"keys": [_serialize(k) for k in keys]})
    except Exception as e:
        return server_error_response(e)


@manager.route("/api_keys", methods=["POST"])  # noqa: F821
@login_required
async def create_my_api_key():
    workspace_id = _resolve_active_workspace_id()
    if not workspace_id:
        return get_data_error_result(message="X-Workspace-Id header is required")

    payload = await get_request_json() or {}
    name = (payload.get("name") or "").strip()
    permissions = payload.get("permissions") or []
    expires_at_raw = payload.get("expires_at")

    if not name:
        return get_data_error_result(message="name is required")
    if not isinstance(permissions, list) or not permissions:
        return get_data_error_result(message="permissions must be a non-empty list")
    invalid = [p for p in permissions if p not in _VALID_PERMISSIONS]
    if invalid:
        return get_data_error_result(message=f"unknown permissions: {invalid}")

    ok, ws = WorkspaceService.get_by_id(workspace_id)
    if not ok or not ws or ws.status != "1":
        return get_data_error_result(message=f"workspace {workspace_id} not found")

    # Clamp: a key cannot grant more than the caller already has.
    effective = _user_effective_permissions(current_user.id, ws.tenant_id)
    excess = [p for p in permissions if p not in effective]
    if excess:
        return get_data_error_result(
            message=(
                f"Cannot mint a key with permissions you don't hold: {excess}. "
                "Ask a workspace admin if you need broader scope."
            )
        )

    expires_at = None
    if expires_at_raw:
        try:
            # Accept ISO 8601 ("YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS").
            expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return get_data_error_result(message=f"invalid expires_at: {expires_at_raw!r}")
        if expires_at <= datetime.utcnow():
            return get_data_error_result(message="expires_at must be in the future")

    token = "ragflow-" + secrets.token_urlsafe(32)
    try:
        APIToken.create(tenant_id=ws.tenant_id, token=token, source="none")
        scope = ApiKeyScope.create(
            id=get_uuid(),
            token=token,
            workspace_id=workspace_id,
            permissions=permissions,
            name=name,
            expires_at=expires_at,
            created_by=current_user.id,
            status="1",
        )
    except Exception as e:
        # Roll back the APIToken row if scope creation failed, otherwise the
        # token would be orphaned and authenticate without RBAC scopes.
        try:
            APIToken.delete().where(APIToken.token == token).execute()
        except Exception:
            pass
        return server_error_response(e)

    AuditService.record(
        org_id=ws.org_id,
        workspace_id=workspace_id,
        user_id=current_user.id,
        actor_email=getattr(current_user, "email", ""),
        action="API_KEY_CREATE",
        resource_type="api_key_scope",
        resource_id=scope.id,
        details={
            "name": name,
            "permissions": permissions,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent", "")[:512],
    )
    return get_json_result(data=_serialize(scope, include_token=True))


@manager.route("/api_keys/<scope_id>", methods=["DELETE"])  # noqa: F821
@login_required
async def revoke_my_api_key(scope_id: str):
    workspace_id = _resolve_active_workspace_id()
    if not workspace_id:
        return get_data_error_result(message="X-Workspace-Id header is required")

    scope = ApiKeyScope.get_or_none(ApiKeyScope.id == scope_id)
    if not scope or scope.workspace_id != workspace_id:
        # Don't leak existence cross-workspace — same generic 404.
        return get_data_error_result(message=f"key {scope_id} not found")
    if scope.created_by != current_user.id:
        # A member cannot revoke another member's key. Workspace-wide /
        # service keys must be revoked from the admin panel.
        return get_data_error_result(message=f"key {scope_id} not found")
    if scope.status != "1":
        return get_json_result(data={"id": scope.id, "status": scope.status})

    snapshot = {
        "name": scope.name,
        "permissions": list(scope.permissions or []),
        "created_by": scope.created_by,
    }
    try:
        # Hard-delete both rows so the token can no longer authenticate. Soft-
        # delete (status=0) would still pass the APIToken.query filter in
        # _load_user, defeating the revocation.
        APIToken.delete().where(APIToken.token == scope.token).execute()
        ApiKeyScope.delete().where(ApiKeyScope.id == scope.id).execute()
    except Exception as e:
        return server_error_response(e)

    ok, ws = WorkspaceService.get_by_id(workspace_id)
    AuditService.record(
        org_id=ws.org_id if ok and ws else None,
        workspace_id=workspace_id,
        user_id=current_user.id,
        actor_email=getattr(current_user, "email", ""),
        action="API_KEY_REVOKE",
        resource_type="api_key_scope",
        resource_id=scope_id,
        details=snapshot,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent", "")[:512],
    )
    # active_tenant_id() touch — keeps audit trail / last-used tracking
    # consistent with other RBAC-gated routes that mutate workspace state.
    _ = active_tenant_id()
    return get_json_result(data={"id": scope_id, "deleted": True})
