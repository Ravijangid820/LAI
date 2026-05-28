"""FastAPI router for Path A Step 2 — per-session view-only sharing.

Endpoints (chat sessions; DDiQ doc/report sharing lives in
``ddiq_report.py``):

    GET    /sessions/{sid}/shares            — list collaborators
    POST   /sessions/{sid}/shares {user_id}  — grant view access
    DELETE /sessions/{sid}/shares/{user_id}  — revoke

    GET    /share-targets/search?q=&exclude_session_id=
                                             — typeahead for the share dialog

Authorisation model (v1):

* Share management (list/add/revoke) — **owner** of the session OR
  ``super_admin``. A shared collaborator can read the session but cannot
  re-share it.
* Share targeting — any authenticated user may search **their own org's**
  members (no cross-firm enumeration). Org-less callers get an empty
  list.
* Cross-firm grants — rejected at POST time even for super-admin via the
  same-org guard, so a shared session is always within one firm.

Notes:

* SQLite holds ``session_shares`` (chat lives in SQLite); user details
  (name/email/org_id) come from Postgres via the auth pool. Each share
  list endpoint enriches via a single batched SELECT — one round-trip
  per request, not per row.
* 404-on-miss posture preserved across the boundary — a non-owner
  caller managing shares on someone else's session sees 404, not 403.
"""

from __future__ import annotations

import logging
from uuid import UUID

import asyncpg  # noqa: F401 — only for typing exposure
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from lai import persistence
from lai.api.auth_router import AuthDeps
from lai.common.auth import CurrentUser

__all__ = ["build_share_router"]

_logger = logging.getLogger("lai.share")


# ─── Wire shapes ────────────────────────────────────────────────────────────


class ShareUserOut(BaseModel):
    """Enriched share row — what the SPA needs to render the share list."""

    user_id: UUID
    email: str
    full_name: str
    granted_at: float


class AddShareBody(BaseModel):
    user_id: UUID


class ShareTargetOut(BaseModel):
    """Result row for the share-target typeahead."""

    id: UUID
    email: str
    full_name: str


# ─── Authorization helper ───────────────────────────────────────────────────


def _can_manage_shares(session_id: str, user: CurrentUser) -> bool:
    """Owner of the session OR super-admin. Used by the share-management
    endpoints; never by the read-widening (that's a different question)."""
    if user.is_super_admin:
        return True
    owner = persistence.session_owner(session_id)
    return owner is not None and owner == str(user.id)


# ─── Router factory ─────────────────────────────────────────────────────────


