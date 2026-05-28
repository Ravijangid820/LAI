"""JWT access tokens + opaque refresh tokens.

Two distinct token primitives, both routed through this module:

* **Access token** — short-lived (15 min default), HS256-signed JWT.
  Stateless: the server keeps no per-token state and validates by
  signature + lifetime alone. Carries the user id (``sub``), email,
  and role so the FastAPI dependency does not need to round-trip to
  Postgres for every authenticated request.
* **Refresh token** — opaque, 256-bit random string. The raw value is
  given to the client (in an http-only cookie); the **sha256** of the
  raw value is stored in the ``refresh_tokens`` table. We never store
  the raw value, so a database leak cannot be replayed as a session.

Algorithm and claims are pinned in code, not on the wire: we always
decode with ``algorithms=[<configured>]`` to defeat the historical
``alg: none`` confusion attack, and we require an ``iss`` match. There
is no ``aud`` claim in v1 — single audience.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from lai.common.auth.config import AuthConfig
from lai.common.auth.exceptions import InvalidTokenError, TokenExpiredError

__all__ = [
    "AccessTokenClaims",
    "RefreshToken",
    "TokenIssuer",
    "hash_refresh_token",
]

# 32 bytes → 256 bits of entropy; encoded URL-safe base64 (~43 chars).
# Long enough that brute-force or birthday collisions are not credible
# attacks even with a 90-day TTL and many millions of issued tokens.
_REFRESH_TOKEN_NBYTES: Final[int] = 32


def hash_refresh_token(raw: str) -> str:
    """sha256 hex digest of a raw refresh token.

    Used both at issue time (to compute ``token_hash`` for insertion)
    and at validation time (to look up the row given the cookie value).
    sha256 is appropriate here — the input has 256 bits of entropy by
    construction, so we do not need a slow KDF; bcrypt would add login
    latency for zero security gain on a high-entropy secret.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """Decoded, validated access-token payload.

    Attributes:
        user_id: ``sub`` claim, parsed back to :class:`uuid.UUID`.
        email: ``email`` claim, the canonical login email.
        role: ``role`` claim, one of ``'user'`` or ``'admin'``.
        org_id: ``org_id`` claim — the firm this user belongs to, or
            ``None`` for an org-less (just-signed-up, not-yet-placed)
            user. Optional on the wire: tokens minted before firm
            tenancy (migration 002) omit it and decode to ``None``.
        issued_at: ``iat`` as an aware UTC datetime.
        expires_at: ``exp`` as an aware UTC datetime.
    """

    user_id: UUID
    email: str
    role: str
    org_id: UUID | None
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """A freshly minted refresh token, in two forms.

    Attributes:
        raw: The opaque value to send to the client (cookie body).
            Treat as a secret; never log.
        token_hash: sha256 hex digest of ``raw``, suitable for storing
            in ``refresh_tokens.token_hash``.
        expires_at: Aware UTC datetime; mirrored to ``expires_at`` in
            the row.
    """

    raw: str
    token_hash: str
    expires_at: datetime


