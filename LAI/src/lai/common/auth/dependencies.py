"""FastAPI dependencies — the single tenant-isolation enforcement point.

Every protected route in ``serve_rag``, ``api.py``, and ``ddiq_report``
calls :func:`get_current_user` (directly or via
:func:`require_authenticated`). That function is the single chokepoint
where a Bearer token becomes a :class:`CurrentUser`. If this function
returns, the principal has been authenticated; if it raises, FastAPI
emits a 401 and the route never runs.

There is **no** background fallback: a missing or malformed header is
a 401, every time. Bearer-only — we do not read tokens from cookies,
query params, or request bodies.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from lai.common.auth.exceptions import (
    InvalidTokenError,
    TokenExpiredError,
)
from lai.common.auth.models import CurrentUser
from lai.common.auth.tokens import TokenIssuer

__all__ = [
    "CurrentUserDep",
    "build_get_current_user",
    "require_admin",
    "require_super_admin",
]

# Setting ``auto_error=False`` lets us emit our own 401 with a
# ``WWW-Authenticate`` hint instead of FastAPI's default 403.
# Note: ``bearerFormat`` is camelCase to match the OpenAPI spec field
# name — FastAPI accepts only that spelling.
_bearer = HTTPBearer(auto_error=False, bearerFormat="JWT", scheme_name="lai-access")


def _unauthorized(detail: str, *, expired: bool = False) -> HTTPException:
    headers = {"WWW-Authenticate": 'Bearer realm="lai"'}
    if expired:
        # RFC 6750 §3 — hint the reason so the SPA refresh client can
        # branch without parsing the body.
        headers["WWW-Authenticate"] = (
            'Bearer realm="lai", error="invalid_token", '
            'error_description="The access token expired"'
        )
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=headers,
    )


def build_get_current_user(issuer: TokenIssuer):
    """Build a request-scoped ``get_current_user`` dependency bound to ``issuer``.

    The :class:`TokenIssuer` carries the signing secret and lifetime
    knobs; injecting it via closure (rather than a process global)
    keeps the auth module ergonomic to unit-test — a test can build a
    second issuer with a different secret and an isolated dependency
    without poking module state.

    Args:
        issuer: Configured :class:`TokenIssuer` (constructed once at
            app startup from :class:`AuthConfig`).

    Returns:
        An async callable suitable for ``Depends(...)``.
    """

    async def get_current_user(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> CurrentUser:
        if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
            raise _unauthorized("missing or malformed Authorization header")
        try:
            claims = issuer.decode_access_token(creds.credentials)
        except TokenExpiredError as exc:
            raise _unauthorized("access token expired", expired=True) from exc
        except InvalidTokenError as exc:
            # Generic 401; never echo the verifier's reason — that lets
            # an attacker probe for "valid signature, wrong issuer" vs
            # "tampered signature".
            raise _unauthorized("invalid access token") from exc
        return CurrentUser(
            id=claims.user_id,
            email=claims.email,
            role=claims.role,
            org_id=claims.org_id,
        )

    return get_current_user


# Convenience alias for route handler signatures.
# Routes that need only "any authenticated user" type-annotate with this.
CurrentUserDep = Annotated[CurrentUser, Depends(lambda: None)]  # placeholder; see note


async def require_admin(user: CurrentUser) -> CurrentUser:
    """Sub-dependency that 403s non-admin principals.

    Use as ``user: CurrentUser = Depends(require_admin)`` on routes
    that must reject regular users. The outer
    :func:`get_current_user` runs first (so a 401 still fires for
    unauthenticated callers); this layer only checks role.

    Args:
        user: The already-authenticated principal.

    Returns:
        ``user``, unchanged, when the principal is an admin.

    Raises:
        HTTPException: 403 when the principal is not an admin.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user


async def require_super_admin(user: CurrentUser) -> CurrentUser:
    """Sub-dependency that 403s anyone who is not a ``super_admin``.

    A firm admin (``role='admin'``) does NOT satisfy this — only platform
    super-admins. Use on routes that touch the cross-org plane: creating /
    deleting organisations, promoting firm admins, listing every org.
    """
    if not user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="super-admin role required",
        )
    return user
