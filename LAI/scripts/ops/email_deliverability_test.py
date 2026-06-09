#!/usr/bin/env -S python3 -u
"""Email deliverability test harness — Phase 4.5.4.

Sends one of each transactional template (password-reset, org-invite,
report-ready, report-failed) to each target inbox, captures Brevo's
messageId per send, and prints a checklist for the human-in-the-loop
inbox-vs-spam verification.

Usage:
    # dry-run — shows what WOULD be sent, no Brevo call
    python scripts/ops/email_deliverability_test.py \\
        --to outlook365@yourcorp.com \\
        --to dmarc-strict@yourgws.com \\
        --to test@gmx.de \\
        --to test@web.de \\
        --to custom@your-domain.example

    # actually send (requires --yes)
    python scripts/ops/email_deliverability_test.py \\
        --to <addr> [--to <addr> ...] --yes

    # send only one template type
    python scripts/ops/email_deliverability_test.py \\
        --to <addr> --template invite --yes

Hard safety rails:
    - Refuses to send if LAI_EMAIL_SENDER_EMAIL looks like a freemail
      address (gmail / yahoo / outlook.com / web.de / gmx.de etc.).
      Brevo will not DKIM-align with these and the test result is
      pre-determined: spam. See blueprint 2026-06-10-email-deliverability.md.
    - Refuses to send if LAI_EMAIL_PUBLIC_APP_BASE_URL points at an
      RFC1918 / loopback IP. The call-to-action link would be dead
      for external recipients — the test would mislead.
    - --yes is required to actually fire. Default is dry-run.

Output:
    - Per send: template, recipient, recipient MX provider (via dig),
      Brevo messageId, HTTP status.
    - Final checklist: for each recipient × template, a line ready
      to be ticked "INBOX" or "SPAM" once the human verifies.

Reads the same LAI_EMAIL_* env vars as production (lai.api.email):
    LAI_EMAIL_BREVO_API_KEY        — required for --yes
    LAI_EMAIL_SENDER_EMAIL         — required
    LAI_EMAIL_SENDER_NAME          — default "LAI"
    LAI_EMAIL_PUBLIC_APP_BASE_URL  — required (must be public-reachable)
    LAI_EMAIL_TIMEOUT_SECONDS      — default 10
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import IPv4Address, AddressValueError
from typing import Final
from urllib.parse import urlparse

# Import the template strings from production so they can't drift.
# scripts/ops/ runs from repo root via the .venv, lai package is importable.
sys.path.insert(0, "/data/projects/lai/LAI/src")
from lai.api.email import (  # noqa: E402
    _RESET_TEMPLATE_SUBJECT,
    _RESET_TEMPLATE_BODY,
    _INVITE_TEMPLATE_SUBJECT,
    _INVITE_TEMPLATE_BODY,
    _REPORT_READY_TEMPLATE_SUBJECT,
    _REPORT_READY_TEMPLATE_BODY,
    _REPORT_FAILED_TEMPLATE_SUBJECT,
    _REPORT_FAILED_TEMPLATE_BODY,
)

import httpx  # noqa: E402

_BREVO_SEND_URL: Final[str] = "https://api.brevo.com/v3/smtp/email"

_FREEMAIL_DOMAINS: Final[frozenset[str]] = frozenset({
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.de", "yahoo.co.uk",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "web.de", "gmx.de", "gmx.net", "gmx.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "aol.de",
    "t-online.de", "freenet.de",
    "proton.me", "protonmail.com",
})

_TEMPLATES: Final[tuple[str, ...]] = ("reset", "invite", "report_ready", "report_failed")


@dataclass(frozen=True)
class _Config:
    brevo_api_key: str
    sender_email: str
    sender_name: str
    public_app_base_url: str
    timeout_seconds: float


def _load_config(env: dict[str, str]) -> _Config:
    """Mirror the lai.api.email.EmailConfig but without pydantic — keeps the
    harness importable even when the venv's pydantic-settings is unhappy."""
    missing: list[str] = []
    for key in ("LAI_EMAIL_BREVO_API_KEY", "LAI_EMAIL_SENDER_EMAIL", "LAI_EMAIL_PUBLIC_APP_BASE_URL"):
        if not env.get(key):
            missing.append(key)
    if missing:
        sys.exit(f"FATAL: missing env vars: {', '.join(missing)}\n"
                 f"Source LAI/.env.auth first (or LAI/micro-services/.env per your setup).")
    return _Config(
        brevo_api_key=env["LAI_EMAIL_BREVO_API_KEY"],
        sender_email=env["LAI_EMAIL_SENDER_EMAIL"],
        sender_name=env.get("LAI_EMAIL_SENDER_NAME", "LAI"),
        public_app_base_url=env["LAI_EMAIL_PUBLIC_APP_BASE_URL"],
        timeout_seconds=float(env.get("LAI_EMAIL_TIMEOUT_SECONDS", "10")),
    )


