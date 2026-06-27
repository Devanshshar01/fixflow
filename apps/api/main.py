from routers import health, webhooks, repositories, analytics
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import asyncio
import importlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from config import get_settings
from db import lifespan_db, AsyncSessionLocal
from logger import logger, setup_logging
from models.database import WorkflowRun


STARTUP_STEP_TIMEOUT_SECONDS = 15


async def _requeue_stuck_runs() -> None:
    """
    On startup: reset any WorkflowRun stuck in 'analyzing' or 'pending'
    for more than 2 minutes back to 'pending' with retry_count incremented.

    These are jobs that were in-flight when the server last crashed or restarted.
    They won't be re-triggered by GitHub (webhook already delivered), so we
    reset them here. A separate process can then re-drive them — for now we
    just reset status so they're visible and don't block analytics.
    """
    logger.info("Startup re-queue: start")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)

    async with AsyncSessionLocal() as db:
        logger.info("Startup re-queue: querying stuck runs")
        result = await db.execute(
            select(WorkflowRun).where(
                WorkflowRun.status.in_(["analyzing", "pending"]),
                WorkflowRun.triggered_at < cutoff,
            )
        )
        logger.info("Startup re-queue: query completed")
        stuck_runs = result.scalars().all()

        if not stuck_runs:
            logger.info("Startup re-queue: no stuck runs found")
            return

        for run in stuck_runs:
            run.status = "failed"
            run.error_detail = "server_restart_during_analysis"
            run.retry_count += 1

        await db.commit()

        logger.warning(
            "Startup re-queue: marked stuck runs as failed",
            count=len(stuck_runs),
            run_ids=[str(r.id) for r in stuck_runs],
        )
    logger.info("Startup re-queue: end")


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
        await _requeue_stuck_runs()
        logger.info("FixFlow API ready")
        yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    logger.info("FixFlow API shut down cleanly")


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
    origins = (
        ["http://localhost:3000", "http://127.0.0.1:3000"]
        if not settings.is_production
        else ["https://fixflow.vercel.app"]
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
    app.include_router(analytics.router)

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
