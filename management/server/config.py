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

    # Bridge token (single-use auth handoff from admin panel → RAGFlow).
    # 120s gives enough headroom for slow networks; Redis SETNX is the real
    # single-use guard so extending the TTL doesn't open a replay window.
    BRIDGE_TOKEN_EXPIRE_SECONDS: int = 120

    # Invite token (single-use, mailed to freshly provisioned users so they
    # can set their initial password). 48h gives the recipient time to check
    # their email without leaving the token valid indefinitely; Redis SETNX
    # on the jti enforces single-use on top of that.
    INVITE_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 48

    class Config:
        env_prefix = "ADMIN_"


settings = Settings()
