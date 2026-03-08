"""User database operations."""

from uuid import uuid4

from lai.auth.jwt import hash_password
from lai.core.logging import get_logger
from lai.infra.database import get_pool

logger = get_logger("lai.auth.repository")


async def create_user(email: str, password: str) -> dict:
    """Create a new user. Returns user dict."""
    pool = get_pool()
    user_id = str(uuid4())
    hashed = hash_password(password)

    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (id, email, password_hash)
               VALUES ($1, $2, $3)""",
            user_id, email, hashed,
        )
    logger.info("User created: %s (%s)", user_id, email)
    return {"id": user_id, "email": email}


async def get_user_by_email(email: str) -> dict | None:
    """Find user by email."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, email, password_hash FROM users WHERE email = $1", email)
        return dict(row) if row else None


async def get_user_by_id(user_id: str) -> dict | None:
    """Find user by ID."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, email FROM users WHERE id = $1", user_id)
        return dict(row) if row else None
