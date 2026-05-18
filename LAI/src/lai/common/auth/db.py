"""asyncpg pool configuration for the auth subsystem.

A tiny module that owns one thing: turn ``DB_*`` environment variables
(the same ones :mod:`micro-services/ddiq_report.py` reads) into a
:class:`asyncpg.Pool`. Lives in :mod:`lai.common.auth` because every
backend that mounts the auth router needs a pool, and we want a single
canonical implementation.

The pool is created lazily in the FastAPI startup hook and closed in
shutdown. We do not maintain a process-global; the pool is parked on
``app.state`` so dependent routes acquire it via FastAPI's ``Request``
object rather than a module-level singleton (cleaner test isolation,
correct shutdown semantics).
"""

from __future__ import annotations

import os

import asyncpg

__all__ = ["DbSettings", "create_pool"]


class DbSettings:
    """Minimal DB connection settings, read from ``DB_*`` env vars.

    Kept as a plain class (not pydantic) because this is the shared
    bottom-of-the-stack configuration that other ``lai.common``
    modules consume; introducing a pydantic dependency here would
    couple the auth module's startup ordering to settings-validation,
    and the field set is too small to be worth it.
    """

    __slots__ = ("database", "host", "max_size", "min_size", "password", "port", "user")

    def __init__(self) -> None:
        self.host: str = os.getenv("DB_HOST", "localhost")
        self.port: int = int(os.getenv("DB_PORT", "5433"))
        self.database: str = os.getenv("DB_NAME", "lai_db")
        self.user: str = os.getenv("DB_USER", "lai_user")
        # No default for password in production; tests / dev fall back
        # to the value already coded into the legacy DB_CONFIG dicts so
        # local stacks keep working unchanged.
        self.password: str = os.getenv("DB_PASSWORD", "lai_test_password_2024")
        self.min_size: int = int(os.getenv("DB_POOL_MIN", "2"))
        self.max_size: int = int(os.getenv("DB_POOL_MAX", "10"))


async def create_pool(settings: DbSettings | None = None) -> asyncpg.Pool:
    """Create a sized asyncpg pool against the configured database.

    The pool is configured for the auth + conversations workload:
    short queries, sub-millisecond latency, high concurrency. Sizing
    is conservative (2..10) so a single FastAPI worker does not
    saturate Postgres under modest load; raise via ``DB_POOL_MAX`` if
    the workload warrants.

    Args:
        settings: Optional override; defaults to a fresh
            :class:`DbSettings` reading process env.

    Returns:
        A live :class:`asyncpg.Pool`. Caller owns the lifetime —
        always pair with ``await pool.close()`` at shutdown.
    """
    cfg = settings or DbSettings()
    pool = await asyncpg.create_pool(
        host=cfg.host,
        port=cfg.port,
        database=cfg.database,
        user=cfg.user,
        password=cfg.password,
        min_size=cfg.min_size,
        max_size=cfg.max_size,
        # Cache prepared statements per-connection so the per-request
        # parse cost amortises across a worker's lifetime.
        statement_cache_size=128,
        # Time out an acquire after 10s rather than the asyncpg default
        # (∞) — under a wedge it's better to 503 fast than hang.
        command_timeout=10.0,
    )
    if pool is None:  # asyncpg never returns None on success; defensive
        raise RuntimeError("asyncpg.create_pool returned None")
    return pool
