# Email deliverability — Phase 4.5.4

**Date:** 2026-06-10 · **Owner:** rj (engineering) + boss / IT (DNS) ·
**Status:** ENGINEERING READY — blocked on DNS access at `blockland.ae`

## TL;DR

The PROGRESS_V2 4.5.4 row reads "configure Brevo SPF/DKIM/DMARC
properly if mail lands in spam." It assumes a real sender domain
already exists. **It doesn't.** Current `LAI_EMAIL_SENDER_EMAIL` is
a personal Gmail address (`vinodfinance07@gmail.com`), and
`LAI_EMAIL_PUBLIC_APP_BASE_URL` is a LAN IP
(`http://192.168.178.82:5173`). Test-mailing five corporate inboxes
in the current state would teach us nothing we don't already know
— it'll land in spam everywhere and the call-to-action link would
be unreachable for the recipients.

Real shape of 4.5.4: **pick a controlled sender subdomain on
`blockland.ae`, plant 3 DNS records, verify in Brevo, point env at
it, retest.**

## What's deployed today (verified 2026-06-10)

| Knob | Current value | Status |
|---|---|---|
| Provider | Brevo (`api.brevo.com/v3/smtp/email`) | ✅ — production-grade |
| Templates wired | 4 — password-reset, org-invite, report-ready, report-failed | ✅ — all in [`lai.api.email`](../LAI/src/lai/api/email.py) |
| `LAI_EMAIL_BREVO_API_KEY` | Set (value not inspected) | ✅ |
| `LAI_EMAIL_SENDER_EMAIL` | `vinodfinance07@gmail.com` | ❌ **broken** |
| `LAI_EMAIL_SENDER_NAME` | `LAI` | ✅ |
| `LAI_EMAIL_PUBLIC_APP_BASE_URL` | `http://192.168.178.82:5173` | ❌ **broken** (LAN IP, not reachable from outside the office) |
| `LAI_EMAIL_ENABLED` | `true` | ✅ |

## Why the gmail sender breaks deliverability

1. **gmail.com SPF** (`v=spf1 redirect=_spf.google.com`) authorises
   ONLY Google's IPs to send for `@gmail.com`. Brevo's outbound IPs
   are not in `_spf.google.com`. Therefore every Brevo send From:
   `vinodfinance07@gmail.com` **fails SPF** at every recipient on
   the planet.
2. **gmail.com DKIM** is signed by Google's `20230601._domainkey.gmail.com`
   selector. Brevo signs with its own selector
   (`mail._domainkey.brevo.com`), which is NOT a `gmail.com`-aligned
   key. **DKIM alignment fails.**
3. **gmail.com DMARC** is `p=none; sp=quarantine`. The "none" means
   gmail's policy is *report only* — receivers are not instructed to
   hard-reject. But Outlook, Microsoft 365, Google Workspace with
   strict DMARC, and most corporate gateways treat
   `SPF=fail + DKIM no-alignment + DMARC=none` as the textbook
   "looks like a spoofing attempt" signal — typical outcome is the
   **spam folder**, sometimes a soft bounce.

Net: ~today every transactional mail we send already lands in spam
at most corporate receivers. The 5-inbox test would only confirm
this; it wouldn't help us fix it.

## Why the LAN-IP base URL is also broken

Reset/invite/report mails embed a URL built as
`{public_app_base_url}/reset-password?token=...`. Today that
resolves to `http://192.168.178.82:5173/reset-password?token=...` —
a private RFC1918 IP. Recipients outside the office network cannot
resolve or reach `192.168.178.82`. Even if a mail lands in inbox,
the call-to-action is dead.

This is a **separate decision** from the sender-domain choice but
must close in the same change to be useful: we need both a
controlled sender domain AND a public app hostname before the test
harness has anything meaningful to measure.

## Recommended fix — `lai.blockland.ae` subdomain

