# LAI V1 — Authentication, Tenant Isolation & Email Plan

**Date:** 2026-05-16 — _trimmed to v1-only scope 2026-05-16 by hc_
**Branch:** `v2-restructure`
**Owner:** Sumit (implementation), hc (scope trim)
**Source basis:** [`IMPLEMENTATION_GUIDE.md`](IMPLEMENTATION_GUIDE.md) §4.3
(Move 3: Auth + tenant isolation) and §9.4 (S1–S6); [`PROGRESS.md`](PROGRESS.md)
Phase-0 priorities; direct code probe (2026-05-16) of
[`LAI-UI/src/react-app/contexts/AuthContext.tsx`](../LAI-UI/src/react-app/contexts/AuthContext.tsx),
[`LAI-UI/src/react-app/utils/jwt.ts`](../LAI-UI/src/react-app/utils/jwt.ts),
[`LAI/micro-services/ddiq_report.py`](../LAI/micro-services/ddiq_report.py),
[`LAI/micro-services/api.py`](../LAI/micro-services/api.py),
[`LAI/src/lai/api/serve_rag.py`](../LAI/src/lai/api/serve_rag.py),
[`LAI/pyproject.toml`](../LAI/pyproject.toml).

This document is the single source of truth for the v1 auth work. Read
**before** any code is written.