def _is_private_ip(host: str) -> bool:
    try:
        addr = IPv4Address(host)
    except (AddressValueError, ValueError):
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _guard_config(config: _Config, *, dry_run: bool) -> None:
    """Block the most catastrophic misconfigurations before burning Brevo
    quota on a pre-determined failure."""
    sender_domain = config.sender_email.split("@", 1)[-1].lower()
    if sender_domain in _FREEMAIL_DOMAINS:
        msg = (
            f"FATAL: LAI_EMAIL_SENDER_EMAIL is a freemail address "
            f"({config.sender_email}). Brevo cannot DKIM-align with "
            f"{sender_domain} — the result is pre-determined: spam at every "
            f"corporate receiver. Configure a controlled subdomain "
            f"first; see rj/blueprint/2026-06-10-email-deliverability.md."
        )
        if not dry_run:
            sys.exit(msg)
        print(f"[guard] WOULD FAIL: {msg}\n")

    parsed = urlparse(config.public_app_base_url)
    host = parsed.hostname or ""
    if _is_private_ip(host) or host in ("localhost", ""):
        msg = (
            f"FATAL: LAI_EMAIL_PUBLIC_APP_BASE_URL ({config.public_app_base_url}) "
            f"is not internet-reachable. Test recipients cannot resolve "
            f"{host}; the call-to-action link would be dead and the test would mislead."
        )
        if not dry_run:
            sys.exit(msg)
        print(f"[guard] WOULD FAIL: {msg}\n")


