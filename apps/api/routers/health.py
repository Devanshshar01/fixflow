from datetime import datetime, timezone

from fastapi import APIRouter

from db import check_db_connection
from config import get_settings
from logger import logger

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check():
    settings = get_settings()
    db_ok = await check_db_connection()

    status = "healthy" if db_ok else "degraded"

    if not db_ok:
        logger.warning("Health check: database unreachable")

    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.environment,
        "checks": {
            "database": "ok" if db_ok else "unreachable",
        },
    }