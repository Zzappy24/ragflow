"""
Configuration for the management admin panel.
"""
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # JWT
    JWT_SECRET: str = os.getenv("ADMIN_JWT_SECRET", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS — override via ADMIN_CORS_ORIGINS="https://admin.example.com,https://app.example.com"
    CORS_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv("ADMIN_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")
        if o.strip()
    ]

    # RAGFlow API (for provisioning operations that need RAGFlow's internal API)
    RAGFLOW_API_URL: str = os.getenv("RAGFLOW_API_URL", "http://localhost:9380")

    # RAGFlow user-facing base URL — used to construct bridge launch URLs.
    # Dev: the RAGFlow frontend (vite/umi). Prod: your customer-facing domain.
    RAGFLOW_BASE_URL: str = os.getenv("RAGFLOW_BASE_URL", "http://localhost:9222")

    # LiteLLM management API (for Code product's headless orchestration)
    LITELLM_BASE_URL: str = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
    LITELLM_MASTER_KEY: str = os.getenv("LITELLM_MASTER_KEY", "")  # K8s Secret in prod; never logged, never returned by any route
    # Public URL clients plug into Kilo/OpenCode/Cline (shown in the panel
    # next to their keys). Prod: the Envoy hostname fronting LiteLLM's /v1,
    # e.g. "https://code.cyllene.cloud/v1". Empty = hidden in the UI.
    CODE_GATEWAY_PUBLIC_URL: str = os.getenv("ADMIN_CODE_GATEWAY_PUBLIC_URL", "")

    INVITE_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 48

    class Config:
        env_prefix = "ADMIN_"


settings = Settings()
