"""Shared auth data shapes.

Tiny module: defines the :class:`CurrentUser` value object that the
FastAPI :func:`get_current_user` dependency hands to every protected
route. Kept separate from :mod:`tokens` because route handlers should
import the *result* of authentication, not the JWT machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

__all__ = ["CurrentUser"]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated principal for a single request.

    Constructed once per request by :func:`get_current_user` from a
    validated :class:`AccessTokenClaims`. Frozen so route handlers
    cannot accidentally mutate identity mid-request.

    Attributes:
        id: ``users.id``. The attribution / ``created_by`` identity.
            Never trust an id from the request body — use this.
        email: Canonical email (lower-cased, trimmed at signup).
        role: ``'user'`` or ``'admin'``. Admin role grants read across
            tenants; mutation across tenants is still forbidden.
        org_id: ``users.org_id`` — the firm this principal belongs to,
            and the **tenant-isolation key** (Phase B filters rows by
            ``org_id``). ``None`` for an org-less user (signed up but not
            yet placed in a firm): such a user sees an empty workspace and
            cannot create resources.
    """

    id: UUID
    email: str
    role: str
    org_id: UUID | None = None

    @property
    def is_admin(self) -> bool:
        """Whether this principal has *at least* firm-admin authority.

        Returns ``True`` for both ``'admin'`` (firm admin) and
        ``'super_admin'`` (platform admin) — a super-admin can do anything
        a firm admin can, so every existing ``require_admin``/``is_admin``
        check transparently lets a super-admin through.
        """
        return self.role in {"admin", "super_admin"}

    @property
    def is_super_admin(self) -> bool:
        """Whether this principal is a platform-level super admin.

        Super admins create/delete organisations, place users across any
        org, and promote firm admins. A firm admin (``role='admin'``) is
        NOT a super admin — scope ends at their own ``org_id``.
        """
        return self.role == "super_admin"
