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
        id: ``users.id``. Use this — and *only* this — when filtering
            tenant rows by user. Never trust an id from the request body.
        email: Canonical email (lower-cased, trimmed at signup).
        role: ``'user'`` or ``'admin'``. Admin role grants read across
            tenants; mutation across tenants is still forbidden.
    """

    id: UUID
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        """Whether this principal has the ``admin`` role."""
        return self.role == "admin"
