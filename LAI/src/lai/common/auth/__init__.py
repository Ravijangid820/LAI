"""Shared authentication primitives.

The :mod:`lai.common.auth` package is the single home for password
hashing, JWT access-token issuance/verification, refresh-token mint
+ hash, and the FastAPI dependency that turns a Bearer token into a
:class:`CurrentUser`. ``serve_rag``, the DDiQ microservice, and the
``api.py`` chat microservice all import from here — same hashing,
same verifier, no drift.

Public surface
--------------

Configuration:
    :class:`AuthConfig` — pydantic-settings, env-prefix ``LAI_AUTH_``.

Exceptions:
    :class:`AuthError` and its sub-types
    (:class:`InvalidCredentialsError`, :class:`InvalidTokenError`,
    :class:`TokenExpiredError`, …).

Hashing:
    :class:`PasswordHasher` — bcrypt wrapper, work-factor pinned via
    :class:`AuthConfig`.

Tokens:
    :class:`TokenIssuer`, :class:`AccessTokenClaims`,
    :class:`RefreshToken`, :func:`hash_refresh_token`.

Request-time:
    :class:`CurrentUser` — the authenticated principal value object.
    :func:`build_get_current_user` — the FastAPI dependency factory.
    :func:`require_admin` — sub-dependency for admin-only routes.
"""

from __future__ import annotations

from lai.common.auth.config import AuthConfig
from lai.common.auth.dependencies import build_get_current_user, require_admin
from lai.common.auth.exceptions import (
    AuthError,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidTokenError,
    PasswordPolicyError,
    TokenExpiredError,
    UserDisabledError,
    UserNotFoundError,
)
from lai.common.auth.hashing import PasswordHasher
from lai.common.auth.models import CurrentUser
from lai.common.auth.tokens import (
    AccessTokenClaims,
    RefreshToken,
    TokenIssuer,
    hash_refresh_token,
)

__all__ = [
    "AccessTokenClaims",
    "AuthConfig",
    "AuthError",
    "CurrentUser",
    "EmailAlreadyExistsError",
    "InvalidCredentialsError",
    "InvalidTokenError",
    "PasswordHasher",
    "PasswordPolicyError",
    "RefreshToken",
    "TokenExpiredError",
    "TokenIssuer",
    "UserDisabledError",
    "UserNotFoundError",
    "build_get_current_user",
    "hash_refresh_token",
    "require_admin",
]