def build_share_router(
    deps: AuthDeps,
    *,
    get_current_user,
) -> APIRouter:
    router = APIRouter(tags=["share"])

    # ── GET /sessions/{sid}/shares ────────────────────────────────────
    @router.get("/sessions/{session_id}/shares", response_model=list[ShareUserOut])
    async def list_shares(
        session_id: str,
        user: CurrentUser = Depends(get_current_user),
    ) -> list[ShareUserOut]:
        if not _can_manage_shares(session_id, user):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session_id not found",
            )
        rows = persistence.list_session_shares(session_id)
        if not rows:
            return []
        # Enrich with Postgres user details in ONE round-trip.
        ids = [UUID(r["user_id"]) for r in rows]
        async with deps.pool.acquire() as conn:
            user_rows = await conn.fetch(
                "SELECT id, email, full_name FROM users WHERE id = ANY($1::uuid[])",
                ids,
            )
        by_id = {str(r["id"]): r for r in user_rows}
        out: list[ShareUserOut] = []
        for r in rows:
            u = by_id.get(r["user_id"])
            if u is None:
                # Defensive: user was deleted between the share-add and now.
                # Silently skip rather than 500 the whole list.
                continue
            out.append(
                ShareUserOut(
                    user_id=u["id"],
                    email=u["email"],
                    full_name=u["full_name"],
                    granted_at=r["created_at"],
                )
            )
        return out

    # ── POST /sessions/{sid}/shares ───────────────────────────────────
    @router.post(
        "/sessions/{session_id}/shares",
        response_model=ShareUserOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_share(
        session_id: str,
        body: AddShareBody,
        user: CurrentUser = Depends(get_current_user),
    ) -> ShareUserOut:
        if not _can_manage_shares(session_id, user):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session_id not found",
            )
        if user.org_id is None:
            # An org-less owner has no firm context to share within.
            # (Super-admin without an org is a corner case — same handling.)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="join a firm before sharing",
            )
        if body.user_id == user.id:
            # Idempotent — the owner already has access by definition.
            # Return a synthesised "share" so the SPA can no-op gracefully.
            async with deps.pool.acquire() as conn:
                me = await conn.fetchrow(
                    "SELECT id, email, full_name FROM users WHERE id = $1",
                    user.id,
                )
            assert me is not None
            return ShareUserOut(
                user_id=me["id"],
                email=me["email"],
                full_name=me["full_name"],
                granted_at=0.0,
            )

        # Validate the target user exists AND is in the caller's org. The
        # same-org constraint is what stops cross-firm sharing — even a
        # super-admin can't grant cross-firm here (sharing across firms
        # would conflate two tenants' data planes).
        async with deps.pool.acquire() as conn:
            target = await conn.fetchrow(
                "SELECT id, email, full_name, org_id FROM users WHERE id = $1",
                body.user_id,
            )
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="user not found",
            )
        if target["org_id"] != user.org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="user is not in your organisation",
            )

        # Persistence's add_session_share validates ``granted_by == owner``
        # for strict-owner attribution. Super-admin acts on behalf of the
        # actual owner so the row's ``granted_by`` audit trail stays
        # honest (it's "owner granted X", not "super-admin granted X").
        if user.is_super_admin:
            owner = persistence.session_owner(session_id)
            grantor = owner if owner is not None else str(user.id)
        else:
            grantor = str(user.id)
        share_id = persistence.add_session_share(
            session_id,
            str(body.user_id),
            granted_by=grantor,
        )
        if share_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session_id not found",
            )
        _logger.info(
            "session.share.add session=%s target=%s by=%s",
            session_id,
            body.user_id,
            user.id,
        )
        # Re-read the row so the response carries the real created_at.
        # add_session_share returned id=0 for self-share (handled above).
        for r in persistence.list_session_shares(session_id):
            if r["user_id"] == str(body.user_id):
                return ShareUserOut(
                    user_id=target["id"],
                    email=target["email"],
                    full_name=target["full_name"],
                    granted_at=r["created_at"],
                )
        # Unreachable if add_session_share returned non-None.
        raise HTTPException(500, "share persisted but not readable")

    # ── DELETE /sessions/{sid}/shares/{user_id} ───────────────────────
    @router.delete(
        "/sessions/{session_id}/shares/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def revoke_share(
        session_id: str,
        user_id: UUID,
        user: CurrentUser = Depends(get_current_user),
    ) -> None:
        if not _can_manage_shares(session_id, user):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session_id not found",
            )
        # Super-admin: the persistence layer's granted_by check is
        # strict-owner; for super-admin we synthesise the owner as the
        # actual session owner so the delete proceeds.
        if user.is_super_admin:
            owner = persistence.session_owner(session_id)
            grantor = owner if owner is not None else str(user.id)
        else:
            grantor = str(user.id)
        ok = persistence.revoke_session_share(
            session_id,
            str(user_id),
            granted_by=grantor,
        )
        if not ok:
            # Either the share didn't exist, or the auth gate above missed
            # a corner case. Either way: 404 (no existence leak).
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="share not found",
            )
        _logger.info(
            "session.share.revoke session=%s target=%s by=%s",
            session_id,
            user_id,
            user.id,
        )

    # ── GET /share-targets/search?q=&exclude_session_id= ─────────────
    @router.get("/share-targets/search", response_model=list[ShareTargetOut])
    async def search_share_targets(
        q: str,
        limit: int = 10,
        exclude_session_id: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ) -> list[ShareTargetOut]:
        """Same-org member typeahead for the FE share dialog.

        Any authenticated user may call this (sharing isn't an admin-only
        action). Scope: ``users.org_id = caller.org_id`` AND ``users.id
        != caller.id`` AND optionally NOT already in
        ``session_shares(exclude_session_id)``. Trigram-indexed; min
        2-char query like the admin search.
        """
        if user.org_id is None:
            return []  # org-less callers have no firm to search
        q_norm = q.strip().lower()
        if len(q_norm) < 2:
            return []
        limit = max(1, min(50, limit))

        # Find already-shared users to exclude. Done outside the SQL to
        # keep the cross-store coupling explicit — session_shares lives
        # in SQLite, users in Postgres.
        already = set()
        if exclude_session_id:
            already = persistence.session_share_user_ids(exclude_session_id)
            # Also exclude the session owner so the dialog never offers
            # "share with the owner" as a target.
            owner = persistence.session_owner(exclude_session_id)
            if owner:
                already.add(owner)
        already.add(str(user.id))  # never offer the caller themselves

        async with deps.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, email, full_name
                FROM users
                WHERE status = 'active'
                  AND org_id = $1
                  AND id <> ALL($2::uuid[])
                  AND (lower(full_name) ILIKE '%' || $3 || '%'
                       OR email_canonical ILIKE '%' || $3 || '%')
                ORDER BY GREATEST(
                           similarity(lower(full_name), $3),
                           similarity(email_canonical, $3)
                         ) DESC,
                         full_name
                LIMIT $4
                """,
                user.org_id,
                [UUID(uid) for uid in already],
                q_norm,
                limit,
            )
        return [ShareTargetOut(id=r["id"], email=r["email"], full_name=r["full_name"]) for r in rows]

    return router
