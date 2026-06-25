import sys
from loguru import logger
from config import get_settings


def setup_logging() -> None:
    settings = get_settings()

    logger.remove()  # Remove default handler

    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "{message} | {extra}"
        ),
        serialize=settings.is_production,  # JSON in prod, pretty in dev
        backtrace=True,
        diagnose=not settings.is_production,
        enqueue=True,  # Thread-safe async logging
    )

    # Separate error sink — errors always go to stderr
    logger.add(
        sys.stderr,
        level="ERROR",
        format="{time:ISO8601} | {level} | {message} | {extra}",
        serialize=True,
        backtrace=True,
        enqueue=True,
    )


__all__ = ["logger", "setup_logging"]