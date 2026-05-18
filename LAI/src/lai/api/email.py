"""Transactional email sender — v1 ships exactly one flow: password reset.

A single ~40-line function (with a tenacity retry, a Jinja-style
template, and a config object) sized exactly to the requirement. The
plan promotes this module to a full :mod:`lai.common.email` package
when a second email flow lands; until then, keeping it small and
local is the right call.

The Brevo API key never leaves the server. The send is always invoked
via a FastAPI :class:`BackgroundTask` from the route handler so the
HTTP response to ``/auth/forgot-password`` does not block on Brevo's
round-trip.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx
from pydantic import EmailStr, Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

__all__ = ["EmailConfig", "send_reset_email"]

_logger = logging.getLogger("lai.email")

_BREVO_SEND_URL: Final[str] = "https://api.brevo.com/v3/smtp/email"

_RESET_TEMPLATE_SUBJECT: Final[str] = "Reset your LAI password"
_RESET_TEMPLATE_BODY: Final[str] = (
    "Hello {full_name},\n"
    "\n"
    "We received a request to reset your LAI password. Click the link "
    "below to choose a new one. The link is valid for {ttl_minutes} minutes.\n"
    "\n"
    "{reset_url}\n"
    "\n"
    "If you didn't request this, you can safely ignore this email.\n"
    "\n"
    "— LAI"
)


class EmailConfig(BaseSettings):
    """Brevo + sender + UI-base configuration.

    All knobs read from ``LAI_EMAIL_*`` environment variables. The
    object is frozen — constructed once at app start, shared read-only.
    """

    model_config = SettingsConfigDict(
        env_prefix="LAI_EMAIL_",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    brevo_api_key: SecretStr = Field(
        description="Brevo API key, scoped to Transactional Email Send only.",
    )
    sender_email: EmailStr = Field(
        description="``From`` address. Must match a DNS-verified sender identity in Brevo.",
    )
    sender_name: str = Field(
        default="LAI",
        min_length=1,
        description="Display name attached to the From header.",
    )
    public_app_base_url: HttpUrl = Field(
        description=(
            "Public URL of the LAI UI. Used to build the reset link "
            "(``{base}/reset-password?token=...``). Must include the "
            "scheme and host the user's browser will see — i.e., the "
            "external URL, not the internal Docker name."
        ),
    )
    enabled: bool = Field(
        default=True,
        description=(
            "Set false in dev / CI to suppress the network call and "
            "log the rendered email instead. The reset-password flow "
            "itself still works; an engineer can pull the token from "
            "the logs."
        ),
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description="Per-request timeout against the Brevo API.",
    )


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
async def _post_brevo(
    config: EmailConfig,
    payload: dict[str, object],
) -> None:
    headers = {
        "api-key": config.brevo_api_key.get_secret_value(),
        "accept": "application/json",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
        response = await client.post(_BREVO_SEND_URL, json=payload, headers=headers)
        response.raise_for_status()


async def send_reset_email(
    config: EmailConfig,
    *,
    recipient_email: str,
    recipient_name: str,
    raw_reset_token: str,
    ttl_minutes: int,
) -> None:
    """Send a password-reset email via Brevo.

    The reset URL is composed as
    ``{public_app_base_url}/reset-password?token={raw_reset_token}``.
    The raw token is never logged; on send failure we record only the
    recipient and HTTP status.

    Args:
        config: Resolved email config.
        recipient_email: The address to send to. The route handler
            must only call this for an email that has just resolved
            to a real account — never blast Brevo with unknown
            addresses (rate-limits + reputation cost).
        recipient_name: ``users.full_name`` for the salutation.
        raw_reset_token: The opaque token value (the cookie of the
            reset flow). Goes into the URL query parameter.
        ttl_minutes: Lifetime of the token, in minutes — used in the
            body copy so the user knows how long they have.
    """
    reset_url = f"{str(config.public_app_base_url).rstrip('/')}/reset-password?token={raw_reset_token}"
    body = _RESET_TEMPLATE_BODY.format(
        full_name=recipient_name,
        reset_url=reset_url,
        ttl_minutes=ttl_minutes,
    )

    if not config.enabled:
        # Dev / CI path: log the body so an engineer can pluck the
        # token without standing up Brevo. The raw token *is* logged
        # here; this branch must never run in production.
        _logger.info(
            "email.disabled.reset_email",
            extra={
                "to": recipient_email,
                "subject": _RESET_TEMPLATE_SUBJECT,
                "body": body,
            },
        )
        return

    payload: dict[str, object] = {
        "sender": {"email": str(config.sender_email), "name": config.sender_name},
        "to": [{"email": recipient_email, "name": recipient_name}],
        "subject": _RESET_TEMPLATE_SUBJECT,
        "textContent": body,
    }
    try:
        await _post_brevo(config, payload)
    except (httpx.HTTPError, RetryError) as exc:
        # We swallow + log instead of raising into the BackgroundTask
        # runner. The user's response to /auth/forgot-password has
        # already been sent (204); raising here would only crash the
        # background worker. Log loudly so ops sees deliverability
        # regressions.
        status_code = (
            exc.response.status_code  # type: ignore[union-attr]
            if isinstance(exc, httpx.HTTPStatusError)
            else None
        )
        _logger.error(
            "email.send_failed",
            extra={
                "recipient": recipient_email,
                "subject": _RESET_TEMPLATE_SUBJECT,
                "status_code": status_code,
                "error": str(exc),
            },
        )
