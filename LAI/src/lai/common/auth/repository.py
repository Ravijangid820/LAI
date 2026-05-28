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
    "InvitationRecord",
    "InvitationRepository",
    "OrganizationRecord",
    "OrganizationRepository",
    "OrganizationSummary",
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
    org_id: UUID | None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


_USER_COLS: Final[str] = (
    "id, email, email_canonical, password_hash, full_name, company, "
    "role, status, org_id, created_at, updated_at, last_login_at"
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
        conn: asyncpg.Connection,
        user_id: UUID,
    ) -> UserRecord | None:
        row = await conn.fetchrow(
            f"SELECT {_USER_COLS} FROM users WHERE id = $1",
            user_id,
        )
        return _row_to_user(row) if row is not None else None

    async def get_by_email(
        self,
        conn: asyncpg.Connection,
        email: str,
    ) -> UserRecord | None:
        row = await conn.fetchrow(
            f"SELECT {_USER_COLS} FROM users WHERE email_canonical = $1",
            canonical_email(email),
        )
        return _row_to_user(row) if row is not None else None

    async def create(
        self,
        conn: asyncpg.Connection,
        *,
        email: str,
        password_hash: str,
        full_name: str,
        company: str | None,
        role: str = "user",
        org_id: UUID | None = None,
    ) -> UserRecord:
        """Insert a new user. Caller must have checked for collision.

        ``org_id`` defaults to ``None`` (org-less): open signup creates a
        user with no firm; an admin places them later (MULTIUSER_PLAN.md
        §7). Admin-driven member provisioning passes an explicit ``org_id``.

        Raises:
            asyncpg.UniqueViolationError: If ``email_canonical``
                collides. Caller should catch and map to
                :class:`EmailAlreadyExistsError`.
        """
        row = await conn.fetchrow(
            f"""
            INSERT INTO users (email, email_canonical, password_hash, full_name, company, role, org_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING {_USER_COLS}
            """,
            email,
            canonical_email(email),
            password_hash,
            full_name,
            company,
            role,
            org_id,
        )
        assert row is not None
        return _row_to_user(row)

    async def update_password_hash(
        self,
        conn: asyncpg.Connection,
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
        conn: asyncpg.Connection,
        user_id: UUID,
    ) -> None:
        await conn.execute(
            "UPDATE users SET last_login_at = NOW() WHERE id = $1",
            user_id,
        )

    async def set_org_id(
        self,
        conn: asyncpg.Connection,
        user_id: UUID,
        org_id: UUID | None,
    ) -> bool:
        """Place a user in an org (``org_id``) or remove them (``None``).

        Returns ``True`` when a row was actually updated. Used by the admin
        membership endpoints — adding a user to a firm sets ``org_id``;
        removing them clears it so they fall back to the org-less holding
        state and stop seeing the firm's data. Their authored rows
        (``created_by``) stay with the firm.
        """
        res = await conn.execute(
            "UPDATE users SET org_id = $1, updated_at = NOW() WHERE id = $2",
            org_id,
            user_id,
        )
        return res.endswith(" 1")

    async def set_role(
        self,
        conn: asyncpg.Connection,
        user_id: UUID,
        role: str,
    ) -> bool:
        """Update a user's role. Caller is responsible for authorisation
        (only a super-admin may grant ``super_admin``; a firm admin may
        only promote within their own org and never beyond ``admin``)."""
        res = await conn.execute(
            "UPDATE users SET role = $1, updated_at = NOW() WHERE id = $2",
            role,
            user_id,
        )
        return res.endswith(" 1")

    async def search(
        self,
        conn: asyncpg.Connection,
        *,
        q: str,
        scope: str,
        viewer_org_id: UUID | None = None,
        limit: int = 20,
    ) -> list[UserRecord]:
        """Trigram-indexed name/email typeahead for the admin member picker.

        ``q`` is the search string; the leading 2-char minimum is enforced
        here to keep the result list meaningful and the query cheap.
        ``scope``:

        * ``'all'`` — every active user matching ``q``. Super-admin only.
        * ``'addable'`` — only users a firm admin may legitimately add:
          unaffiliated (``org_id IS NULL``) **or** already in
          ``viewer_org_id`` (so a firm admin can also surface their own
          team for management). Other firms' members are never returned —
          an admin cannot enumerate a rival firm's roster (GDPR §7.1).

        The query is index-accelerated by the GIN trigram indexes added in
        migration 002 (``users_full_name_trgm`` / ``users_email_trgm``).
        Results are similarity-ranked so the best match leads.
        """
        q_norm = q.strip().lower()
        if len(q_norm) < 2:
            return []
        if scope == "addable":
            rows = await conn.fetch(
                f"""
                SELECT {_USER_COLS}
                FROM users
                WHERE status = 'active'
                  AND (org_id IS NULL OR org_id = $2)
                  AND (lower(full_name) ILIKE '%' || $1 || '%'
                       OR email_canonical ILIKE '%' || $1 || '%')
                ORDER BY GREATEST(
                           similarity(lower(full_name), $1),
                           similarity(email_canonical, $1)
                         ) DESC,
                         full_name
                LIMIT $3
                """,
                q_norm,
                viewer_org_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_USER_COLS}
                FROM users
                WHERE status = 'active'
                  AND (lower(full_name) ILIKE '%' || $1 || '%'
                       OR email_canonical ILIKE '%' || $1 || '%')
                ORDER BY GREATEST(
                           similarity(lower(full_name), $1),
                           similarity(email_canonical, $1)
                         ) DESC,
                         full_name
                LIMIT $2
                """,
                q_norm,
                limit,
            )
        return [_row_to_user(r) for r in rows]


def _row_to_user(row: asyncpg.Record) -> UserRecord:
    return UserRecord(
        id=row["id"],
        email=row["email"],
        email_canonical=row["email_canonical"],
        password_hash=row["password_hash"],
        full_name=row["full_name"],
        company=row["company"],
        role=row["role"],
        status=row["status"],
        org_id=row["org_id"],
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
        conn: asyncpg.Connection,
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
        assert row is not None
        return _row_to_refresh(row)

    async def get_active_by_hash(
        self,
        conn: asyncpg.Connection,
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
        conn: asyncpg.Connection,
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
        conn: asyncpg.Connection,
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


def _row_to_refresh(row: asyncpg.Record) -> RefreshTokenRecord:
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
        conn: asyncpg.Connection,
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
        assert row is not None
        return _row_to_reset(row)

    async def consume(
        self,
        conn: asyncpg.Connection,
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


def _row_to_reset(row: asyncpg.Record) -> ResetTokenRecord:
    return ResetTokenRecord(
        id=row["id"],
        user_id=row["user_id"],
        token_hash=row["token_hash"],
        expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
        created_at=row["created_at"],
    )


# ── Organization ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class OrganizationRecord:
    """A row from the ``organizations`` table — the tenant boundary."""

    id: UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class OrganizationSummary:
    """An organisation with its current member count, for the admin listing.

    Returned by :meth:`OrganizationRepository.list_all` so the super-admin
    org grid can show "Nordlicht Wind Recht · 7 members" without a second
    round-trip per row.
    """

    id: UUID
    name: str
    status: str
    member_count: int
    created_at: datetime


_ORG_COLS: Final[str] = "id, name, status, created_at, updated_at"


class OrganizationRepository:
    """CRUD + listing for ``organizations`` and its membership relation.

    Membership is just ``users.org_id``; this repository owns the queries
    that group / count by it so route handlers stay free of raw SQL.
    """

    __slots__ = ()

    async def create(
        self,
        conn: asyncpg.Connection,
        *,
        name: str,
    ) -> OrganizationRecord:
        """Insert a new organisation. Caller (super-admin only) is
        responsible for de-duplicating by display name if desired —
        organisations are keyed by UUID, not name, so duplicates are
        allowed at the schema level."""
        row = await conn.fetchrow(
            f"INSERT INTO organizations (name) VALUES ($1) RETURNING {_ORG_COLS}",
            name,
        )
        assert row is not None
        return _row_to_org(row)

    async def get_by_id(
        self,
        conn: asyncpg.Connection,
        org_id: UUID,
    ) -> OrganizationRecord | None:
        row = await conn.fetchrow(
            f"SELECT {_ORG_COLS} FROM organizations WHERE id = $1",
            org_id,
        )
        return _row_to_org(row) if row is not None else None

    async def list_all(
        self,
        conn: asyncpg.Connection,
    ) -> list[OrganizationSummary]:
        """Every organisation with its current member count. Super-admin
        only — a firm admin gets just their own org via :meth:`get_by_id`."""
        rows = await conn.fetch(
            """
            SELECT o.id, o.name, o.status, o.created_at,
                   COUNT(u.id)::int AS member_count
            FROM organizations o
            LEFT JOIN users u ON u.org_id = o.id
            GROUP BY o.id, o.name, o.status, o.created_at
            ORDER BY o.created_at DESC
            """
        )
        return [
            OrganizationSummary(
                id=r["id"],
                name=r["name"],
                status=r["status"],
                member_count=int(r["member_count"] or 0),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def list_members(
        self,
        conn: asyncpg.Connection,
        org_id: UUID,
    ) -> list[UserRecord]:
        """All active members of an organisation, ordered by display name."""
        rows = await conn.fetch(
            f"SELECT {_USER_COLS} FROM users WHERE org_id = $1 AND status = 'active' ORDER BY full_name",
            org_id,
        )
        return [_row_to_user(r) for r in rows]

    async def rename(
        self,
        conn: asyncpg.Connection,
        org_id: UUID,
        name: str,
    ) -> bool:
        """Update an organisation's display name. Super-admin only.

        Returns ``True`` when a row was updated (an unknown ``org_id``
        returns ``False`` and the route maps to 404 — never 403 — to
        avoid leaking org existence)."""
        res = await conn.execute(
            "UPDATE organizations SET name = $1, updated_at = NOW() WHERE id = $2",
            name,
            org_id,
        )
        return res.endswith(" 1")


def _row_to_org(row: asyncpg.Record) -> OrganizationRecord:
    return OrganizationRecord(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── Org invitations (Phase C.1) ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InvitationRecord:
    """A row from ``org_invitations``.

    Each row is a single-use, time-limited claim mapping ``email_canonical``
    to ``org_id`` with an intended ``role``. ``accepted_at`` flipping
    non-null is the consumed marker — the unique partial index on
    ``(org_id, email_canonical) WHERE accepted_at IS NULL`` ensures only
    one outstanding invitation per (org, email).
    """

    id: UUID
    org_id: UUID
    email_canonical: str
    role: str
    invited_by: UUID | None
    token_hash: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None


_INVITATION_COLS: Final[str] = (
    "id, org_id, email_canonical, role, invited_by, token_hash, expires_at, accepted_at, created_at"
)


class InvitationRepository:
    """CRUD for ``org_invitations``.

    Mirrors :class:`ResetTokenRepository`'s consume-once posture: the
    accept query uses an atomic ``UPDATE … WHERE accepted_at IS NULL AND
    expires_at > NOW() RETURNING …`` so two concurrent ``/auth/accept-
    invite`` calls with the same token observe exactly-one success.
    """

    __slots__ = ()

    async def upsert_outstanding(
        self,
        conn: asyncpg.Connection,
        *,
        org_id: UUID,
        email_canonical: str,
        role: str,
        invited_by: UUID | None,
        token_hash: str,
        expires_at: datetime,
    ) -> InvitationRecord:
        """Create or refresh the outstanding invitation for ``(org, email)``.

        Re-inviting an already-invited email rotates the token and extends
        ``expires_at`` in place rather than producing duplicate rows — the
        unique partial index on ``(org_id, email_canonical) WHERE
        accepted_at IS NULL`` is what makes ``ON CONFLICT`` resolve.
        Accepted invitations are excluded from the unique index, so a
        re-invite after acceptance creates a NEW pending row (rare, but
        valid — e.g. user was removed and re-invited).
        """
        row = await conn.fetchrow(
            f"""
            INSERT INTO org_invitations
                (org_id, email_canonical, role, invited_by, token_hash, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (org_id, email_canonical)
                WHERE accepted_at IS NULL
            DO UPDATE SET
                role        = EXCLUDED.role,
                invited_by  = EXCLUDED.invited_by,
                token_hash  = EXCLUDED.token_hash,
                expires_at  = EXCLUDED.expires_at,
                created_at  = NOW()
            RETURNING {_INVITATION_COLS}
            """,
            org_id,
            email_canonical,
            role,
            invited_by,
            token_hash,
            expires_at,
        )
        assert row is not None
        return _row_to_invitation(row)

    async def get_active_by_hash(
        self,
        conn: asyncpg.Connection,
        token_hash: str,
    ) -> InvitationRecord | None:
        """Look up a pending, unexpired invitation by sha256 token hash."""
        row = await conn.fetchrow(
            f"""
            SELECT {_INVITATION_COLS}
            FROM org_invitations
            WHERE token_hash = $1
              AND accepted_at IS NULL
              AND expires_at > NOW()
            """,
            token_hash,
        )
        return _row_to_invitation(row) if row is not None else None

    async def list_pending_for_org(
        self,
        conn: asyncpg.Connection,
        org_id: UUID,
    ) -> list[InvitationRecord]:
        """All outstanding (unaccepted, unexpired) invitations for an org,
        newest-first. Drives the "Pending invitations" panel in the admin
        UI; expired rows are filtered out so the panel stays useful."""
        rows = await conn.fetch(
            f"""
            SELECT {_INVITATION_COLS}
            FROM org_invitations
            WHERE org_id = $1
              AND accepted_at IS NULL
              AND expires_at > NOW()
            ORDER BY created_at DESC
            """,
            org_id,
        )
        return [_row_to_invitation(r) for r in rows]

    async def accept(
        self,
        conn: asyncpg.Connection,
        token_hash: str,
    ) -> InvitationRecord | None:
        """Atomically mark an invitation accepted.

        Returns the row if it was pending+unexpired at the moment of the
        UPDATE; ``None`` otherwise. Two concurrent accepts with the same
        token therefore observe exactly-one success — same posture as
        :meth:`ResetTokenRepository.consume`.
        """
        row = await conn.fetchrow(
            f"""
            UPDATE org_invitations
            SET accepted_at = NOW()
            WHERE token_hash = $1
              AND accepted_at IS NULL
              AND expires_at > NOW()
            RETURNING {_INVITATION_COLS}
            """,
            token_hash,
        )
        return _row_to_invitation(row) if row is not None else None

    async def revoke(
        self,
        conn: asyncpg.Connection,
        invitation_id: UUID,
        org_id: UUID,
    ) -> bool:
        """Delete a pending invitation. Scoped on ``org_id`` so a firm
        admin cannot revoke another firm's invitation via id-guessing —
        the WHERE clause makes a cross-firm revoke a no-op (route maps
        the False return to a 404, never 403)."""
        res = await conn.execute(
            "DELETE FROM org_invitations WHERE id = $1 AND org_id = $2 AND accepted_at IS NULL",
            invitation_id,
            org_id,
        )
        return res.endswith(" 1")


def _row_to_invitation(row: asyncpg.Record) -> InvitationRecord:
    return InvitationRecord(
        id=row["id"],
        org_id=row["org_id"],
        email_canonical=row["email_canonical"],
        role=row["role"],
        invited_by=row["invited_by"],
        token_hash=row["token_hash"],
        expires_at=row["expires_at"],
        accepted_at=row["accepted_at"],
        created_at=row["created_at"],
    )
