"""Search-domain database queries.

Handles schema management for multi-tenant search.
"""

from lai.core.logging import get_logger
from lai.infra.database import get_pool

logger = get_logger("lai.search.repository")


async def get_user_schemas(user_id: str) -> list[str]:
    """Get search schemas for a user: always public + user's personal schema if it exists."""
    pool = get_pool()
    schemas = ["public"]
    user_schema = f"user_{user_id.replace('-', '_')}"

    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = $1)",
            user_schema,
        )
        if exists:
            schemas.append(user_schema)
            logger.debug("User %s has personal schema %s", user_id, user_schema)

    return schemas


async def get_chunk_by_id(chunk_id: str, schema: str = "public") -> dict | None:
    """Fetch a single chunk by ID."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {schema}.chunks WHERE id = $1", chunk_id)
        return dict(row) if row else None
