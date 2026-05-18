"""Configuration for :mod:`lai.common.auth`.

A single :class:`AuthConfig` :class:`~pydantic_settings.BaseSettings`
subclass owns every knob the auth subsystem exposes. All knobs read
from ``LAI_AUTH_*`` environment variables; defaults are production-safe
except :attr:`AuthConfig.jwt_access_secret`, which **must** be supplied
out-of-band (no default — a missing secret is a startup failure, not
a runtime surprise).

Configuration sources, in precedence order (highest first):

1. Keyword arguments to ``AuthConfig(...)``.
2. Environment variables prefixed ``LAI_AUTH_`` (case-insensitive).
3. The defaults declared here.

The settings object is **frozen** — construct once at process start,
share read-only. Mutations raise :class:`pydantic.ValidationError`.

Example
-------

::

    cfg = AuthConfig()  # picks up env
    cfg = AuthConfig(jwt_access_ttl_minutes=5)  # tighter for tests
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["AuthConfig"]


class AuthConfig(BaseSettings):
    """Settings for the auth subsystem.

    Every field has a production-quality default *except*
    :attr:`jwt_access_secret`, which must be supplied via env or kwarg.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_AUTH_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    # ── JWT signing ─────────────────────────────────────────────────────
    jwt_access_secret: SecretStr = Field(
        description=(
            "HS256 signing secret for the short-lived access token. "
            "Must be a high-entropy random string (>= 32 bytes). Never "
            "commit; load from a server-side secret store. Rotating "
            "this secret invalidates all in-flight access tokens — "
            "users will be auto-refreshed via the cookie path."
        ),
    )
    jwt_algorithm: Literal["HS256"] = Field(
        default="HS256",
        description=(
            "JWT signing algorithm. Pinned to HS256 in v1 — never "
            "accept ``alg=none``; if asymmetric is needed later (RS256 "
            "for downstream service verification) bump the version "
            "and add the public key knobs."
        ),
    )
    jwt_issuer: str = Field(
        default="lai",
        min_length=1,
        description=(
            "JWT ``iss`` claim. Used for audit attribution and to "
            "reject tokens minted by a different service if multiple "
            "issuers ever share a verifier."
        ),
    )
    jwt_access_ttl_minutes: int = Field(
        default=15,
        gt=0,
        le=120,
        description=(
            "Lifetime of the access token in minutes. Kept short so "
            "the absence of a per-request revocation list is safe; the "
            "refresh-token cookie is the long-lived secret."
        ),
    )

    # ── Refresh token ───────────────────────────────────────────────────
    jwt_refresh_ttl_days: int = Field(
        default=30,
        gt=0,
        le=365,
        description="Default refresh-token lifetime when ``remember_me`` is false.",
    )
    jwt_refresh_ttl_days_remember_me: int = Field(
        default=90,
        gt=0,
        le=365,
        description="Refresh-token lifetime when the client opted into 'Keep me signed in'.",
    )
    refresh_cookie_name: str = Field(
        default="lai_refresh",
        min_length=1,
        description="Name of the http-only refresh cookie set on /auth/login and /auth/refresh.",
    )
    refresh_cookie_path: str = Field(
        default="/auth",
        description=(
            "Path attribute of the refresh cookie. Scoped to ``/auth`` so "
            "the cookie is only attached to refresh + logout requests, "
            "never to chat / DDiQ data routes."
        ),
    )
    refresh_cookie_secure: bool = Field(
        default=True,
        description=(
            "Set ``Secure`` on the refresh cookie. Default true; flip "
            "to false **only** for local plaintext development."
        ),
    )
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = Field(
        default="lax",
        description=(
            "``SameSite`` attribute on the refresh cookie. ``lax`` works "
            "when UI and API share an eTLD+1; cross-origin deployments "
            "must set ``none`` (and ``Secure=True``) and the API CORS "
            "preflight must allow credentials. See AUTH_PLAN Q11."
        ),
    )

    # ── Password hashing ────────────────────────────────────────────────
    bcrypt_rounds: int = Field(
        default=12,
        ge=10,
        le=15,
        description=(
            "bcrypt work factor. 12 is the OWASP 2024 floor; 13/14 "
            "for higher-assurance deployments at the cost of ~2x / 4x "
            "login latency. Capped at 15 to bound login wall-clock."
        ),
    )

    # ── Password policy (applied on signup + reset) ─────────────────────
    password_min_length: int = Field(
        default=12,
        ge=8,
        description="Minimum password length in characters.",
    )
    password_max_length: int = Field(
        default=128,
        ge=64,
        description=(
            "Maximum password length in characters. bcrypt truncates "
            "inputs > 72 bytes; we hard-cap higher to keep error "
            "messages user-friendly but never silently truncate."
        ),
    )

    # ── Reset token ─────────────────────────────────────────────────────
    reset_token_ttl_minutes: int = Field(
        default=30,
        gt=0,
        le=240,
        description="Lifetime of a single-use password-reset token.",
    )

    # ── Validators ──────────────────────────────────────────────────────
    @field_validator("jwt_access_secret")
    @classmethod
    def _check_secret_entropy(cls, value: SecretStr) -> SecretStr:
        """Reject obviously weak signing secrets at startup."""
        raw = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError(
                "jwt_access_secret must be at least 32 characters (256 bits of entropy)",
            )
        return value

    @field_validator("password_max_length")
    @classmethod
    def _check_password_bounds(cls, value: int, info: object) -> int:
        """Ensure max >= min so the policy is satisfiable."""
        min_len = getattr(info, "data", {}).get("password_min_length")
        if min_len is not None and value < min_len:
            raise ValueError(
                f"password_max_length ({value}) must be >= password_min_length ({min_len})",
            )
        return value
