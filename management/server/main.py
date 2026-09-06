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

if not settings.JWT_SECRET:
    raise RuntimeError(
        "ADMIN_JWT_SECRET environment variable is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    # Sous uvicorn le root logger n'a aucun handler : les INFO applicatifs
    # (dont le log de démarrage du scheduler housekeeping — notre signal de
    # vie, leçon asgi.py 2026-06-30) sont avalés par le lastResort (WARNING+).
    # basicConfig est un no-op si des handlers existent déjà (tests/pytest).
    logging.basicConfig(level=os.getenv("ADMIN_LOG_LEVEL", "INFO"))

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

    # CUSTOM B2B SaaS — Code product : housekeeping scheduler in-process.
    # Sleep-first (pas de run au boot), DB.lock dans housekeeping() -> multi-replica safe.
    # Désactivable via ADMIN_CODE_SCHEDULER=0 (tests / TestClient).
    import asyncio
    scheduler_task = None
    if os.getenv("ADMIN_CODE_SCHEDULER", "1") == "1":
        # 15 min par défaut : cadence des relevés tokens/erreurs — le marché
        # affiche l'usage en ~10 min-1 h (Anthropic/OpenAI/Datadog) ; 96
        # lectures /spend/logs par jour restent triviales côté gateway.
        interval = int(os.getenv("ADMIN_CODE_SCHEDULER_INTERVAL_S", "900"))

        async def _code_housekeeping_loop():
            from management.server.services.code_housekeeping import housekeeping
            while True:
                await asyncio.sleep(interval)
                try:
                    report = await asyncio.to_thread(housekeeping)
                    logging.info(f"code housekeeping run: {report}")
                except Exception:
                    logging.exception("code housekeeping run failed")

        scheduler_task = asyncio.get_running_loop().create_task(_code_housekeeping_loop())
        logging.info(f"code housekeeping scheduler started (interval={interval}s)")
    else:
        logging.warning("code housekeeping scheduler DISABLED (ADMIN_CODE_SCHEDULER=0)")

    yield

    if scheduler_task is not None:
        import contextlib

        scheduler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler_task


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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
        "X-Workspace-Id",
    ],
)

# CUSTOM B2B SaaS — purge physique non confirmée par ragflow-api (chunks,
# vecteurs, fichiers) : 502 explicite, rien n'a été effacé côté structure et
# l'admin peut relancer. Sans ce handler, un 500 opaque.
from management.server.services.tenant_purge_client import TenantPurgeError


@app.exception_handler(TenantPurgeError)
async def _tenant_purge_error_handler(_request, exc: TenantPurgeError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=502, content={"detail": str(exc)})


# Register routers
from management.server.routers import (
    auth,
    orgs,
    workspaces,
    members,
    users,
    groups,
    api_keys,
    audit,
    system,
    models,
    archives,
    stats,
    code,
    org_invites,
)

app.include_router(auth.router, prefix="/api/admin/auth", tags=["Auth"])
app.include_router(orgs.router, prefix="/api/admin/orgs", tags=["Organisations"])
app.include_router(workspaces.router, prefix="/api/admin", tags=["Workspaces"])
app.include_router(members.router, prefix="/api/admin", tags=["Members"])
app.include_router(org_invites.router, prefix="/api/admin", tags=["Org Invitations"])
app.include_router(users.router, prefix="/api/admin", tags=["Users"])
app.include_router(groups.router, prefix="/api/admin", tags=["Groups"])
app.include_router(api_keys.router, prefix="/api/admin", tags=["API Keys"])
app.include_router(audit.router, prefix="/api/admin", tags=["Audit"])
app.include_router(system.router, prefix="/api/admin/system", tags=["System"])
app.include_router(models.router, prefix="/api/admin", tags=["Workspace Models"])
app.include_router(archives.router, prefix="/api/admin", tags=["Archives"])
app.include_router(stats.router, prefix="/api/admin", tags=["Stats"])
app.include_router(code.router, prefix="/api/admin", tags=["Code Product"])
