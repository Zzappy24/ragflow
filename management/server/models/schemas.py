"""
Pydantic schemas for the admin panel API.
"""
from datetime import datetime
from pydantic import BaseModel, Field


# -- Auth -------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfo(BaseModel):
    id: str
    email: str
    nickname: str | None = None
    is_superuser: bool = False
    orgs: list[dict] = []  # [{org_id, org_name, role}]


# -- Organisation -----------------------------------------------------------

class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255, pattern=r"^[a-z0-9\-]+$")
    max_users: int = Field(default=50, ge=1)
    max_workspaces: int = Field(default=10, ge=1)
    max_datasets: int = Field(default=100, ge=1)
    max_documents: int = Field(default=10000, ge=1)
    max_storage_gb: int = Field(default=50, ge=1)


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    max_users: int | None = Field(default=None, ge=1)
    max_workspaces: int | None = Field(default=None, ge=1)
    max_datasets: int | None = Field(default=None, ge=1)
    max_documents: int | None = Field(default=None, ge=1)
    max_storage_gb: int | None = Field(default=None, ge=1)
    llm_config: dict | None = None
    settings_json: dict | None = None


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str
    max_users: int
    max_workspaces: int
    max_datasets: int
    max_documents: int
    max_storage_gb: int
    llm_config: dict | None = None
    created_by: str | None = None
    create_time: datetime | None = None


# -- Workspace ---------------------------------------------------------------

class WsCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=1024)


class WsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    status: str | None = Field(default=None, pattern=r"^[01]$")
    settings_json: dict | None = None


class WsResponse(BaseModel):
    id: str
    org_id: str
    tenant_id: str
    name: str
    description: str = ""
    status: str = "1"
    created_by: str | None = None
    create_time: datetime | None = None


# -- Members -----------------------------------------------------------------

class MemberAdd(BaseModel):
    email: str
    role: str = Field(default="viewer", pattern=r"^(org_admin|member|ws_admin|editor|viewer)$")


class MemberUpdateRole(BaseModel):
    role: str = Field(pattern=r"^(org_admin|member|ws_admin|editor|viewer)$")


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: str | None = None
    nickname: str | None = None
    role: str
    create_time: datetime | None = None


# -- Groups ------------------------------------------------------------------

class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=1024)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)


class GroupResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    description: str = ""
    create_time: datetime | None = None


# -- API Keys ----------------------------------------------------------------

class ApiKeyCreate(BaseModel):
    name: str = Field(default="", max_length=255)
    permissions: list[str] = []  # list of Permission values
    expires_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyResponse(BaseModel):
    id: str
    workspace_id: str
    name: str = ""
    token: str | None = None  # only returned on creation
    token_hint: str | None = None  # masked preview, e.g. ragflow-ws-uaKQ...n99Fs
    permissions: list[str] = []
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    status: str = "1"
    create_time: datetime | None = None


# -- Audit -------------------------------------------------------------------

class AuditQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    user_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None


# -- System ------------------------------------------------------------------

class SystemStats(BaseModel):
    total_orgs: int = 0
    total_workspaces: int = 0
    total_users: int = 0
    total_datasets: int = 0
    total_documents: int = 0