class TokenIssuer:
    """Issues and verifies access JWTs and opaque refresh tokens.

    Construct once at app startup with the resolved
    :class:`AuthConfig` and share read-only.
    """

    __slots__ = ("_config",)

    def __init__(self, config: AuthConfig) -> None:
        self._config: AuthConfig = config

    # ── Access token ────────────────────────────────────────────────────
    def issue_access_token(
        self,
        *,
        user_id: UUID,
        email: str,
        role: str,
        org_id: UUID | None = None,
        now: datetime | None = None,
    ) -> tuple[str, int]:
        """Mint a fresh access JWT.

        Args:
            user_id: The user's primary key. Becomes the ``sub`` claim
                (stringified UUID).
            email: The user's canonical email. Becomes the ``email`` claim.
                Carried so the request handler does not need a DB lookup.
            role: ``'user'`` or ``'admin'``. Carried for the same reason.
            org_id: The user's firm, or ``None`` for an org-less user.
                Becomes the ``org_id`` claim so tenant scoping (Phase B)
                needs no per-request DB round-trip.
            now: Override the "now" reference for deterministic tests.
                Defaults to :func:`datetime.now` (UTC).

        Returns:
            ``(token, expires_in_seconds)`` — the encoded JWT and the
            integer seconds until ``exp``, suitable for return in the
            ``{ access_token, expires_in }`` response body.
        """
        issued = now or datetime.now(UTC)
        ttl = timedelta(minutes=self._config.jwt_access_ttl_minutes)
        expires = issued + ttl
        claims: dict[str, Any] = {
            "iss": self._config.jwt_issuer,
            "sub": str(user_id),
            "email": email,
            "role": role,
            "org_id": str(org_id) if org_id is not None else None,
            "iat": int(issued.timestamp()),
            "exp": int(expires.timestamp()),
        }
        token = jwt.encode(
            claims,
            self._config.jwt_access_secret.get_secret_value(),
            algorithm=self._config.jwt_algorithm,
        )
        return token, int(ttl.total_seconds())

    def decode_access_token(self, token: str) -> AccessTokenClaims:
        """Verify signature + lifetime and parse claims.

        Args:
            token: The raw JWT string from the ``Authorization`` header.

        Returns:
            The validated, parsed :class:`AccessTokenClaims`.

        Raises:
            TokenExpiredError: ``exp`` has passed.
            InvalidTokenError: Anything else — signature mismatch,
                tampered payload, wrong issuer, missing/malformed
                required claim, unexpected algorithm.
        """
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._config.jwt_access_secret.get_secret_value(),
                algorithms=[self._config.jwt_algorithm],
                issuer=self._config.jwt_issuer,
                options={"require": ["exp", "iat", "sub", "email", "role"]},
            )
        except ExpiredSignatureError as exc:
            raise TokenExpiredError("access token expired") from exc
        except JWTError as exc:
            raise InvalidTokenError(f"access token rejected: {exc}") from exc

        try:
            user_id = UUID(str(payload["sub"]))
        except (KeyError, ValueError) as exc:
            raise InvalidTokenError("access token has malformed sub claim") from exc

        email_raw = payload.get("email")
        role_raw = payload.get("role")
        if not isinstance(email_raw, str) or not isinstance(role_raw, str):
            raise InvalidTokenError("access token has malformed email/role claim")

        # ``org_id`` is optional on the wire: a null/absent value is a
        # valid org-less user, and tokens minted before firm tenancy omit
        # it entirely. Only a *present but unparseable* value is rejected.
        org_raw = payload.get("org_id")
        org_id: UUID | None = None
        if org_raw is not None:
            try:
                org_id = UUID(str(org_raw))
            except ValueError as exc:
                raise InvalidTokenError("access token has malformed org_id claim") from exc

        return AccessTokenClaims(
            user_id=user_id,
            email=email_raw,
            role=role_raw,
            org_id=org_id,
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )

    # ── Refresh token ───────────────────────────────────────────────────
    def issue_refresh_token(
        self,
        *,
        remember_me: bool = False,
        now: datetime | None = None,
    ) -> RefreshToken:
        """Mint a fresh opaque refresh token.

        Args:
            remember_me: If true, use the longer
                :attr:`AuthConfig.jwt_refresh_ttl_days_remember_me`
                lifetime; otherwise the default.
            now: Override "now" for deterministic tests.

        Returns:
            A :class:`RefreshToken` carrying the raw value (to be
            cookied to the client) and the sha256 hash + expiry (to be
            inserted into ``refresh_tokens``).
        """
        issued = now or datetime.now(UTC)
        days = self._config.jwt_refresh_ttl_days_remember_me if remember_me else self._config.jwt_refresh_ttl_days
        raw = secrets.token_urlsafe(_REFRESH_TOKEN_NBYTES)
        return RefreshToken(
            raw=raw,
            token_hash=hash_refresh_token(raw),
            expires_at=issued + timedelta(days=days),
        )

    # ── One-shot invitation token (same primitive, different table) ────
    def issue_invite_token(
        self,
        *,
        ttl_days: int = 7,
        now: datetime | None = None,
    ) -> RefreshToken:
        """Mint a single-use organisation-invitation token.

        Reuses the high-entropy random + sha256 storage primitive — the
        raw value is what goes into the invite URL, the sha256 hex is
        what's persisted on ``org_invitations.token_hash``.

        Args:
            ttl_days: Lifetime in days (default 7). Bounded loosely;
                the caller picks the policy.
            now: Override "now" for deterministic tests.

        Returns:
            A :class:`RefreshToken` (raw + hash + expires_at). The same
            value object as :meth:`issue_reset_token` since the shape is
            identical; only the persistence table differs.
        """
        issued = now or datetime.now(UTC)
        raw = secrets.token_urlsafe(_REFRESH_TOKEN_NBYTES)
        return RefreshToken(
            raw=raw,
            token_hash=hash_refresh_token(raw),
            expires_at=issued + timedelta(days=ttl_days),
        )

    # ── One-shot reset token (same primitive, different table) ──────────
    def issue_reset_token(
        self,
        *,
        now: datetime | None = None,
    ) -> RefreshToken:
        """Mint a single-use password-reset token.

        Reuses the refresh-token primitive (high-entropy random string +
        sha256 storage) with the reset-token TTL from config.

        Returns:
            A :class:`RefreshToken` instance — same shape, used for
            the ``password_reset_tokens`` row. The raw value is what
            goes into the reset-link query parameter.
        """
        issued = now or datetime.now(UTC)
        raw = secrets.token_urlsafe(_REFRESH_TOKEN_NBYTES)
        return RefreshToken(
            raw=raw,
            token_hash=hash_refresh_token(raw),
            expires_at=issued + timedelta(minutes=self._config.reset_token_ttl_minutes),
        )
