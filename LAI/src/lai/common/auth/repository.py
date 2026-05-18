"""asyncpg repositories for users, refresh tokens, and reset tokens.

Every SQL statement that touches the auth tables lives here. Routes
import the repository, never raw SQL. That keeps the
parameter-binding style consistent and makes it trivially auditable
which queries do and don't filter by user_id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg

__all__ = [
    "RefreshTokenRecord",
    "RefreshTokenRepository",
    "ResetTokenRecord",
    "ResetTokenRepository",
    "UserRecord",
    "UserRepository",
    "canonical_email",
]


def canonical_email(raw: str) -> str:
    """Normalise an email address for unique-key comparison.

    Lowercases and trims surrounding whitespace. Does **not** apply
    domain-specific normalisation (gmail dot-stripping, plus-tags) —
    those are policy decisions and would surprise legitimate users
    who own those addresses on other providers.
    """
    return raw.strip().lower()


# ── User ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class UserRecord:
    """A row from the ``users`` table.

    Mirrors the schema in migration 001 exactly. ``password_hash`` is
    the bcrypt digest; callers must never log this value.
    """

    id: UUID
    email: str
    email_canonical: str
    password_hash: str
    full_name: str
    company: str | None
    role: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


_USER_COLS: Final[str] = (
    "id, email, email_canonical, password_hash, full_name, company, "
    "role, status, created_at, updated_at, last_login_at"
)


class UserRepository:
    """CRUD for the ``users`` table.

    Methods accept an :class:`asyncpg.Connection` so the caller controls
    transaction boundaries (a route that issues both a user insert and a
    refresh-token insert in one transaction stays atomic).
    """

    __slots__ = ()

    async def get_by_id(
        self,
        conn: "asyncpg.Connection",
        user_id: UUID,
    ) -> UserRecord | None:
        row = await conn.fetchrow(
            f"SELECT {_USER_COLS} FROM users WHERE id = $1",
            user_id,
        )
        return _row_to_user(row) if row is not None else None

    async def get_by_email(
        self,
        conn: "asyncpg.Connection",
        email: str,
    ) -> UserRecord | None:
        row = await conn.fetchrow(
            f"SELECT {_USER_COLS} FROM users WHERE email_canonical = $1",
            canonical_email(email),
        )
        return _row_to_user(row) if row is not None else None

    async def create(
        self,
        conn: "asyncpg.Connection",
        *,
        email: str,
        password_hash: str,
        full_name: str,
        company: str | None,
        role: str = "user",
    ) -> UserRecord:
        """Insert a new user. Caller must have checked for collision.

        Raises:
            asyncpg.UniqueViolationError: If ``email_canonical``
                collides. Caller should catch and map to
                :class:`EmailAlreadyExistsError`.
        """
        row = await conn.fetchrow(
            f"""
            INSERT INTO users (email, email_canonical, password_hash, full_name, company, role)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {_USER_COLS}
            """,
            email,
            canonical_email(email),
            password_hash,
            full_name,
            company,
            role,
        )
        assert row is not None  # noqa: S101 — RETURNING always yields a row
        return _row_to_user(row)

    async def update_password_hash(
        self,
        conn: "asyncpg.Connection",
        user_id: UUID,
        password_hash: str,
    ) -> None:
        await conn.execute(
            "UPDATE users SET password_hash = $1, updated_at = NOW() WHERE id = $2",
            password_hash,
            user_id,
        )

    async def touch_last_login(
        self,
        conn: "asyncpg.Connection",
        user_id: UUID,
    ) -> None:
        await conn.execute(
            "UPDATE users SET last_login_at = NOW() WHERE id = $1",
            user_id,
        )


def _row_to_user(row: "asyncpg.Record") -> UserRecord:
    return UserRecord(
        id=row["id"],
        email=row["email"],
        email_canonical=row["email_canonical"],
        password_hash=row["password_hash"],
        full_name=row["full_name"],
        company=row["company"],
        role=row["role"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )


# ── Refresh token ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    """A row from the ``refresh_tokens`` table."""

    id: UUID
    user_id: UUID
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class RefreshTokenRepository:
    """CRUD for ``refresh_tokens``."""

    __slots__ = ()

    async def create(
        self,
        conn: "asyncpg.Connection",
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshTokenRecord:
        row = await conn.fetchrow(
            """
            INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
            VALUES ($1, $2, $3)
            RETURNING id, user_id, token_hash, issued_at, expires_at, revoked_at
            """,
            user_id,
            token_hash,
            expires_at,
        )
        assert row is not None  # noqa: S101
        return _row_to_refresh(row)

    async def get_active_by_hash(
        self,
        conn: "asyncpg.Connection",
        token_hash: str,
    ) -> RefreshTokenRecord | None:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, token_hash, issued_at, expires_at, revoked_at
            FROM refresh_tokens
            WHERE token_hash = $1 AND revoked_at IS NULL AND expires_at > NOW()
            """,
            token_hash,
        )
        return _row_to_refresh(row) if row is not None else None

    async def revoke_by_hash(
        self,
        conn: "asyncpg.Connection",
        token_hash: str,
    ) -> None:
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = NOW()
            WHERE token_hash = $1 AND revoked_at IS NULL
            """,
            token_hash,
        )

    async def revoke_all_for_user(
        self,
        conn: "asyncpg.Connection",
        user_id: UUID,
    ) -> None:
        """Revoke every active refresh token for a user.

        Called after a password reset so every device must re-login —
        the v1 substitute for a refresh-rotation chain.
        """
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = NOW()
            WHERE user_id = $1 AND revoked_at IS NULL
            """,
            user_id,
        )


def _row_to_refresh(row: "asyncpg.Record") -> RefreshTokenRecord:
    return RefreshTokenRecord(
        id=row["id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
    )


# ── Password reset token ────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResetTokenRecord:
    """A row from the ``password_reset_tokens`` table."""

    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None


class ResetTokenRepository:
    """CRUD for ``password_reset_tokens``."""

    __slots__ = ()

    async def create(
        self,
        conn: "asyncpg.Connection",
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> ResetTokenRecord:
        row = await conn.fetchrow(
            """
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
            VALUES ($1, $2, $3)
            RETURNING id, user_id, token_hash, expires_at, consumed_at, created_at
            """,
            user_id,
            token_hash,
            expires_at,
        )
        assert row is not None  # noqa: S101
        return _row_to_reset(row)

    async def consume(
        self,
        conn: "asyncpg.Connection",
        token_hash: str,
    ) -> ResetTokenRecord | None:
        """Atomically mark a reset token consumed.

        Returns the row if and only if the token was unconsumed and
        unexpired *at the moment of the UPDATE*. Two concurrent
        ``/auth/reset-password`` calls with the same token therefore
        observe exactly-one success; the other gets ``None``.
        """
        row = await conn.fetchrow(
            """
            UPDATE password_reset_tokens
            SET consumed_at = NOW()
            WHERE token_hash = $1
              AND consumed_at IS NULL
              AND expires_at > NOW()
            RETURNING id, user_id, token_hash, expires_at, consumed_at, created_at
            """,
            token_hash,
        )
        return _row_to_reset(row) if row is not None else None


def _row_to_reset(row: "asyncpg.Record") -> ResetTokenRecord:
    return ResetTokenRecord(
        id=row["id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
        created_at=row["created_at"],
    )
