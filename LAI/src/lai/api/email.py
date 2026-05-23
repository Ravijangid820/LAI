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

_REPORT_READY_TEMPLATE_SUBJECT: Final[str] = "Your LAI report is ready — {project_name}"
_REPORT_READY_TEMPLATE_BODY: Final[str] = (
    "Hello {full_name},\n"
    "\n"
    "Your due-diligence report for {project_name} has finished generating "
    "(took about {elapsed_minutes} minute(s)). You can open it from your "
    "LAI dashboard:\n"
    "\n"
    "{report_url}\n"
    "\n"
    "— LAI"
)

_REPORT_FAILED_TEMPLATE_SUBJECT: Final[str] = "Your LAI report didn't finish — {project_name}"
_REPORT_FAILED_TEMPLATE_BODY: Final[str] = (
    "Hello {full_name},\n"
    "\n"
    "We weren't able to finish your due-diligence report for "
    "{project_name}. Reason:\n"
    "\n"
    "    {error}\n"
    "\n"
    "You can retry from the dashboard, or reply to this email if the "
    "problem persists.\n"
    "\n"
    "Report id (for support): {report_id}\n"
    "\n"
    "— LAI"
)

_INVITE_TEMPLATE_SUBJECT: Final[str] = "You've been invited to {org_name} on LAI"
_INVITE_TEMPLATE_BODY: Final[str] = (
    "Hello,\n"
    "\n"
    "{inviter_name} has invited you to join {org_name} on LAI, "
    "a German legal due-diligence assistant for wind-energy matters.\n"
    "\n"
    "Click the link below to accept the invitation and finish setting up your "
    "account. You'll be asked for your name and to choose a password. The "
    "link is valid for {ttl_days} days.\n"
    "\n"
    "{invite_url}\n"
    "\n"
    "If you weren't expecting this invitation, you can safely ignore this email.\n"
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


async def send_invite_email(
    config: EmailConfig,
    *,
    recipient_email: str,
    raw_invite_token: str,
    org_name: str,
    inviter_name: str,
    ttl_days: int,
) -> None:
    """Send an organisation-invitation email via Brevo.

    The invite URL is composed as
    ``{public_app_base_url}/accept-invite?token={raw_invite_token}``.
    Mirrors :func:`send_reset_email` deliverability behaviour: dev-mode
    (``EmailConfig.enabled=False``) logs the rendered body so an
    engineer can pull the token without standing up Brevo; production
    swallows + structured-logs Brevo failures rather than crashing the
    BackgroundTask runner (the admin already got a 201 by then).

    Args:
        config: Resolved email config.
        recipient_email: The invited address. The admin endpoint must
            only call this for an email that just produced an
            ``org_invitations`` row, never for a free-text input.
        raw_invite_token: The opaque token value. Goes into the URL
            query parameter; never logged in the production branch.
        org_name: Display name of the inviting organisation, for the
            body copy ("you've been invited to {org_name}").
        inviter_name: ``users.full_name`` of the admin who pressed the
            invite button — useful context for the recipient.
        ttl_days: Lifetime of the invitation, used in the body copy
            so the user knows how long they have to accept.
    """
    invite_url = (
        f"{str(config.public_app_base_url).rstrip('/')}"
        f"/accept-invite?token={raw_invite_token}"
    )
    subject = _INVITE_TEMPLATE_SUBJECT.format(org_name=org_name)
    body = _INVITE_TEMPLATE_BODY.format(
        org_name=org_name,
        inviter_name=inviter_name,
        invite_url=invite_url,
        ttl_days=ttl_days,
    )

    if not config.enabled:
        # Dev / CI path: log the body (incl. raw token) so an engineer can
        # accept without standing up Brevo. Never runs in production.
        _logger.info(
            "email.disabled.invite_email",
            extra={
                "to": recipient_email,
                "subject": subject,
                "body": body,
            },
        )
        return

    payload: dict[str, object] = {
        "sender": {"email": str(config.sender_email), "name": config.sender_name},
        "to": [{"email": recipient_email}],
        "subject": subject,
        "textContent": body,
    }
    try:
        await _post_brevo(config, payload)
    except (httpx.HTTPError, RetryError) as exc:
        status_code = (
            exc.response.status_code  # type: ignore[union-attr]
            if isinstance(exc, httpx.HTTPStatusError)
            else None
        )
        _logger.error(
            "email.send_failed",
            extra={
                "recipient": recipient_email,
                "subject": subject,
                "status_code": status_code,
                "error": str(exc),
            },
        )


async def send_report_ready_email(
    config: EmailConfig,
    *,
    recipient_email: str,
    recipient_name: str,
    report_id: str,
    project_name: str,
    elapsed_minutes: int,
) -> None:
    """Send a "your DDiQ report is ready" email via Brevo.

    Fired from the Celery worker the moment ``_run_report_generation_job``
    sets the row to ``status='done'`` — so the user can close the tab the
    instant they submit and still know when the report is ready.

    Same deliverability posture as :func:`send_reset_email`: dev-mode
    (``EmailConfig.enabled=False``) logs the body so an engineer can
    follow the link without standing up Brevo; production swallows +
    structured-logs Brevo failures rather than crashing the worker.
    """
    report_url = (
        f"{str(config.public_app_base_url).rstrip('/')}"
        f"/dashboard/documents?report={report_id}"
    )
    subject = _REPORT_READY_TEMPLATE_SUBJECT.format(project_name=project_name)
    body = _REPORT_READY_TEMPLATE_BODY.format(
        full_name=recipient_name or recipient_email,
        project_name=project_name,
        elapsed_minutes=elapsed_minutes,
        report_url=report_url,
    )
    if not config.enabled:
        _logger.info(
            "email.disabled.report_ready",
            extra={"to": recipient_email, "subject": subject, "body": body},
        )
        return
    payload: dict[str, object] = {
        "sender": {"email": str(config.sender_email), "name": config.sender_name},
        "to": [{"email": recipient_email, "name": recipient_name or recipient_email}],
        "subject": subject,
        "textContent": body,
    }
    try:
        await _post_brevo(config, payload)
    except (httpx.HTTPError, RetryError) as exc:
        status_code = (
            exc.response.status_code  # type: ignore[union-attr]
            if isinstance(exc, httpx.HTTPStatusError) else None
        )
        _logger.error(
            "email.send_failed",
            extra={
                "recipient": recipient_email,
                "subject": subject,
                "status_code": status_code,
                "error": str(exc),
            },
        )


async def send_report_failed_email(
    config: EmailConfig,
    *,
    recipient_email: str,
    recipient_name: str,
    report_id: str,
    project_name: str,
    error: str,
) -> None:
    """Send a "your DDiQ report didn't finish" email via Brevo.

    Mirrors :func:`send_report_ready_email`. The user is notified on
    failure too, so they aren't left waiting indefinitely after closing
    the tab. The reason string is included verbatim — the worker has
    already truncated it to a sane length before calling this.
    """
    subject = _REPORT_FAILED_TEMPLATE_SUBJECT.format(project_name=project_name)
    body = _REPORT_FAILED_TEMPLATE_BODY.format(
        full_name=recipient_name or recipient_email,
        project_name=project_name,
        error=(error or "unknown error").strip()[:500],
        report_id=report_id,
    )
    if not config.enabled:
        _logger.info(
            "email.disabled.report_failed",
            extra={"to": recipient_email, "subject": subject, "body": body},
        )
        return
    payload: dict[str, object] = {
        "sender": {"email": str(config.sender_email), "name": config.sender_name},
        "to": [{"email": recipient_email, "name": recipient_name or recipient_email}],
        "subject": subject,
        "textContent": body,
    }
    try:
        await _post_brevo(config, payload)
    except (httpx.HTTPError, RetryError) as exc:
        status_code = (
            exc.response.status_code  # type: ignore[union-attr]
            if isinstance(exc, httpx.HTTPStatusError) else None
        )
        _logger.error(
            "email.send_failed",
            extra={
                "recipient": recipient_email,
                "subject": subject,
                "status_code": status_code,
                "error": str(exc),
            },
        )
