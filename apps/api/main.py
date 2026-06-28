from routers import health, webhooks, repositories, analytics
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from config import get_settings
from db import lifespan_db, AsyncSessionLocal
from logger import logger, setup_logging
from models.database import WorkflowRun
from routers import health, webhooks, repositories, analytics
from routers import auth


async def _requeue_stuck_runs() -> None:
    """
    On startup: reset any WorkflowRun stuck in 'analyzing' or 'pending'
    for more than 2 minutes. These are jobs lost during a previous crash.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WorkflowRun).where(
                WorkflowRun.status.in_(["analyzing", "pending"]),
                WorkflowRun.triggered_at < cutoff,
            )
        )
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


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    origins = (
    ["http://localhost:3000", "http://127.0.0.1:3000"]
    if not settings.is_production
    else [
        "https://fixflow.vercel.app",
    ]
)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=r"https://.*\.vercel\.app",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(repositories.router)
    app.include_router(analytics.router)
    app.include_router(auth.router)

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