def _mx_provider(email: str) -> str:
    """Best-effort 'who runs this inbox' from MX. Read-only DNS lookup."""
    if not shutil.which("dig"):
        return "(dig not installed)"
    domain = email.split("@", 1)[-1]
    try:
        result = subprocess.run(
            ["dig", "+short", "MX", domain],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except subprocess.TimeoutExpired:
        return "(MX lookup timeout)"
    lines = [line for line in result.stdout.strip().split("\n") if line]
    if not lines:
        return "(no MX)"
    first = lines[0].lower()
    if "google" in first or "googlemail" in first:
        return "Google Workspace / Gmail"
    if "outlook" in first or "protection.outlook" in first or "office365" in first:
        return "Microsoft 365 / Outlook"
    if "gmx" in first:
        return "GMX"
    if "web.de" in first or "kundenserver.de" in first or "1and1" in first or "ionos" in first:
        return "1&1 / IONOS / web.de"
    if "yahoo" in first:
        return "Yahoo"
    if "mailgun" in first:
        return "Mailgun"
    if "amazonses" in first:
        return "Amazon SES"
    return first.rstrip(".").split()[-1] if first else "(unknown)"


def _render(config: _Config, template: str, recipient_email: str) -> tuple[str, str]:
    """Return (subject, body) for the chosen template, with realistic-looking
    test data so the body resembles what users actually receive."""
    recipient_name = "Test Recipient"
    fake_token = uuid.uuid4().hex  # 32 hex chars — same shape as a real opaque token
    ttl_minutes = 30
    ttl_days = 7
    org_name = "Wind-DD Pilot GmbH"
    inviter_name = "Ravi Jangid"
    project_name = "Pilot-Park Nord"
    elapsed_minutes = 4
    report_id = uuid.uuid4().hex[:12]
    base = config.public_app_base_url.rstrip("/")

    if template == "reset":
        reset_url = f"{base}/reset-password?token={fake_token}"
        return _RESET_TEMPLATE_SUBJECT, _RESET_TEMPLATE_BODY.format(
            full_name=recipient_name,
            reset_url=reset_url,
            ttl_minutes=ttl_minutes,
        )
    if template == "invite":
        invite_url = f"{base}/accept-invite?token={fake_token}"
        return _INVITE_TEMPLATE_SUBJECT.format(org_name=org_name), _INVITE_TEMPLATE_BODY.format(
            org_name=org_name,
            inviter_name=inviter_name,
            invite_url=invite_url,
            ttl_days=ttl_days,
        )
    if template == "report_ready":
        report_url = f"{base}/dashboard/documents?report={report_id}"
        return (
            _REPORT_READY_TEMPLATE_SUBJECT.format(project_name=project_name),
            _REPORT_READY_TEMPLATE_BODY.format(
                full_name=recipient_name,
                project_name=project_name,
                elapsed_minutes=elapsed_minutes,
                report_url=report_url,
            ),
        )
    if template == "report_failed":
        return (
            _REPORT_FAILED_TEMPLATE_SUBJECT.format(project_name=project_name),
            _REPORT_FAILED_TEMPLATE_BODY.format(
                full_name=recipient_name,
                project_name=project_name,
                error="(synthetic) deliverability test — ignore",
                report_id=report_id,
            ),
        )
    sys.exit(f"unknown template: {template}")


def _send(config: _Config, *, subject: str, body: str, recipient_email: str) -> dict[str, object]:
    """POST directly to Brevo and return (status_code, response body or error)."""
    payload = {
        "sender": {"email": config.sender_email, "name": config.sender_name},
        "to": [{"email": recipient_email, "name": "Test Recipient"}],
        "subject": f"[DELIVERY-TEST] {subject}",  # prefix so harsh can filter in his inbox
        "textContent": body + "\n\n--\n(Deliverability test mail. Safe to delete.)",
        # Headers help us trace + tell receivers it's transactional, not bulk.
        "headers": {
            "X-LAI-DeliveryTest": "true",
            "X-LAI-TestTimestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    headers = {
        "api-key": config.brevo_api_key,
        "accept": "application/json",
        "content-type": "application/json",
    }
    try:
        response = httpx.post(_BREVO_SEND_URL, json=payload, headers=headers, timeout=config.timeout_seconds)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc), "status": None, "messageId": None}
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text[:200]}
    return {
        "ok": response.is_success,
        "status": response.status_code,
        "messageId": data.get("messageId") if isinstance(data, dict) else None,
        "response": data,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--to", action="append", required=True, metavar="EMAIL",
                        help="recipient address (repeatable; min 1, recommended 5)")
    parser.add_argument("--template", choices=("all", *_TEMPLATES), default="all",
                        help="which template to send (default: all 4)")
    parser.add_argument("--yes", action="store_true",
                        help="actually fire the Brevo calls (default is dry-run)")
    args = parser.parse_args()

    config = _load_config(dict(os.environ))
    _guard_config(config, dry_run=not args.yes)

    print("=" * 72)
    print(f" mode: {'LIVE SEND' if args.yes else 'DRY RUN'}")
    print(f" sender:        {config.sender_email}")
    print(f" base URL:      {config.public_app_base_url}")
    print(f" recipients:    {len(args.to)}")
    templates = _TEMPLATES if args.template == "all" else (args.template,)
    print(f" templates:     {', '.join(templates)}")
    print(f" total sends:   {len(args.to) * len(templates)}")
    print("=" * 72)
    print()

    results: list[dict[str, object]] = []
    for to in args.to:
        provider = _mx_provider(to)
        print(f"→ {to}  ({provider})")
        for template in templates:
            subject, body = _render(config, template, to)
            print(f"    [{template:>13}]  '{subject[:60]}'")
            if not args.yes:
                results.append({
                    "to": to, "provider": provider, "template": template,
                    "subject": subject, "would_send": True,
                })
                continue
            outcome = _send(config, subject=subject, body=body, recipient_email=to)
            results.append({
                "to": to, "provider": provider, "template": template,
                "subject": subject, **outcome,
            })
            status = outcome.get("status")
            msg_id = outcome.get("messageId") or "(none)"
            ok_flag = "✓" if outcome.get("ok") else "✗"
            print(f"        {ok_flag} status={status}  messageId={msg_id}")
            if not outcome.get("ok"):
                print(f"        ERROR: {outcome.get('response') or outcome.get('error')}")
        print()

    print("=" * 72)
    if args.yes:
        ok_count = sum(1 for r in results if r.get("ok"))
        print(f" Brevo accepted: {ok_count}/{len(results)}")
        print()
        print(" Human verification checklist — open each inbox and tick:")
        print(" (subject lines all start with [DELIVERY-TEST])")
        for r in results:
            mark = "[ ]"
            print(f"   {mark} INBOX or SPAM  ?  {r['to']:35} | {r['template']:>13} | {r['provider']}")
    else:
        print(" DRY RUN complete. Re-run with --yes to actually send.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
