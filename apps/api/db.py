from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import ssl

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import get_settings
from logger import logger


class Base(DeclarativeBase):
    pass


def _create_engine():
    settings = get_settings()

    ssl_context = ssl.create_default_context()

    return create_async_engine(
        settings.database_url,
        echo=not settings.is_production,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "ssl": ssl_context,
        },
    )


engine = _create_engine()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_db_connection() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database connection check failed", error=str(exc))
        return False


@asynccontextmanager
async def lifespan_db():
    logger.info("Initializing database connection pool")
    yield
    await engine.dispose()
    logger.info("Database connection pool disposed")