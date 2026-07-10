"""
Pydantic schemas for the admin panel API.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    orgs: list[dict] = []         # [{org_id, org_name, role}]
    # Workspaces the caller belongs to (any role). Used by the management UI
    # to land ws_admin-only users directly on their workspace without ever
    # showing the org-level pages.
    workspaces: list[dict] = []   # [{ws_id, ws_name, org_id, role}]


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
    is_active: str = "1"  # '0' = invitation jamais consommée


# -- User provisioning -------------------------------------------------------

class UserProvision(BaseModel):
    """Top-down user creation from the admin panel.

    Org is mandatory (no ghost users floating outside an org). Workspace is
    optional (org-billing admins may not belong to any workspace yet).
    """
    email: str = Field(min_length=3, max_length=255)
    nickname: str = Field(min_length=1, max_length=255)
    org_id: str
    org_role: str = Field(pattern=r"^(org_admin|member)$")
    ws_id: str | None = None
    ws_role: str | None = Field(default=None, pattern=r"^(ws_admin|editor|viewer)$")


class UserProvisionResponse(BaseModel):
    user_id: str
    email: str
    invite_url: str
    expires_in: int
    email_sent: bool = False


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


# -- Workspace LLM models ----------------------------------------------------

class WsLlmProviderAdd(BaseModel):
    llm_factory: str = Field(min_length=1, max_length=128)
    llm_name: str = Field(min_length=1, max_length=128)
    model_type: str = Field(min_length=1, max_length=128)
    api_key: str | None = Field(default=None)
    api_base: str | None = Field(default=None, max_length=255)
    max_tokens: int = Field(default=8192, ge=1)


class WsLlmProviderResponse(BaseModel):
    llm_factory: str
    llm_name: str
    model_type: str
    api_base: str = ""
    max_tokens: int = 8192
    used_tokens: int = 0
    status: str = "1"


class WsLlmProviderUpdate(BaseModel):
    api_key: str | None = None
    api_base: str | None = None
    max_tokens: int | None = Field(default=None, ge=1)


class WsLlmVerifyRequest(BaseModel):
    llm_factory: str = Field(min_length=1, max_length=128)
    llm_name: str = Field(min_length=1, max_length=128)
    model_type: str = Field(min_length=1, max_length=128)
    api_key: str | None = None
    api_base: str | None = None


class WsLlmVerifyResponse(BaseModel):
    ok: bool
    message: str = ""


class WsLlmDefaultsSet(BaseModel):
    llm_id: str | None = None
    embd_id: str | None = None
    asr_id: str | None = None
    img2txt_id: str | None = None
    rerank_id: str | None = None
    tts_id: str | None = None


class WsLlmDefaultsResponse(BaseModel):
    llm_id: str = ""
    embd_id: str = ""
    asr_id: str = ""
    img2txt_id: str = ""
    rerank_id: str = ""
    tts_id: str = ""


# -- System ------------------------------------------------------------------

class SystemStats(BaseModel):
    total_orgs: int = 0
    total_workspaces: int = 0
    total_users: int = 0
    total_datasets: int = 0
    total_documents: int = 0


# -- Code product -------------------------------------------------------------

class CodeEntitlementUpsert(BaseModel):
    status: Literal["active", "suspended"]
    org_code_budget: float = Field(ge=0)
    budget_period: Literal["1d", "7d", "1mo", "1y"] = "1mo"


class CodeTeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    max_budget: float = Field(gt=0)
    model_access: list[str] = []


class CodeTeamUpdate(BaseModel):
    max_budget: float = Field(gt=0)


class CodeTeamAdminAdd(BaseModel):
    # NOTE: email-validator is not a project dependency (not in
    # management/server/requirements.txt), so pydantic's EmailStr cannot be
    # used here without adding it. A minimal manual check stands in for it.
    email: str = Field(min_length=3)

    @field_validator("email")
    @classmethod
    def _basic_email_shape(cls, v: str) -> str:
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("invalid email address")
        return v


class CodeKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    owner_user_id: str | None = None


class CodeKeyBulkCreate(BaseModel):
    emails: list[str] = Field(min_length=1)


class CodeClaimRequest(BaseModel):
    token: str = Field(min_length=1)
