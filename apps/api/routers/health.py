from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from db import check_db_connection, get_db
from config import get_settings
from logger import logger
from models.database import WorkflowRun

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check(db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    db_ok = await check_db_connection()

    # Runs stuck in analyzing/pending for >5 minutes indicate a pipeline issue
    five_min_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    stuck_result = await db.execute(
        select(func.count()).select_from(WorkflowRun).where(
            WorkflowRun.status.in_(["pending", "analyzing"]),
            WorkflowRun.triggered_at < five_min_ago,
        )
    )
    stuck_count = stuck_result.scalar_one() or 0

    status = "healthy"
    if not db_ok:
        status = "degraded"
    elif stuck_count > 5:
        status = "degraded"

    if not db_ok:
        logger.warning("Health check: database unreachable")
    if stuck_count > 0:
        logger.warning("Health check: stuck runs detected", count=stuck_count)

    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.environment,
        "checks": {
            "database": "ok" if db_ok else "unreachable",
            "stuck_runs": stuck_count,
            "ai_provider": settings.ai_provider,
        },
    }