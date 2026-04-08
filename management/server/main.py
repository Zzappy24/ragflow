"""
Management Admin Panel — FastAPI entry point.

Runs as a separate service from RAGFlow, sharing the same MySQL database.
Provides CRUD for organisations, workspaces, members, groups, API keys, audit.
"""
import sys
import os

# Ensure RAGFlow root is in PYTHONPATH so we can import api.db models/services
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from management.server.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    # Pre-init settings that would otherwise trigger ES/Infinity connections
    from common import settings as rag_settings
    if not rag_settings.SECRET_KEY:
        rag_settings.SECRET_KEY = settings.JWT_SECRET

    # Init DB tables
    from api.db.db_models import init_database_tables
    try:
        init_database_tables()
    except Exception as e:
        logging.warning(f"init_database_tables: {e} (tables may already exist)")

    # Set DOC_ENGINE to avoid ES connection attempts on lazy imports
    if not getattr(rag_settings, 'DOC_ENGINE', None):
        rag_settings.DOC_ENGINE = os.getenv("DOC_ENGINE", "infinity")
        rag_settings.DOC_ENGINE_INFINITY = True

    yield


app = FastAPI(
    title="RAGFlow Admin Panel",
    version="1.0.0",
    docs_url="/api/admin/docs",
    openapi_url="/api/admin/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
from management.server.routers import (
    auth,
    orgs,
    workspaces,
    members,
    groups,
    api_keys,
    audit,
    system,
)

app.include_router(auth.router, prefix="/api/admin/auth", tags=["Auth"])
app.include_router(orgs.router, prefix="/api/admin/orgs", tags=["Organisations"])
app.include_router(workspaces.router, prefix="/api/admin", tags=["Workspaces"])
app.include_router(members.router, prefix="/api/admin", tags=["Members"])
app.include_router(groups.router, prefix="/api/admin", tags=["Groups"])
app.include_router(api_keys.router, prefix="/api/admin", tags=["API Keys"])
app.include_router(audit.router, prefix="/api/admin", tags=["Audit"])
app.include_router(system.router, prefix="/api/admin/system", tags=["System"])
