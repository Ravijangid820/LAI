"""Async PostgreSQL connection pool with pgvector support.

Provides connection pooling via asyncpg. Used by all domain packages
that need database access.
"""

import asyncpg
from pgvector.asyncpg import register_vector

from lai.core.config import get_settings
from lai.core.exceptions import DatabaseError
from lai.core.logging import get_logger

logger = get_logger("lai.infra.database")

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """Initialize the asyncpg connection pool with pgvector support."""
    global _pool
    if _pool is not None:
        return _pool

    settings = get_settings().db

    async def _init_conn(conn: asyncpg.Connection) -> None:
        await register_vector(conn)

    try:
        _pool = await asyncpg.create_pool(
            dsn=settings.dsn,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            init=_init_conn,
        )
        logger.info(
            "Database pool initialized",
            extra={"host": settings.host, "port": settings.port, "min": settings.pool_min_size, "max": settings.pool_max_size},
        )
    except Exception as e:
        logger.error("Failed to initialize database pool: %s", e)
        raise DatabaseError(f"Cannot connect to PostgreSQL: {e}") from e

    return _pool


async def close_pool() -> None:
    """Close the connection pool gracefully."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database pool closed")


def get_pool() -> asyncpg.Pool:
    """Get the current pool. Raises if not initialized."""
    if _pool is None:
        raise DatabaseError("Database pool not initialized. Call init_pool() first.")
    return _pool


async def check_health() -> dict:
    """Check database connectivity and pgvector status."""
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            pgvector = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            count = await conn.fetchval("SELECT COUNT(*) FROM chunks")
            return {"status": "healthy", "pgvector": pgvector, "chunk_count": count}
    except Exception as e:
        logger.error("Database health check failed: %s", e)
        return {"status": "unhealthy", "error": str(e)}