> **Scope discipline.** This is the *minimum viable* auth that delivers
> the privacy guarantee — nothing more. Refresh-token theft chains,
> per-request audit middleware, email verification, lockout policies,
> per-org tenancy, OIDC future-compat, and three of the five email
> flows are all real engineering work but **deferred to v2**. They are
> listed in [§12 Deferred to v2](#12-deferred-to-v2) so they aren't
> forgotten — but they don't belong in this implementation cycle.

---

## Table of contents

0. The honest truth about where we are
1. What "full privacy" actually requires (v1)
2. Brevo — what it does in our system (one flow only)
3. Data model
4. Backend service surface
5. Frontend AuthContext rewrite
6. Tenant isolation — every table, every query
7. The one email flow we ship in v1
8. Secrets & deployment
9. Implementation order
10. Test plan
11. Open decisions
12. Deferred to v2

---

# 0. The honest truth about where we are

So we don't fool ourselves about the starting line:

| Thing | Reality |
|---|---|
| **Frontend "auth"** | [`AuthContext.tsx:55–82`](../LAI-UI/src/react-app/contexts/AuthContext.tsx#L55-L82) is a **demo stub**. `login()` accepts any email/password, generates a base64-encoded blob client-side ([`utils/jwt.ts:26–36`](../LAI-UI/src/react-app/utils/jwt.ts#L26-L36)), stores it in `localStorage`, never calls a backend. |
| **Token format** | `tokens_<base64(JSON)>`. **Not signed.** Anyone can mint one in DevTools. |
| **`VITE_JWT_SECRET`** | Declared in [`LAI-UI/.env.example`](../LAI-UI/.env.example), referenced nowhere in `src/`. (Putting a JWT secret in a `VITE_*` env var would bundle it into the client JS — a category error.) |
| **`/login`, `/signup` pages** | Real-looking UI fully wired to the fake `AuthContext`. Submit "doctor@example.com" + any password → you're "logged in". |
| **Backend auth on `serve_rag` (:18000)** | **None.** No `Depends(get_current_user)`, no JWT validation, no per-user filter. Every chat session is keyed by a client-supplied `session_id` (UUID) — anyone who guesses or steals a UUID gets the full conversation. |
| **Backend auth on `lai-backend` / DDiQ (:18001)** | **None.** Tables in [`ddiq_report.py:117–183`](../LAI/micro-services/ddiq_report.py#L117-L183) have no `user_id` column — `ddiq_documents` has only `session_id TEXT`, the others have nothing. Every report is globally visible to anyone who hits `/ddiq/report/{id}`. |
| **`api.py` (microservice chat)** | In-memory dicts (`document_store`, `conversation_store`) keyed by `session_id`. Lost on restart. No user binding. |
| **The `auth/` package the guide mentioned** | Already deleted (part of the ~3,200 LOC dead stack, commit `8431797`). We write the new module fresh against `passlib[bcrypt]` + `python-jose` (still declared in [`pyproject.toml:39–40`](../LAI/pyproject.toml)). |
| **CORS** | `serve_rag.py:1053` literally `allow_origins=["*"]`; `api.py:43` env-driven (`_cors_origins`) but defaults are loose. Any origin can hit the API. |
| **HF token** | Still live and committed to `Docker/inference_engine/.env:11` per the audit; rotate as part of this work. |
| **Brevo** | **NOT in the codebase.** Zero references. We are *designing* the integration, not migrating. |
| **Postgres + Redis** | Both running and healthy on `lai_network`. We have everything we need to host sessions, refresh tokens, and reset tokens without adding infrastructure. |

**Translation:** today LAI has the *appearance* of authentication and
**zero substance**. From a GDPR / BRAO perspective the system is
single-tenant demoware — you cannot legally onboard customer #2 in its
current state.

This is why §4.3 of the implementation guide calls auth a **Day-0
priority**. It's the gate for everything commercial.

---

# 1. What "full privacy" actually requires (v1)

Five concrete, testable guarantees. The original plan had eight; the
three v2 ones (theft-detection blast-radius, immutable audit trail,
unrecoverable-at-rest credentials beyond bcrypt) are listed in §12.

| # | Guarantee | Mechanism |
|---|---|---|
| **G1** | **A user can only enumerate or read their own chats, documents, reports, and matters.** | Every `SELECT` on tenant-data tables filters by `user_id = current_user.id`. Every `GET /resource/:id` 404s (not 403 — don't leak existence) when the row's `user_id` doesn't match. |
| **G2** | **A user cannot mutate (write/delete) anyone else's data.** | Same filter, applied to `UPDATE` / `DELETE`. Row is loaded *first* with the user filter, then mutated — never `UPDATE … WHERE id = :id` without the user clause. |
| **G3** | **A user cannot upload a document into another user's namespace.** | The `user_id` on `INSERT` is taken from the JWT, never from the request body. Same for the per-document storage path. |
| **G4** | **A user cannot retrieve another user's session-scoped artifacts.** | All in-memory `session_id`-keyed dicts in `api.py` and `serve_rag.py` are replaced with DB-backed rows that carry `user_id`. Session ID alone is no longer a capability — it must combine with the JWT. |
| **G5** | **Credentials at rest are not recoverable.** | `bcrypt` with work factor 12. No plaintext. Reset = invalidate + re-issue, never "send me my password". |

Deliver these five and the on-prem demo passes a credible
privacy-and-tenant-isolation audit. The G6/G7/G8 items in §12 raise
the bar further but aren't required to land customer #2.

---

# 2. Brevo — what it does in our system (one flow only)

**v1 uses Brevo for exactly one thing: password reset.** No email
verification, no welcome, no account-change notification, no
new-device alert — those are §12 work.

## 2.1 What Brevo is (short version)

EU-headquartered (Paris) transactional email provider, GDPR-compliant
by default, 300 free emails/day. Same trade-off the EU legal-tech
space makes — small privacy footprint (recipient address + name +
one-time token, never legal documents, never chat content). The
alternative (self-hosted Postfix) is a deliverability rabbit hole
that adds zero value for transactional-only volume.

⚠ **Decision needed — Q6 in §11.** If you want zero third parties,
say so now and the implementation pivots to self-hosted SMTP.

## 2.2 How it's wired

```
                  ┌───────────────────┐
   POST /auth/    │  serve_rag :18000 │
   forgot-passwd  │  auth_router      │
                  └────────┬──────────┘
                           │ background task
                           ▼
                  ┌───────────────────┐
                  │ send_reset_email()│   ~40 LOC: one httpx.post
                  │ (lai.api.email)   │   + one tenacity retry
                  └────────┬──────────┘
                           ▼
                    Brevo SaaS  ──▶  recipient inbox
```

Three rules:

1. **The Brevo API key lives server-side only.** Never in `VITE_*`
   env vars, never in the React bundle.
2. **Email is sent in a FastAPI `BackgroundTask`** — the HTTP
   response to `/auth/forgot-password` does not block on Brevo's RTT.
3. **The reset email template is one Jinja2 string in the repo** —
   version-controlled, no Brevo console login needed for wording
   fixes.

## 2.3 What we set up at Brevo (one-time, ~30 min)

1. Create the Brevo account.
2. Add and DNS-verify the sending domain (SPF, DKIM, optional DMARC).
3. Create a sender identity (`no-reply@<domain>` + display name "LAI").
4. Generate one API key, scoped to "Transactional Email Send only".
   Store as `BREVO_API_KEY` in the secret store.

## 2.4 What v1 does NOT have

No `lai.common.email` full-package mirror (config + metrics + templates
dir + 100% coverage). For one flow in one place, a single ~40-line
function `send_reset_email()` in `lai.api.email` with an httpx call
and one tenacity retry is the right shape. **Promote to a full package
when a second email flow (verify-email / account-change) lands in v2.**

---

# 3. Data model

What gets added to Postgres. All new tables live in
`lai_postgres_main` alongside the existing `ddiq_*` tables.

## 3.1 New tables (4 — was 5 in original plan)

```sql
-- ── identity ─────────────────────────────────────────────────────────────
CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL,
    email_canonical     TEXT NOT NULL UNIQUE,    -- lower(trim(email))
    password_hash       TEXT NOT NULL,           -- bcrypt $2b$12$…
    full_name           TEXT NOT NULL,
    company             TEXT,
    role                TEXT NOT NULL DEFAULT 'user',  -- 'user' | 'admin'
    status              TEXT NOT NULL DEFAULT 'active', -- 'active' | 'disabled'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at       TIMESTAMPTZ
);
CREATE INDEX users_email_canonical_idx ON users(email_canonical);
-- v2: email_verified_at, failed_login_count, locked_until (deferred §12)

-- ── refresh tokens (simple revocation; no rotation chain for v1) ─────────
CREATE TABLE refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,    -- sha256(token); never store the raw
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ
);
CREATE INDEX refresh_tokens_user_idx ON refresh_tokens(user_id, revoked_at)
    WHERE revoked_at IS NULL;
-- v2: rotated_to, user_agent, ip_address for theft-detection (deferred §12)

-- ── one-shot password reset tokens ───────────────────────────────────────
CREATE TABLE password_reset_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── conversations / messages (replaces in-memory session_id dicts) ───────
CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT,
    pinned_facts    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- existing session-meta
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX conversations_user_idx ON conversations(user_id, updated_at DESC);

CREATE TABLE messages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,    -- 'user' | 'assistant' | 'system'
    content          TEXT NOT NULL,
    citations        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX messages_conv_idx ON messages(conversation_id, created_at);
```

**Dropped from v1** (now in §12):
- `email_verification_tokens` — verification deferred
- `audit_events` — structured logs (`structlog`) cover v1; build a
  real audit table when a customer asks
- `failed_login_count` / `locked_until` columns on `users` — global
  rate limit comes later if brute force is observed
- `rotated_to` / `user_agent` / `ip_address` on `refresh_tokens` —
  theft-detection chain is v2

## 3.2 Migrations on existing tables

The existing 5 documents / 3 reports in DDiQ are demo data — assign
them to a designated `legacy@lai.local` user account (preserves
smoke-test reproducibility) per Q9 default.

```sql
-- Add nullable user_id, backfill, lock down.
ALTER TABLE ddiq_documents          ADD COLUMN user_id UUID;
ALTER TABLE ddiq_reports            ADD COLUMN user_id UUID;
ALTER TABLE ddiq_project_areas      ADD COLUMN user_id UUID;
ALTER TABLE ddiq_contracts          ADD COLUMN user_id UUID;
ALTER TABLE ddiq_classified_parcels ADD COLUMN user_id UUID;
-- ddiq_doc_chunks and ddiq_contract_parcels reach user via FK join
-- (doc_id → ddiq_documents.user_id; contract_id → ddiq_contracts.user_id).
-- ddiq_geocode_cache and ddiq_parcel_cache are deliberately shared
-- (no PII; cost-saving caches). Left alone.

-- Backfill (legacy user must exist first; see §9 step 2):
UPDATE ddiq_documents          SET user_id = '<legacy-user-uuid>' WHERE user_id IS NULL;
-- ...same for the other four

ALTER TABLE ddiq_documents          ALTER COLUMN user_id SET NOT NULL;
-- ...etc

CREATE INDEX ddiq_documents_user_idx          ON ddiq_documents(user_id);
CREATE INDEX ddiq_reports_user_idx            ON ddiq_reports(user_id);
-- ...etc

ALTER TABLE ddiq_documents ADD CONSTRAINT fk_doc_user FOREIGN KEY (user_id) REFERENCES users(id);
-- ...etc
```

---

# 4. Backend service surface

## 4.1 Where the code lives

```
LAI/src/lai/common/auth/        # shared across serve_rag + DDiQ
├── __init__.py
├── config.py                   # AuthConfig (pydantic-settings)
├── exceptions.py               # AuthError, InvalidCredentialsError, …
├── hashing.py                  # passlib bcrypt wrapper
├── tokens.py                   # access + refresh JWT issue/verify
└── dependencies.py             # FastAPI Depends(get_current_user)

LAI/src/lai/api/
├── auth_router.py              # the 7 endpoints below
└── email.py                    # one function: send_reset_email() — ~40 LOC
```

The auth_router lives in one place and is mounted from `serve_rag.py`.
DDiQ doesn't need its own login endpoint — it imports
`get_current_user` from `lai.common.auth.dependencies` and validates
JWTs that serve_rag issued. Same hashing, same token validator
everywhere.

## 4.2 Endpoints — 7 in v1

| Method | Path | Body | Returns | Notes |
|---|---|---|---|---|
| POST | `/auth/signup` | `{ email, password, full_name, company? }` | `200 { access_token, expires_in }` + refresh cookie | Creates `users` row, logs in immediately. (No verify step in v1 — see Q2 default.) |
| POST | `/auth/login` | `{ email, password }` | `200 { access_token, expires_in }` + refresh cookie | bcrypt verify; 401 on miss. |
| POST | `/auth/refresh` | — (cookie) | `200 { access_token, expires_in }` + same cookie | Validates refresh cookie; issues new access token. (No rotation chain in v1 — §12.) |
| POST | `/auth/logout` | — | `204` | Revokes the current refresh row; clears cookie. |
| GET  | `/auth/me` | — | `200 { id, email, full_name, company, role }` | Authenticated. Frontend hydrates `AuthContext` from this. |
| POST | `/auth/forgot-password` | `{ email }` | `204 No Content` | Always 204 (no email enumeration). Issues a single-use token; queues the Brevo reset email. |
| POST | `/auth/reset-password` | `{ token, new_password }` | `204` | Consumes token; updates `password_hash`; revokes all refresh rows for the user. |

**Dropped from v1** (in §12): `/auth/verify-email`, `/auth/logout-all`,
`/auth/change-password` (the last comes back via reset-password — user
forgets password, clicks reset, picks new one).

## 4.3 Token model

| | Access token | Refresh token |
|---|---|---|
| Format | JWT (HS256), claims: `sub` (user_id), `exp`, `iat`, `email`, `role` | Opaque 256-bit random string |
| Lifetime | 15 minutes | 30 days (90 days if "Keep me signed in") |
| Storage on client | In-memory in JS (React state); never persisted | http-only, secure, SameSite=Lax cookie |
| Storage on server | Stateless (signature verified per request) | `refresh_tokens` row, sha256-hashed |
| Transmitted via | `Authorization: Bearer …` header | Cookie, automatic |
| Revocable | No (short TTL is the revocation) | Yes (`revoked_at`) |

**Why this shape:** access token in memory means XSS can read it but
it dies in 15 min; refresh token in http-only cookie means XSS
*cannot* read it at all. This is the modern best practice.

**Cookie attributes pin:** `SameSite=Lax` works if UI and API share
an eTLD+1 (e.g. both at `lai.de`). If the on-prem deployment puts
them cross-origin (`app.lai.de` calling `api.lai.de`), refresh cookie
must be `SameSite=None; Secure` + the CORS preflight must allow
credentials. **Decide the deployment shape before writing the cookie
code — Q11 in §11.**

## 4.4 The single tenant-isolation enforcement point

Every protected route depends on this:

```python
# lai/common/auth/dependencies.py

bearer = HTTPBearer(auto_error=False)

async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(401, "missing credentials")
    try:
        payload = decode_access_token(creds.credentials)  # pinned algorithms=["HS256"]
    except InvalidTokenError as e:
        raise HTTPException(401, str(e))
    return CurrentUser(id=payload["sub"], email=payload["email"], role=payload["role"])
```

Every protected endpoint becomes:

```python
@router.get("/ddiq/documents")
async def list_documents(user: CurrentUser = Depends(get_current_user)):
    # The user_id filter MUST be part of the SQL. Never client-supplied.
    return await db.fetch_all(
        "SELECT id, filename, status FROM ddiq_documents WHERE user_id = $1",
        user.id,
    )
```

No audit middleware in v1 — `structlog` already wraps every request
with structured fields; that's enough until a customer asks for an
auditor-grade trail.

---

# 5. Frontend AuthContext rewrite

## 5.1 Goals

- Delete every line of fake JWT logic in
  [`utils/jwt.ts`](../LAI-UI/src/react-app/utils/jwt.ts).
- Replace the demo `login()` / `signup()` in
  [`AuthContext.tsx`](../LAI-UI/src/react-app/contexts/AuthContext.tsx)
  with real backend calls.
- One place where the access token lives. All API clients route
  through one fetch wrapper.

## 5.2 New shape

```
src/react-app/auth/
├── AuthContext.tsx     # { user, status: 'loading'|'authed'|'anon' }
├── AuthProvider.tsx    # owns access token (in React state, not localStorage)
├── useAuth.ts          # consumer hook
└── apiFetch.ts         # the SINGLE wrapper — Authorization header + refresh-on-401
```

Existing API clients (chat, ddiq) import `apiFetch` from
`src/react-app/auth/apiFetch.ts` instead of calling `fetch` directly.

## 5.3 The fetch wrapper

Plain refresh-then-retry. No single-flight dedup — the race window is
benign (extra refresh request, not a correctness bug) and the
complexity isn't worth it for v1.

```ts
// src/react-app/auth/apiFetch.ts

let accessToken: string | null = null;
export function setAccessToken(t: string | null) { accessToken = t; }

export async function apiFetch(input: RequestInfo, init: RequestInit = {}) {
  const headers = new Headers(init.headers ?? {});
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  let res = await fetch(input, { ...init, headers, credentials: "include" });
  if (res.status !== 401) return res;

  // Try one refresh + retry.
  const r = await fetch("/auth/refresh", { method: "POST", credentials: "include" });
  if (!r.ok) {
    setAccessToken(null);
    window.location.assign("/login");
    return res;
  }
  const { access_token } = await r.json();
  setAccessToken(access_token);
  headers.set("Authorization", `Bearer ${access_token}`);
  return fetch(input, { ...init, headers, credentials: "include" });
}
```

**Where the access token lives:** module-scope memory inside
`apiFetch.ts`. Never `localStorage`, never a cookie the JS can read.

**On page reload:** `AuthProvider` mounts, calls `apiFetch('/auth/me')`.
That fires without an access token, 401, triggers `/auth/refresh`
(which uses the http-only cookie automatically), gets a new token,
retries — `/auth/me` returns the profile. User stays logged in across
reloads with no client-side token persistence.

## 5.4 What changes in the existing pages

| File | Change |
|---|---|
| [`AuthContext.tsx`](../LAI-UI/src/react-app/contexts/AuthContext.tsx) | Rewrite to call real backend. Move into `auth/`. |
| [`utils/jwt.ts`](../LAI-UI/src/react-app/utils/jwt.ts) | **Delete.** Entire file. |
| [`Login.tsx`](../LAI-UI/src/react-app/pages/Login.tsx) | Unchanged UI. `login()` hits real backend. |
| [`Signup.tsx`](../LAI-UI/src/react-app/pages/Signup.tsx) | Unchanged UI. `signup()` returns access token directly (no "check your inbox" step in v1). |
| **(new)** `ForgotPassword.tsx` | Email-only form → `POST /auth/forgot-password`. |
| **(new)** `ResetPassword.tsx` | Reads `?token=…` from URL; new-password form. |
| [`ProtectedRoute.tsx`](../LAI-UI/src/react-app/components/ProtectedRoute.tsx) | Add a loading state while `AuthProvider` hydrates (`status === 'loading'`); without this you get a flash of `/login` on every reload. |
| [`App.tsx`](../LAI-UI/src/react-app/App.tsx) | Add routes for `/forgot-password`, `/reset-password`. |
| [`LAI-UI/.env.example`](../LAI-UI/.env.example) | Delete `VITE_JWT_SECRET`. Replace with `VITE_API_BASE_URL` only. |

**Dropped from v1:** `VerifyEmail.tsx` page (no verification in v1).

---

# 6. Tenant isolation — every table, every query

This is the mechanical part of "no user sees another user's data".
One table at a time, one filter at a time.

| Table | How `user_id` enters | Where the filter applies |
|---|---|---|
| `users` | row IS the user | Only the user themselves and `role='admin'` |
| `refresh_tokens` | set on issue | Server-only; never user-readable via API |
| `password_reset_tokens` | set on issue | Same |
| `conversations` | from JWT on `POST /conversations` | Every list / get / append / delete |
| `messages` | via `conversation_id → conversations.user_id` | Join filter on read |
| `ddiq_documents` | from JWT on upload | Every list / get / delete / chunk-query |
| `ddiq_doc_chunks` | via `doc_id → ddiq_documents.user_id` | Join filter on retrieval |
| `ddiq_reports` | from JWT on generate | Every list / get / delete |
| `ddiq_project_areas` | from JWT | Same |
| `ddiq_contracts` | from JWT | Same |
| `ddiq_contract_parcels` | via `contract_id → ddiq_contracts.user_id` | Join filter |
| `ddiq_classified_parcels` | from JWT | Same |
| `ddiq_geocode_cache` | **shared (no PII)** | No filter — geocoding `"Berlin"` returns the same coords regardless of who asks. |
| `ddiq_parcel_cache` | **shared (no PII)** | Same. Cadastral parcel polygons are public data. |
| `pipeline_local.db` / corpus | **shared, read-only** | Legal corpus is the same for everyone. |

**Patterns we enforce in code review:**

1. **Never** `WHERE id = :id` on a tenant table without an `AND user_id = :uid`.
2. **Never** trust a `user_id` value from the request body — always
   `current_user.id` from the JWT.
3. **Always** load-then-mutate: select with the filter, mutate by
   primary key, so the filter can't be sidestepped.
4. **Always** 404 (not 403) on cross-tenant access — don't leak existence.

The grep-based SQL-safety test the original plan proposed is brittle
(misses query builders, f-strings, `WHERE id IN (…)`, parameter
styles). **The real safety net is the §10.3 integration battery** —
Alice/Bob, every resource, list/get/put/delete, expect 404.

## 6.1 The in-flight `session_id` artifacts

Today: in-memory dicts in `api.py:121,128` keyed by `session_id`.
After step 4 of §9:

- Dicts go away.
- Uploads → rows in `ddiq_documents` with `user_id`.
- Conversations → rows in `conversations` with `user_id`.
- The frontend stops minting `session_id` UUIDs; it calls
  `POST /conversations` to get a server-assigned id and uses the
  returned `conversation_id` on every subsequent call. That id is no
  longer a capability on its own — the JWT is required too.

---

# 7. The one email flow we ship in v1

## E2 — Password reset (only)

```
POST /auth/forgot-password { email }
  if user exists:
    issue password_reset_tokens row (30 min TTL)
    BackgroundTask → send_reset_email(email, link)
  always return 204 (no enumeration)

POST /auth/reset-password { token, new_password }
  consume token; bcrypt-hash new password;
  revoke all refresh_tokens for the user (force re-login on every device).
  return 204
```

Brevo template (a single Jinja2 string in `lai.api.email`):

```
Subject: Reset your LAI password

Hello {{ full_name }},

We received a request to reset your LAI password. Click the link below
to choose a new one. The link is valid for 30 minutes.

{{ reset_url }}

If you didn't request this, you can safely ignore this email.

— LAI
```

`reset_url` = `{LAI_EMAIL_PUBLIC_APP_BASE_URL}/reset-password?token={raw_token}`.

**E1/E3/E4/E5 are §12.** Add them when a customer asks for them or
when self-signup volume makes admin-issued accounts unsustainable.

---

# 8. Secrets & deployment

## 8.1 v1 secret inventory

| Name | Where used | Where stored | Rotation cadence |
|---|---|---|---|
| `JWT_ACCESS_SECRET` | sign/verify access tokens | `/etc/lai/secrets/auth.env`, `chmod 600` | every 90 days |
| `BREVO_API_KEY` | `send_reset_email()` | same | every 180 days |
| `DB_PASSWORD` | already exists; per S6 of guide, make non-defaultable | secret store | already on cleanup list |
| `HF_TOKEN` | already exists; rotate as part of this work | secret store | one-time rotation |

DKIM is a DNS record, not a runtime secret — set once during the
domain-verification step in §2.3.

## 8.2 Config objects

```python
# lai/common/auth/config.py
class AuthConfig(BaseSettings):
    jwt_access_secret: SecretStr
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 30
    jwt_refresh_ttl_days_remember_me: int = 90
    bcrypt_rounds: int = 12
    model_config = SettingsConfigDict(env_prefix="LAI_AUTH_")

# lai/api/email.py (top of file)
class EmailConfig(BaseSettings):
    brevo_api_key: SecretStr
    sender_email: EmailStr = "no-reply@<domain>"
    sender_name: str = "LAI"
    public_app_base_url: HttpUrl              # used to build reset links
    enabled: bool = True                      # False → log instead of send (test envs)
    model_config = SettingsConfigDict(env_prefix="LAI_EMAIL_")
```

Same `pydantic-settings` shape as `LlmConfig` / `RerankerConfig`. CI
gate on `lai.common.auth.*` matches the same strict mypy/ruff/coverage
discipline already in place.

## 8.3 Frontend env

```
# LAI-UI/.env.example  (new)
VITE_API_BASE_URL=http://localhost:18000
```

That's it. `VITE_JWT_SECRET` goes away forever.

## 8.4 CORS

Driven by env (`LAI_CORS_ALLOWED_ORIGINS=comma,separated,list`). Drop
the literal `allow_origins=["*"]` in `serve_rag.py:1053`. Default to
the on-prem UI host only.

---

# 9. Implementation order (7 steps — was 12)

Each step independently shippable. Steps 1–4 deliver the privacy
guarantee. Steps 5–7 are the rollout polish.

| # | Step | Touches | Done when |
|---|---|---|---|
| **1** | `lai.common.auth` package (config + hashing + tokens + `get_current_user`) | new package | bcrypt round-trip + JWT issue/decode + lifetime edge-case tests; CI green |
| **2** | DB migration: create `users`, `refresh_tokens`, `password_reset_tokens`, `conversations`, `messages`. Add nullable `user_id` to DDiQ tables. Seed the `legacy@lai.local` user. Backfill DDiQ rows; lock `user_id` NOT NULL + add FK constraints. | new migration | `make db.migrate.up` and `down` both succeed; existing demo data still reachable via legacy user |
| **3** | `auth_router.py` — 7 endpoints from §4.2. Wire E2 email via `BackgroundTask` → `send_reset_email()`. | new files | Happy + error path tests on each endpoint; cookie attributes correct |
| **4** | Add `Depends(get_current_user)` to **every existing** endpoint in `serve_rag.py`, `ddiq_report.py`, `api.py`. Add `WHERE user_id = …` to every SELECT/UPDATE/DELETE. Replace `session_id`-keyed dicts in `api.py` with DB-backed `conversations`/`messages`. | several | The §10.3 tenant-isolation battery passes |
| **5** | Frontend: rewrite `AuthContext`, delete `utils/jwt.ts`, write `apiFetch.ts`. Wire login/signup pages to real backend. Add `ForgotPassword` + `ResetPassword` pages and routes. | `LAI-UI/src/react-app/auth/` | Full signup → login → forgot → reset → login round trip works in the browser; reload-after-login works |
| **6** | CORS allow-list driven by env (`LAI_CORS_ALLOWED_ORIGINS`). Drop `allow_origins=["*"]` in `serve_rag.py:1053`. | both backends | Only the on-prem UI host reaches the API |
| **7** | Secret hardening: rotate HF token, move all secrets to `/etc/lai/secrets/` directory `chmod 600` owned by service user; remove from repo. | env + ops | `git grep` finds no live secret |

Realistic estimate: **5–7 days for one engineer** (was the original
"~2 weeks" estimate which included the §12 deferred work).

The high-leverage milestone is **after step 4** — at that point the
GDPR blocker is gone and customer #2 can legally exist on the system.
Steps 5–7 are polish + ops; the system is *correct* at the end of 4.

---

# 10. Test plan

## 10.1 Unit (per package, gated in CI)

- `hashing.py`: bcrypt round-trip; rejects malformed hashes.
- `tokens.py`: round-trip; tampered token rejected; expired token
  rejected; wrong-secret rejected. Algorithm pinned to `HS256` in
  code (no test needed if it's a constant).
- `dependencies.py`: missing header → 401; malformed → 401; expired
  → 401; valid → returns the right `CurrentUser`.
- `send_reset_email()`: respects `enabled=False`, retries on 5xx
  once, never logs the API key.

## 10.2 Integration (live, opt-in like the existing 12)

- Sign up → login → call `/auth/me` → matches signup data.
- Forgot-password round trip end-to-end against the Brevo sandbox.

## 10.3 Tenant isolation — the explicit test battery

Two users (`alice@`, `bob@`). For each tenant resource type
(conversations, documents, reports, project_areas, contracts,
classified_parcels):

1. Alice creates resource X. Confirm her GET returns X.
2. Bob's GET on X's id returns **404** (not 403, not 200).
3. Bob's PUT/PATCH on X returns 404; X is unchanged.
4. Bob's DELETE on X returns 404; X still exists for Alice.
5. Bob's list endpoint never includes X.
6. After Alice deletes X, her GET returns 404; the row is gone.

Run for every endpoint family. This is the single most important
test file — `tests/integration/test_tenant_isolation.py`. **This is
the v1 privacy gate.**

## 10.4 Frontend (Vitest)

- `apiFetch` retries once on 401, refreshes, replays.
- Refresh failure clears state and redirects to `/login`.
- `AuthContext` shows loading on mount, then hydrates from `/auth/me`.

## 10.5 "Demo destroyer" manual checklist

Before declaring done, by hand:

- [ ] Two browsers (Chrome + Firefox). Sign up as `alice@`, `bob@`.
      Upload a document as Alice. In Bob's window, try every URL:
      `/dashboard/documents`, `/api/ddiq/documents`,
      `/api/ddiq/documents/<alice's doc id>`. Each must be empty or 404.
- [ ] In Chrome DevTools, copy Alice's access token. Paste into Bob's
      session storage. Confirm: it works for **Alice's** data only
      (because the JWT belongs to Alice). This is correct.
- [ ] In Firefox, clear cookies. Reload. You should be at `/login`.
- [ ] Kill the backend, reload. UI should show a useful error, not a
      blank dashboard.

---

# 11. Open decisions (4 of 10 still need answers)

The other six are taken as defaults — flagged explicitly so they
can't be silently re-litigated.

| # | Decision | Default | Status |
|---|---|---|---|
| **Q1** | Token storage shape | access-in-memory + refresh-cookie (§4.3) | **defaulted** |
| **Q2** | Email verification | not in v1 (deferred to §12) | **defaulted** |
| **Q3** | Account creation: self-signup or admin-issued? | **needs answer** — changes the signup-page UI | ⚠ open |
| **Q4** | Multi-tenancy model | per-user for v1; org table is §12 | **defaulted** |
| **Q5** | OIDC future-compat | password-only v1; don't preclude OIDC later | **defaulted** |
| **Q6** | Email provider | Brevo | **defaulted unless you want self-hosted SMTP** |
| **Q7** | Sending domain (for SPF/DKIM) | **needs answer** — DNS blocker for E2 delivery | ⚠ open |
| **Q8** | "Keep me signed in" semantics | 30 d default, 90 d if checked | **defaulted** |
| **Q9** | Demo data backfill | assign to `legacy@lai.local` | **defaulted** |
| **Q11 (new)** | Deployment topology — same eTLD+1 or cross-origin? | **needs answer** — controls `SameSite=Lax` vs `None;Secure` on the refresh cookie | ⚠ open |

**Sumit can start step 1 of §9 without any answers** (the
`lai.common.auth` package is policy-agnostic). The three open
questions block by step 3 (Q3 affects the signup endpoint shape, Q11
affects cookie attributes) and step 4-end (Q7 must be resolved before
the first reset email goes out).

---

# 12. Deferred to v2

Real engineering work, intentionally postponed so v1 ships. Each
item names what would unblock its return.

| Item | What it adds | When to revisit |
|---|---|---|
| **Email verification flow (E1)** + `email_verification_tokens` table + `email_verified_at` column on `users` + `/auth/verify-email` endpoint + `VerifyEmail.tsx` page + "Check your inbox" UX | Required-verify-before-login for B2B legal trust. | When self-signup is enabled in production *and* the firm wants to gate access by email-domain match before payment. |
| **Account-change emails (E4)** to old + new address on email/password change | Notify the real account owner if a credential is changed under them. | When MFA enrolment lands, or when the first "my account was taken over" support ticket appears. |
| **New-device login alert (E3)** + device fingerprint | Stolen-token detection from the user side. | When audit_events lands; same login-path code. |
| **Welcome email (E5)** | UX polish. | Whenever; low priority. |
| **Refresh-token theft-detection chain** (`rotated_to`, rotation-on-every-use, reuse → revoke whole user chain) | Limits blast radius of an exfiltrated refresh token. | When the first credential-stuffing or token-theft incident shows up in logs. |
| **Audit middleware + `audit_events` table** with prompt_hash / response_hash / model_version / cite_ids | Auditor-grade trail of every privileged action. Already in §6 of the implementation guide. | When a customer asks for SOC-2 / GDPR Art. 30 records of processing, or before an external pen-test. |
| **Lockout policy** (`failed_login_count`, `locked_until`) + global Redis rate limit | Protect against brute force + credential stuffing. | When auth-failure rate exceeds a threshold (set a Prometheus alert). |
| **`/auth/logout-all` + `/auth/change-password` endpoints** | Session management UX; same code path, separate endpoint. | When the first user asks "how do I sign out everywhere?". |
| **Per-org / per-firm tenancy** (`organizations` table, `users.organization_id`, document sharing within org) | Real B2B feature for firms with multiple lawyers. | When the first customer has more than one user and wants shared matter access. |
| **OIDC SSO** (Microsoft Entra, Google Workspace) | Enterprise sales unlock; replaces the password path. | When a customer's IT explicitly requires SSO. |
| **`lai.common.email` full package** (config + metrics + templates dir + tenacity + 100% coverage) | Promote `lai.api.email` to a shared package when a second email flow lands. | Step 1 of E1 / E4 implementation in v2. |
| **`forgot-email` recovery channel** | UI for users who forget their account email. | When the first support ticket arrives; defer until then (op-email + manual admin lookup is fine for v1). |

---

## Pointers

- [IMPLEMENTATION_GUIDE.md §4.3](IMPLEMENTATION_GUIDE.md) — the auth move
- [IMPLEMENTATION_GUIDE.md §6](IMPLEMENTATION_GUIDE.md) — matter-centric data model (the audit_events shape — v2)
- [IMPLEMENTATION_GUIDE.md §9.4](IMPLEMENTATION_GUIDE.md) — the S1–S6 security catalog
- [IMPLEMENTATION_GUIDE.md §11.3](IMPLEMENTATION_GUIDE.md) — BRAO §43e / §43a context for the email-provider choice
- [PROGRESS.md](PROGRESS.md) — current `lai.common` shape; this plan follows it