`blockland.ae` is the org domain (verified: A record →
`89.31.143.90`, MX → Outlook 365, SPF locked to
`include:spf.protection.outlook.com -all`). Using a **subdomain**
keeps Brevo's sending IPs separate from the strict Outlook SPF and
preserves the parent domain's existing reputation. This is the
standard SaaS pattern (cf. `mail.stripe.com`,
`notifications.linear.app`, `email.notion.com`).

Subdomain proposal: **`lai.blockland.ae`** — one subdomain serves
both the From: identity *and* the public app URL (`https://lai.blockland.ae`).
Alternatives if `lai.blockland.ae` is reserved for something else:
`mail.lai.blockland.ae` / `app.lai.blockland.ae` split, or
`mailer.blockland.ae` + `app.blockland.ae`.

### What changes once the subdomain is decided

| Knob | New value (with `lai.blockland.ae`) |
|---|---|
| `LAI_EMAIL_SENDER_EMAIL` | `no-reply@lai.blockland.ae` |
| `LAI_EMAIL_PUBLIC_APP_BASE_URL` | `https://lai.blockland.ae` (assumes the same subdomain hosts the UI) |
| Brevo sender identity | Add `lai.blockland.ae` as an **authorised sending domain** in Brevo console → Senders, Domains → Domains tab → "Add a domain" |
| DNS records on `blockland.ae` | 3 to 4 new records under `lai` subdomain — see below |

### Exact DNS records to add (under `blockland.ae` DNS host)

Brevo's standard config (Brevo console → Senders, Domains → Domains
→ "Authenticate this domain" gives you these verbatim — the
selectors and target values are stable across all Brevo accounts
unless they rotate them, which they don't typically):

