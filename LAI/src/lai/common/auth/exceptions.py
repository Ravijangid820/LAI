"""Typed exception hierarchy for ``lai.common.auth``.

All exceptions raised by the auth module inherit from :class:`AuthError`
so callers (FastAPI dependencies, the auth router) can catch the full
failure surface with a single ``except`` clause and map to HTTP status
codes in one place. Sub-types let callers distinguish "this is a 401
credential miss" from "this is a 500 misconfiguration" without parsing
exception messages.

Design intent
-------------

* :class:`InvalidCredentialsError` — bcrypt verify failed *or* the user
  does not exist. Both surface as 401 so we never reveal which.
* :class:`InvalidTokenError` — JWT signature, claims, or lifetime
  rejected. Always 401. Sub-typed for log attribution only.
* :class:`TokenExpiredError` — separate from the parent so callers can
  distinguish "client may refresh" from "tampered token".
* :class:`UserDisabledError` — credentials matched but ``users.status``
  is not ``'active'``. 403, not 401, because the credential itself was
  valid.
* :class:`PasswordPolicyError` — new password failed policy at the
  application boundary; 400.
* :class:`EmailAlreadyExistsError` — signup collision. 409.
"""

from __future__ import annotations

__all__ = [
    "AuthError",
    "EmailAlreadyExistsError",
    "InvalidCredentialsError",
    "InvalidTokenError",
    "PasswordPolicyError",
    "TokenExpiredError",
    "UserDisabledError",
    "UserNotFoundError",
]


class AuthError(Exception):
    """Root exception for the ``lai.common.auth`` package."""


class InvalidCredentialsError(AuthError):
    """Email/password combination did not authenticate.

    Raised for both ``user not found`` and ``password mismatch`` to
    prevent account-enumeration via timing or status-code differences.
    """


class UserNotFoundError(AuthError):
    """A user lookup by id or email returned no row.

    Internal use only — *never* leak this to the network in a login or
    reset flow; map to :class:`InvalidCredentialsError` (login) or a
    204 (forgot-password) at the route boundary.
    """


class UserDisabledError(AuthError):
    """Credentials matched but the account is not ``status='active'``."""


class InvalidTokenError(AuthError):
    """A JWT failed signature, claim, or shape validation.

    Args:
        message: Human-readable reason. Safe to log; do **not** echo
            verbatim to the client — 401 with a generic body is the
            standard response.
    """


class TokenExpiredError(InvalidTokenError):
    """Token signature was valid but ``exp`` has passed.

    Separated from the parent so the FastAPI dependency can choose to
    emit a distinct ``WWW-Authenticate`` hint and the access-token
    refresh client can branch cleanly.
    """


class PasswordPolicyError(AuthError):
    """A new password did not meet the configured strength policy."""


class EmailAlreadyExistsError(AuthError):
    """Signup attempted with an email that is already registered."""
