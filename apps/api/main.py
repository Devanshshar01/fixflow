from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from db import lifespan_db
from logger import logger, setup_logging
from routers import health, webhooks, repositories


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────────────
    setup_logging()
    settings = get_settings()

    logger.info(
        "FixFlow API starting",
        environment=settings.environment,
        ai_provider=settings.ai_provider,
    )

    async with lifespan_db():
        logger.info("FixFlow API ready")
        yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("FixFlow API shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="FixFlow API",
        description="GitHub App that debugs your CI failures",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── CORS ───────────────────────────────────────────────────────────────────
    # In production, lock this down to your Vercel domain
    origins = (
        ["http://localhost:3000", "http://127.0.0.1:3000"]
        if not settings.is_production
        else ["https://fixflow.vercel.app"]  # Update with your real domain
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    # ── Routers ────────────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(repositories.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="debug",
    )