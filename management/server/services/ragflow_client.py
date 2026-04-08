"""
RAGFlow API client wrapper.

Used for operations that require calling RAGFlow's HTTP API rather than
direct DB access (e.g., triggering document processing, fetching LLM config).

For most CRUD operations, we access the shared MySQL DB directly via
the Peewee models. This client is for cases where RAGFlow's internal
logic must be invoked through its API.
"""
import httpx

from management.server.config import settings


class RAGFlowClient:
    def __init__(self, base_url: str = None, timeout: float = 30.0):
        self.base_url = (base_url or settings.RAGFLOW_API_URL).rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def health_check(self) -> bool:
        """Check if RAGFlow server is reachable."""
        try:
            resp = httpx.get(self._url("/v1/health"), timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def list_datasets(self, tenant_id: str, token: str) -> list[dict]:
        """List datasets for a tenant via RAGFlow API."""
        resp = httpx.get(
            self._url("/v1/datasets"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    def get_tenant_llm_config(self, tenant_id: str) -> list[dict]:
        """Get LLM configuration for a tenant (direct DB access)."""
        from api.db.db_models import DB, TenantLLM
        with DB.connection_context():
            configs = list(
                TenantLLM.select()
                .where(TenantLLM.tenant_id == tenant_id)
            )
            return [
                {
                    "llm_factory": getattr(c, "llm_factory", getattr(c, "provider_name", "")),
                    "model_type": getattr(c, "model_type", ""),
                    "llm_name": getattr(c, "llm_name", ""),
                    "api_key": getattr(c, "api_key", ""),
                }
                for c in configs
            ]


# Singleton
ragflow_client = RAGFlowClient()
