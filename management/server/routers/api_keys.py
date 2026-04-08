"""
Scoped API key management routes.
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from management.server.auth.dependencies import get_current_user_id
from management.server.models.schemas import ApiKeyCreate, ApiKeyResponse

router = APIRouter()


def _mask_token(token: str) -> str:
    """Show constant prefix + 4 unique chars + ... + last 4 chars.

    e.g. 'ragflow-ws-RiZxLclnBPBiy7K73SJONICdYuBQzEs3YSNT8AuPWnk'
       → 'ragflow-ws-RiZx...PWnk'
    """
    if not token:
        return ""
    # Reveal at most 8 unique characters total (4 head + 4 tail)
    if len(token) <= 12:
        return token[:2] + "..." + token[-2:]
    for prefix in ("ragflow-ws-", "ragflow-"):
        if token.startswith(prefix) and len(token) >= len(prefix) + 8:
            return token[: len(prefix) + 4] + "..." + token[-4:]
    return token[:4] + "..." + token[-4:]


def _key_to_response(key, include_token: bool = False) -> dict:
    return {
        "id": key.id,
        "workspace_id": key.workspace_id,
        "name": getattr(key, "name", ""),
        "token": key.token if include_token else None,
        "token_hint": _mask_token(key.token) if key.token else None,
        "permissions": key.permissions if isinstance(key.permissions, list) else [],
        "expires_at": getattr(key, "expires_at", None),
        "last_used_at": getattr(key, "last_used_at", None),
        "status": key.status,
        "create_time": getattr(key, "create_time", None),
    }


@router.get("/workspaces/{ws_id}/api-keys", response_model=list[ApiKeyResponse])
def list_api_keys(ws_id: str, user_id: str = Depends(get_current_user_id)):
    """List all API keys for a workspace."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    from api.db.services.workspace_service import ApiKeyScopeService
    keys = ApiKeyScopeService.list_by_workspace(ws_id)
    return [_key_to_response(k) for k in keys]


@router.post("/workspaces/{ws_id}/api-keys", response_model=ApiKeyResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(ws_id: str, body: ApiKeyCreate, user_id: str = Depends(get_current_user_id)):
    """Create a scoped API key for a workspace. Token is only returned once."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    from api.db.services.workspace_service import WorkspaceService, ApiKeyScopeService
    from common.misc_utils import get_uuid

    ok, ws = WorkspaceService.get_by_id(ws_id)
    if not ok or not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Generate a secure token prefixed for easy identification
    token = f"ragflow-ws-{secrets.token_urlsafe(32)}"

    expires_at = None
    if body.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_days)

    key_id = get_uuid()
    ApiKeyScopeService.save(**{
        "id": key_id,
        "workspace_id": ws_id,
        "token": token,
        "permissions": body.permissions,
        "name": body.name,
        "expires_at": expires_at,
        "created_by": user_id,
    })

    # Also create a RAGFlow APIToken so the key works with @token_required routes
    # APIToken has composite PK (tenant_id, token), no 'id' column
    from api.db.db_models import APIToken, DB
    with DB.connection_context():
        APIToken.create(
            token=token,
            tenant_id=ws.tenant_id,
            dialog_id="",
        )

    ok, key = ApiKeyScopeService.get_by_id(key_id)
    return _key_to_response(key, include_token=True)


@router.delete("/workspaces/{ws_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(ws_id: str, key_id: str, user_id: str = Depends(get_current_user_id)):
    """Revoke an API key."""
    from management.server.auth.dependencies import require_ws_admin
    require_ws_admin(ws_id, user_id)

    from api.db.services.workspace_service import ApiKeyScopeService
    ok, key = ApiKeyScopeService.get_by_id(key_id)
    if not ok or not key or key.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="API key not found")

    ApiKeyScopeService.update_by_id(key_id, {"status": "0"})

    # Also remove the RAGFlow APIToken (no status column on this table)
    from api.db.db_models import APIToken, DB
    with DB.connection_context():
        APIToken.delete().where(APIToken.token == key.token).execute()
