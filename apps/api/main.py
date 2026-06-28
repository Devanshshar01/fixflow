from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from config import get_settings
from db import lifespan_db, AsyncSessionLocal
from logger import logger, setup_logging
from models.database import WorkflowRun
from routers import health, webhooks, repositories, analytics
from routers import auth


async def _requeue_stuck_runs() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    async with AsyncSessionLocal() as db:
        logger.info("Startup re-queue: start")
        try:
            logger.info("Startup re-queue: querying stuck runs")
            result = await db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.status.in_(["analyzing", "pending"]),
                    WorkflowRun.triggered_at < cutoff,
                )
            )
            stuck_runs = result.scalars().all()
            logger.info("Startup re-queue: query completed")

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
            )
        except Exception as exc:
            logger.error("Startup re-queue: failed", error=str(exc), exc_info=True)


def _preload_rule_engine() -> None:
    try:
        from services.rule_engine import _load_patterns
        patterns = _load_patterns()
        logger.info("Rule engine pre-loaded", pattern_count=len(patterns))
        if len(patterns) == 0:
            logger.warning("Rule engine has 0 patterns")
    except Exception as exc:
        logger.error("Rule engine pre-load failed", error=str(exc), exc_info=True)


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
        _preload_rule_engine()
        logger.info("FixFlow API ready")
        yield

    logger.info("FixFlow API shut down cleanly")


def create_app() -> FastAPI:
    app = FastAPI(
        title="FixFlow API",
        description="GitHub App that debugs your CI failures",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ── CORS — must be added before any other middleware ───────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://fixflow-henna-alpha.vercel.app",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        max_age=86400,
    )

    # ── Explicit OPTIONS handler — catches preflight before routing ────────────
    @app.options("/{rest_of_path:path}")
    async def preflight_handler(request: Request, rest_of_path: str):
        return JSONResponse(
            content={},
            headers={
                "Access-Control-Allow-Origin": "https://fixflow-henna-alpha.vercel.app",
                "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Max-Age": "86400",
            },
        )

    # ── Routers ────────────────────────────────────────────────────────────────
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