| Type | Host | Value |
|---|---|---|
| TXT | `lai.blockland.ae` | `v=spf1 include:spf.brevo.com -all` |
| CNAME | `mail._domainkey.lai.blockland.ae` | (Brevo console gives the exact target — typically `mail.<your-brevo-account-id>.domainkey1.brevo.com` or similar) |
| CNAME | `mail2._domainkey.lai.blockland.ae` | (Brevo's second selector — also from the console) |
| TXT | `_dmarc.lai.blockland.ae` | `v=DMARC1; p=quarantine; rua=mailto:postmaster@blockland.ae; ruf=mailto:postmaster@blockland.ae; fo=1; adkim=r; aspf=r` |

Notes on the DMARC record:

- `p=quarantine` (not `reject`) — appropriate for a launch. Misfires
  go to spam, not bounce. Step up to `p=reject` only after seeing 2-4
  weeks of clean `rua=` reports.
- `rua=` / `ruf=` to `postmaster@blockland.ae` — make sure that
  mailbox exists. If it doesn't, point at a real monitored inbox.
- `adkim=r` / `aspf=r` — relaxed alignment. Lets the parent domain
  inherit alignment from subdomain sends if we later send From:
  `@blockland.ae` rather than From: `@lai.blockland.ae`.
- **DO NOT touch the existing `blockland.ae` SPF or add a `_dmarc.blockland.ae`
  record without checking with IT** — the parent SPF is currently
  Outlook-locked (`-all`), and silently adding a parent DMARC could
  reject existing Outlook mail flow. The subdomain `_dmarc.lai.blockland.ae`
  is independent and safe to add.

### Verification once propagated

Brevo's console will show `Domain authenticated ✓` once SPF + DKIM
propagate (1-2 hours typical, up to 24 h on slow DNS). Then:

1. Update `LAI/.env.auth` with the new sender email + base URL.
2. `bash scripts/ops/restart_serve_rag.sh` per the standard restart
   pattern.
3. Run `scripts/ops/email_deliverability_test.py` (this commit) →
   sends one of each of the 4 templates to a list of target
   inboxes, logs Brevo's `messageId` for each, prints a checklist.
4. The 5 target inboxes from PROGRESS_V2 4.5.4 — manually open each
   to confirm Inbox vs Spam:
   - Outlook 365 (e.g., `*@blockland.ae`)
   - Google Workspace with strict DMARC (boss has one?)
   - `gmx.de`
   - `web.de`
   - A custom-domain inbox
5. Bonus: send to `*@mail-tester.com` and pull the 10/10 score URL.

## What needs whom

| Step | Who | Effort | Blocked by |
|---|---|---|---|
| Decide subdomain | boss + rj (decision) | 5 min | Reading this blueprint |
| Add `lai.blockland.ae` as sending domain in Brevo | whoever has Brevo console login | 5 min | Brevo login |
| Get the exact DKIM CNAME targets from Brevo console | same person, same step | 0 min | (same as above) |
| Add 3-4 DNS records on `blockland.ae` host | whoever runs `blockland.ae` DNS (IT?) | 5 min | DNS host credentials |
| Wait for DNS propagation + Brevo verification | nobody (just wait) | 1-2 h | DNS planted |
| Update `LAI/.env.auth` + restart serve_rag | rj | 5 min | Brevo verification passes |
| Run test harness, manually check 5 inboxes | harsh + rj | ~1 h | env updated |
| Document mail-tester.com score + per-provider result | rj | 10 min | tests done |

## What's IN this PR vs deferred

**This PR ships (engineering side):**
- This blueprint.
- `scripts/ops/email_deliverability_test.py` — the test harness.
- Brevo + DNS setup section appended to `scripts/ops/README.md`.

**This PR does NOT ship (gated on DNS access):**
- The actual DNS records.
- The Brevo console domain registration.
- The `.env.auth` flip — staying on the gmail sender keeps existing
  dev/CI flows working until the subdomain is verified.
- The 5-inbox test results.

PROGRESS_V2 4.5.4 stays at 🔄 partial after this commit: engineering
is ready; closure is gated on the decision-and-DNS half of the work.

## Rollback

If the chosen subdomain turns out wrong (e.g., we get a real
`lai.de` domain later), the rollback is symmetric:

1. Remove the 3-4 DNS records on `blockland.ae` (or leave them — they
   don't conflict).
2. Update Brevo sender identity to the new domain.
3. Re-plant equivalent records under the new domain.
4. Update `LAI_EMAIL_SENDER_EMAIL` + `LAI_EMAIL_PUBLIC_APP_BASE_URL`.

Cost: same as the initial install.

## Honest gaps after this lands

1. **No bounce / complaint handling.** Brevo records bounces in its
   own dashboard, but we don't sync them back to the LAI DB. A
   permanently-bouncing user account keeps getting reset / invite
   mails sent indefinitely. Mitigation: scrape Brevo's webhook into
   `audit_log` (or a new `email_events` table). Deferred.
2. **No throttling.** Brevo's free tier is 300 sends/day; the paid
   plans cap at higher rates. We don't track sends/day; a noisy
   org with 100 invites per hour could exhaust quota silently.
   Brevo's dashboard surfaces this — for now, monitor manually.
3. **No DMARC report monitoring.** The `rua=` reports go to
   `postmaster@blockland.ae` but nobody is parsing them. The right
   tool is dmarcian.com or postmark's free DMARC monitoring;
   deferred until we have a pilot firm sending real traffic.
4. **English-only templates.** All 4 templates are English. Pilot
   firms will want German. Deferred to post-pilot.

## Related

- [`feedback_blueprint_docs.md`](../memory-not-applicable) — blueprint convention
- [`harsh/PROGRESS_V2.md`](../harsh/PROGRESS_V2.md) row 4.5.4 — work item this closes
- `LAI/src/lai/api/email.py` — the send code (unchanged by this work)
- `LAI/.env.auth` — current config (env flip waits for DNS verification)
- `LAI/scripts/ops/README.md` — runbook target (append)
- `https://www.brevo.com/docs/dns-records/` — Brevo's canonical DNS doc
- `https://www.mail-tester.com` — 10/10 inbox-quality scoring service
