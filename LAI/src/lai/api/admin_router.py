"""FastAPI router for Phase C admin endpoints (MULTIUSER_PLAN §7, §10.4).

Two role tiers manage tenancy:

* ``super_admin`` (platform): creates/deletes orgs, places users across any
  org, promotes firm admins. Visible across orgs.
* ``admin`` (firm): manages members of *their* org only.

Every endpoint checks role + org scope. A firm admin who passes another
firm's ``org_id`` in the path gets 404 (never 403) so org existence is
never leaked across firms — the same posture as the data routes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from lai.api.auth_router import AuthDeps
from lai.api.email import send_invite_email
from lai.common import audit
from lai.common.auth import CurrentUser
from lai.common.auth.repository import (
    InvitationRecord,
    OrganizationSummary,
    UserRecord,
    canonical_email,
)

_INVITE_TTL_DAYS: int = 7

__all__ = ["build_admin_router"]

_logger = logging.getLogger("lai.admin")


# ─── Request / response shapes ──────────────────────────────────────────────


class OrgSummaryOut(BaseModel):
    id: UUID
    name: str
    status: str
    member_count: int


class MemberOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    company: str | None
    role: str
    org_id: UUID | None


class CreateOrgBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RenameOrgBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class AddMemberBody(BaseModel):
    user_id: UUID
    role: str = Field(default="user", pattern="^(user|admin)$")


class SetMemberRoleBody(BaseModel):
    # Firm admins may toggle within {'user','admin'}; only a super-admin may
    # set 'super_admin'. The handler enforces that — the schema accepts all
    # three so the SPA can attempt and receive a clean 403 if forbidden.
    role: str = Field(pattern="^(user|admin|super_admin)$")


class InviteBody(BaseModel):
    email: EmailStr
    role: str = Field(default="user", pattern="^(user|admin)$")


class InvitationOut(BaseModel):
    id: UUID
    org_id: UUID
    email: str
    role: str
    invited_by: UUID | None
    expires_at: datetime
    created_at: datetime


class AuditEntryOut(BaseModel):
    id: int
    ts: datetime
    user_id: UUID | None
    org_id: UUID | None
    action: str
    outcome: str
    session_id: str | None
    latency_ms: int | None
    detail: dict[str, Any] | None


# ─── Helpers ────────────────────────────────────────────────────────────────


def _scope_for(user: CurrentUser) -> str:
    """Search scope per role: super → every active user; firm admin → only
    org-less users plus the admin's own org members (no rival-firm
    enumeration). See ``UserRepository.search``."""
    return "all" if user.is_super_admin else "addable"


def _can_manage_org(user: CurrentUser, org_id: UUID) -> bool:
    """A super-admin may manage any org; a firm admin only their own."""
    return user.is_super_admin or (user.is_admin and user.org_id is not None and user.org_id == org_id)


def _to_member_out(rec: UserRecord) -> MemberOut:
    return MemberOut(
        id=rec.id,
        email=rec.email,
        full_name=rec.full_name,
        company=rec.company,
        role=rec.role,
        org_id=rec.org_id,
    )


def _to_org_out(s: OrganizationSummary) -> OrgSummaryOut:
    return OrgSummaryOut(
        id=s.id,
        name=s.name,
        status=s.status,
        member_count=s.member_count,
    )


def _to_invitation_out(rec: InvitationRecord) -> InvitationOut:
    return InvitationOut(
        id=rec.id,
        org_id=rec.org_id,
        email=rec.email_canonical,
        role=rec.role,
        invited_by=rec.invited_by,
        expires_at=rec.expires_at,
        created_at=rec.created_at,
    )


# ─── Router factory ─────────────────────────────────────────────────────────


def build_admin_router(
    deps: AuthDeps,
    *,
    get_current_user,
) -> APIRouter:
    """Wire the ``/admin`` endpoints. Mirrors the auth_router factory."""
    router = APIRouter(prefix="/admin", tags=["admin"])

    # ── GET /admin/orgs ────────────────────────────────────────────────
    @router.get("/orgs", response_model=list[OrgSummaryOut])
    async def list_orgs(
        user: CurrentUser = Depends(get_current_user),
    ) -> list[OrgSummaryOut]:
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="admin role required",
            )
        async with deps.pool.acquire() as conn:
            if user.is_super_admin:
                summaries = await deps.org_repo.list_all(conn)
            else:
                if user.org_id is None:
                    return []
                org = await deps.org_repo.get_by_id(conn, user.org_id)
                if org is None:
                    return []
                members = await deps.org_repo.list_members(conn, user.org_id)
                summaries = [
                    OrganizationSummary(
                        id=org.id,
                        name=org.name,
                        status=org.status,
                        member_count=len(members),
                        created_at=org.created_at,
                    )
                ]
        return [_to_org_out(s) for s in summaries]

    # ── POST /admin/orgs ───────────────────────────────────────────────
    @router.post(
        "/orgs",
        response_model=OrgSummaryOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_org(
        body: CreateOrgBody,
        user: CurrentUser = Depends(get_current_user),
    ) -> OrgSummaryOut:
        if not user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="super-admin role required",
            )
        async with deps.pool.acquire() as conn:
            rec = await deps.org_repo.create(conn, name=body.name.strip())
        _logger.info(
            "org.create id=%s name=%r by=%s",
            rec.id,
            rec.name,
            user.id,
        )
        return OrgSummaryOut(
            id=rec.id,
            name=rec.name,
            status=rec.status,
            member_count=0,
        )

    # ── PATCH /admin/orgs/{org_id} (rename) ────────────────────────────
    @router.patch("/orgs/{org_id}", response_model=OrgSummaryOut)
    async def rename_org(
        org_id: UUID,
        body: RenameOrgBody,
        user: CurrentUser = Depends(get_current_user),
    ) -> OrgSummaryOut:
        if not user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="super-admin role required",
            )
        async with deps.pool.acquire() as conn:
            ok = await deps.org_repo.rename(conn, org_id, body.name.strip())
            if not ok:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="organisation not found",
                )
            rec = await deps.org_repo.get_by_id(conn, org_id)
            members = await deps.org_repo.list_members(conn, org_id)
        assert rec is not None
        return OrgSummaryOut(
            id=rec.id,
            name=rec.name,
            status=rec.status,
            member_count=len(members),
        )

    # ── GET /admin/orgs/{org_id}/members ───────────────────────────────
    @router.get("/orgs/{org_id}/members", response_model=list[MemberOut])
    async def list_members(
        org_id: UUID,
        user: CurrentUser = Depends(get_current_user),
    ) -> list[MemberOut]:
        if not _can_manage_org(user, org_id):
            # 404 not 403: cross-firm callers must not learn the org exists.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        async with deps.pool.acquire() as conn:
            members = await deps.org_repo.list_members(conn, org_id)
        return [_to_member_out(m) for m in members]

    # ── POST /admin/orgs/{org_id}/members ──────────────────────────────
    @router.post(
        "/orgs/{org_id}/members",
        response_model=MemberOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_member(
        org_id: UUID,
        body: AddMemberBody,
        user: CurrentUser = Depends(get_current_user),
    ) -> MemberOut:
        if not _can_manage_org(user, org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        async with deps.pool.acquire() as conn:
            org = await deps.org_repo.get_by_id(conn, org_id)
            if org is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="organisation not found",
                )
            target = await deps.user_repo.get_by_id(conn, body.user_id)
            if target is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="user not found",
                )
            # A firm admin may only ADD an unaffiliated user OR re-confirm an
            # existing member of their own org. They cannot poach another
            # firm's user. Super-admin: no such constraint.
            if not user.is_super_admin and target.org_id is not None and target.org_id != org_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="user already belongs to another organisation",
                )
            async with conn.transaction():
                await deps.user_repo.set_org_id(conn, body.user_id, org_id)
                if body.role != target.role:
                    await deps.user_repo.set_role(conn, body.user_id, body.role)
            updated = await deps.user_repo.get_by_id(conn, body.user_id)
        assert updated is not None
        _logger.info(
            "org.member.add org=%s user=%s by=%s role=%s",
            org_id,
            body.user_id,
            user.id,
            body.role,
        )
        return _to_member_out(updated)

    # ── DELETE /admin/orgs/{org_id}/members/{user_id} ──────────────────
    @router.delete(
        "/orgs/{org_id}/members/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def remove_member(
        org_id: UUID,
        user_id: UUID,
        user: CurrentUser = Depends(get_current_user),
    ) -> None:
        if not _can_manage_org(user, org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        # A firm admin cannot evict themselves — would leave no firm admin and
        # no path back. Super-admin may (their scope is platform-wide).
        if user_id == user.id and not user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot remove yourself from an organisation",
            )
        async with deps.pool.acquire() as conn:
            target = await deps.user_repo.get_by_id(conn, user_id)
            if target is None or target.org_id != org_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="member not found",
                )
            await deps.user_repo.set_org_id(conn, user_id, None)
        _logger.info(
            "org.member.remove org=%s user=%s by=%s",
            org_id,
            user_id,
            user.id,
        )

    # ── PATCH /admin/orgs/{org_id}/members/{user_id} (change role) ─────
    @router.patch(
        "/orgs/{org_id}/members/{user_id}",
        response_model=MemberOut,
    )
    async def set_member_role(
        org_id: UUID,
        user_id: UUID,
        body: SetMemberRoleBody,
        user: CurrentUser = Depends(get_current_user),
    ) -> MemberOut:
        if not _can_manage_org(user, org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        # Only a super-admin may grant the platform-level super_admin role.
        # A firm admin is limited to {'user','admin'} within their own org.
        if body.role == "super_admin" and not user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only a super-admin may grant super_admin",
            )
        async with deps.pool.acquire() as conn:
            target = await deps.user_repo.get_by_id(conn, user_id)
            if target is None or target.org_id != org_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="member not found",
                )
            await deps.user_repo.set_role(conn, user_id, body.role)
            updated = await deps.user_repo.get_by_id(conn, user_id)
        assert updated is not None
        _logger.info(
            "org.member.role org=%s user=%s by=%s role=%s",
            org_id,
            user_id,
            user.id,
            body.role,
        )
        return _to_member_out(updated)

    # ── GET /admin/users/search?q=… ────────────────────────────────────
    @router.get("/users/search", response_model=list[MemberOut])
    async def search_users(
        q: str,
        limit: int = 20,
        user: CurrentUser = Depends(get_current_user),
    ) -> list[MemberOut]:
        """Trigram-indexed typeahead for the admin member picker. Scoped per
        role: super-admin sees every active user; a firm admin sees only the
        users they may legitimately add (org-less + own-org members)."""
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="admin role required",
            )
        limit = max(1, min(50, limit))
        async with deps.pool.acquire() as conn:
            users = await deps.user_repo.search(
                conn,
                q=q,
                scope=_scope_for(user),
                viewer_org_id=user.org_id,
                limit=limit,
            )
        return [_to_member_out(u) for u in users]

    # ── POST /admin/orgs/{org_id}/invites ──────────────────────────────
    # Phase C.1 — invite an UNREGISTERED email. Creates a single-use,
    # time-limited token row in ``org_invitations``; emails the recipient a
    # link to ``/accept-invite?token=…`` where they pick their own name +
    # password. We never auto-generate or email passwords. If the email
    # already maps to an existing account, the admin should add them via
    # the search-add flow above; we return 409 to point them there.
    @router.post(
        "/orgs/{org_id}/invites",
        response_model=InvitationOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_invite(
        org_id: UUID,
        body: InviteBody,
        background_tasks: BackgroundTasks,
        user: CurrentUser = Depends(get_current_user),
    ) -> InvitationOut:
        if not _can_manage_org(user, org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        email_canon = canonical_email(body.email)
        token = deps.issuer.issue_invite_token(ttl_days=_INVITE_TTL_DAYS)
        async with deps.pool.acquire() as conn:
            org = await deps.org_repo.get_by_id(conn, org_id)
            if org is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="organisation not found",
                )
            existing_user = await deps.user_repo.get_by_email(conn, email_canon)
            if existing_user is not None:
                # An account already exists — point the admin at the search-add
                # flow so we don't create a duplicate identity via accept.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=("an account with this email already exists — use the member search to add them"),
                )
            invitation = await deps.invitation_repo.upsert_outstanding(
                conn,
                org_id=org_id,
                email_canonical=email_canon,
                role=body.role,
                invited_by=user.id,
                token_hash=token.token_hash,
                expires_at=token.expires_at,
            )
            inviter = await deps.user_repo.get_by_id(conn, user.id)

        # Schedule the email AFTER the response so a Brevo hiccup never blocks
        # the admin UI. ``send_invite_email`` already swallows + logs errors.
        if deps.email_config is not None:
            inviter_name = inviter.full_name if inviter is not None else user.email
            background_tasks.add_task(
                send_invite_email,
                deps.email_config,
                recipient_email=body.email,
                raw_invite_token=token.raw,
                org_name=org.name,
                inviter_name=inviter_name,
                ttl_days=_INVITE_TTL_DAYS,
            )
        _logger.info(
            "org.invite.create org=%s email=%s by=%s role=%s",
            org_id,
            email_canon,
            user.id,
            body.role,
        )
        return _to_invitation_out(invitation)

    # ── GET /admin/orgs/{org_id}/invites ───────────────────────────────
    @router.get(
        "/orgs/{org_id}/invites",
        response_model=list[InvitationOut],
    )
    async def list_invites(
        org_id: UUID,
        user: CurrentUser = Depends(get_current_user),
    ) -> list[InvitationOut]:
        if not _can_manage_org(user, org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        async with deps.pool.acquire() as conn:
            invitations = await deps.invitation_repo.list_pending_for_org(
                conn,
                org_id,
            )
        return [_to_invitation_out(i) for i in invitations]

    # ── DELETE /admin/orgs/{org_id}/invites/{invite_id} ────────────────
    @router.delete(
        "/orgs/{org_id}/invites/{invite_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def revoke_invite(
        org_id: UUID,
        invite_id: UUID,
        user: CurrentUser = Depends(get_current_user),
    ) -> None:
        if not _can_manage_org(user, org_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="organisation not found",
            )
        async with deps.pool.acquire() as conn:
            ok = await deps.invitation_repo.revoke(conn, invite_id, org_id)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="invitation not found",
            )
        _logger.info(
            "org.invite.revoke org=%s invite=%s by=%s",
            org_id,
            invite_id,
            user.id,
        )

    # ── GET /admin/audit ───────────────────────────────────────────────
    # Read the append-only audit trail (migration 006). Super-admin sees every
    # org (optional ``org`` filter); a firm admin is scoped to their own org
    # (an org-less admin sees nothing). Newest first; offset paging.
    @router.get("/audit", response_model=list[AuditEntryOut])
    async def list_audit(
        action: str | None = None,
        user_id: UUID | None = None,
        org: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
        user: CurrentUser = Depends(get_current_user),
    ) -> list[AuditEntryOut]:
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="admin role required",
            )
        limit = max(1, min(200, limit))
        offset = max(0, offset)
        if user.is_super_admin:
            scope_org = org  # optional filter; None = every org
        else:
            if user.org_id is None:
                return []
            scope_org = user.org_id  # firm admin: forced to own org
        async with deps.pool.acquire() as conn:
            rows = await audit.query(
                conn,
                org_id=scope_org,
                action=action,
                user_id=user_id,
                limit=limit,
                offset=offset,
            )
        return [AuditEntryOut(**row) for row in rows]

    return router
