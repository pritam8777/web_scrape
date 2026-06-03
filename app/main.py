"""
FastAPI application factory – entry point for the Web Scraper Platform.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from app.config import get_settings, PROJECT_ROOT
from app.database import engine, Base
from app.routes.scrape import router as scrape_router

settings = get_settings()

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format=settings.LOG_FORMAT,
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ──────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: create tables on startup, clean up on shutdown."""
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)

    # Create all tables (for dev; use Alembic migrations in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables ensured.")
    yield

    # Shutdown
    await engine.dispose()
    logger.info("Application shutdown complete.")


# ── App factory ────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="""
## Web Scraper Platform

A production-ready API for scraping company, school, college, and institute websites.

### Features
- **Single URL Scraping** – POST `/api/v1/scrape/url`
- **Bulk URL Scraping** – POST `/api/v1/scrape/bulk`
- **File Upload** – POST `/api/v1/scrape/upload` (.xlsx / .csv)
- **Job Status** – GET `/api/v1/scrape/status/{job_id}`
- **Results** – GET `/api/v1/scrape/result/{job_id}`
- **Export** – GET `/api/v1/export/{job_id}` (JSON, CSV, Excel)
        """,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ── Routers ───────────────────────────────────────────────────
    app.include_router(scrape_router, prefix=settings.API_PREFIX)

    # ── Static Files & UI ─────────────────────────────────────────
    static_dir = PROJECT_ROOT / "app" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir), html=True), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        """Redirect root to the web UI."""
        return RedirectResponse(url="/static/index.html")

    # ── Health check ──────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    async def health_check():
        """Basic health check endpoint."""
        return {"status": "healthy", "app": settings.APP_NAME, "version": settings.APP_VERSION}

    return app


# ── Application instance ───────────────────────────────────────────────────
app = create_app()
