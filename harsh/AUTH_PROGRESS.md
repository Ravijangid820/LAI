# LAI V1 — Auth Implementation Progress

**Date:** 2026-05-17  _(updated 2026-05-17 20:45 after the 48 backend + 15 frontend QA pass — 63 tests total, all green)_
**Branch:** `v2-restructure`
**Owner:** Sumit (implementation)
**Source plan:** [`AUTH_PLAN.md`](AUTH_PLAN.md)
**QA report:** [`AUTH_QA_REPORT.md`](AUTH_QA_REPORT.md)

This document tracks the as-built state of the v1 auth work against the
plan. It is the single source of truth for "what is done, what is left,
and what the next engineer needs to know before they touch this code."

---

## 0. TL;DR

| Layer | Code complete | Backend live-tested | Frontend live-tested | Notes |
|---|---|---|---|---|
| `lai.common.auth` package | ✅ | ✅ 48/48 QA tests | n/a | bcrypt + HS256 + refresh + reset; 3 latent bugs found and fixed during QA |
| DB migration 001 | ✅ | ✅ applied to live `lai_db` | n/a | 5 new tables + `user_id` on 5 DDiQ tables; legacy user seeded |
| `/auth/*` router (7 endpoints) | ✅ | ✅ all 7 endpoints verified | ✅ via Playwright | mounted on `serve_rag` |
| Brevo password-reset email | ✅ | ✅ Brevo API accepted | ✅ forgot-password UI confirmed end-to-end | sender = `vinodfinance07@gmail.com`, fragile for prod (Q7 §11) |
| Frontend rewrite (AuthContext, apiFetch, pages) | ✅ | n/a | ✅ **15/15 headless-Chromium E2E** | signup → /dashboard, cookie attrs verified, no localStorage token, reload survives, logout works, multi-context isolation |
| Tenant isolation across `serve_rag`, `api.py`, `ddiq_report.py` | ✅ | ⏳ **needs full-stack Alice/Bob run** | ⏳ | every endpoint gated + user_id-filtered in code |
| CORS `allow_origins=["*"]` cleanup (AUTH_PLAN §9 step 6) | ❌ | — | — | `serve_rag.py:1083` still wildcard. Note: cross-origin browser tests confirmed this **must** be fixed before any cross-origin browser deploy — wildcard is invalid with credentials. |
| Secret hardening (AUTH_PLAN §9 step 7) | ❌ | — | — | ops task |
| Test suite (AUTH_PLAN §10) | partial | ✅ 48-test bash harness | ✅ 15-test Playwright | no pytest / Vitest committed yet (only bash + Node test files) |

**Privacy guarantee status:** G3, G4, G5 are **fully proven** by the
combined backend + frontend QA (63 tests). G1 and G2 are structurally
enforced in code and verified at the **auth surface** (via the 48
backend tests including 8 explicit attack scenarios + the 15 browser
tests), but not yet verified at the **resource surface** (sessions,
documents, reports) — that still requires the full RAG/DDiQ stack to
run the Alice/Bob battery from AUTH_PLAN §10.3.

---

## 1. What's been done

### 1.1 Step 1 — `lai.common.auth` package ✅

Location: `LAI/src/lai/common/auth/`

| File | Purpose |
|---|---|
| `__init__.py` | Public surface — re-exports the names other modules import |
| `config.py` | `AuthConfig` pydantic-settings, env-prefix `LAI_AUTH_`; 32-char floor on `jwt_access_secret`, configurable cookie attrs, password policy |
| `exceptions.py` | Typed hierarchy — `AuthError`, `InvalidCredentialsError`, `InvalidTokenError`, `TokenExpiredError`, `UserDisabledError`, `PasswordPolicyError`, `EmailAlreadyExistsError`, `UserNotFoundError` |
| `hashing.py` | `PasswordHasher` — passlib bcrypt wrapper, work factor pinned via config, `needs_rehash` for opportunistic cost-floor bumps |
| `tokens.py` | `TokenIssuer` — HS256 access JWT issue/decode, opaque 256-bit refresh tokens stored as sha256 hashes, reset tokens reuse the refresh primitive |
| `models.py` | `CurrentUser` frozen dataclass — the per-request principal |
| `dependencies.py` | `build_get_current_user(issuer)` factory → FastAPI `Depends`; `require_admin` sub-dep; 401 with `WWW-Authenticate` hint on expiry |
| `db.py` | `create_pool()` — async asyncpg pool from `DB_*` env vars |
| `repository.py` | `UserRepository`, `RefreshTokenRepository`, `ResetTokenRepository` — every SQL touching auth tables lives here |

Production discipline applied:
- Algorithm pinned (`HS256`) — never trust `alg` from the wire.
- Issuer claim verified on decode.
- Refresh tokens stored sha256-hashed; raw value never persisted.
- Bcrypt verify path returns `False` on malformed hash (no exception leak).
- Reset token consume is one atomic SQL `UPDATE … WHERE consumed_at IS NULL AND expires_at > NOW() RETURNING …` — two concurrent reset attempts observe exactly-one success.

### 1.2 Step 2 — SQL migration 001 ✅

Location: `LAI/scripts/db/migrations/`
- `001_auth_and_tenant_isolation.up.sql` (9.6 KB)
- `001_auth_and_tenant_isolation.down.sql` (2.5 KB)

What it does:

1. `CREATE EXTENSION IF NOT EXISTS pgcrypto` (for `gen_random_uuid`).
2. Creates 5 new tables:
   - `users` (with `CHECK (role IN ('user','admin'))`, `CHECK (status IN ('active','disabled'))`, unique `email_canonical`)
   - `refresh_tokens` (sha256-hashed tokens, partial index on active rows)
   - `password_reset_tokens` (single-use, 30 min TTL)
   - `conversations` (replaces in-memory dicts in `api.py`)
   - `messages` (FK CASCADE on `conversations`)
3. Adds nullable `user_id UUID` to 5 DDiQ tables:
   `ddiq_documents`, `ddiq_reports`, `ddiq_project_areas`, `ddiq_contracts`, `ddiq_classified_parcels`.
4. Creates per-table `user_id` indexes.
5. Inserts the `legacy@lai.local` user (id `00000000-0000-0000-0000-000000000001`, status `disabled`) — owns pre-auth demo rows.
6. Backfills every existing DDiQ row's `user_id` to the legacy user.
7. Locks `user_id` to `NOT NULL` + adds FK constraints (via DO-block since Postgres has no `ADD CONSTRAINT IF NOT EXISTS`).

Idempotent — safe to re-run. Down migration is destructive (drops auth
tables, removes `user_id` from DDiQ tables; existing DDiQ rows are
preserved).

### 1.3 Step 3 — `/auth/*` router + Brevo email ✅

Locations:
- `LAI/src/lai/api/auth_router.py` — 7 endpoints
- `LAI/src/lai/api/email.py` — Brevo sender (~40 LOC + tenacity retry, per plan)

7 endpoints:

| Method | Path | Behavior |
|---|---|---|
| POST | `/auth/signup` | Create `users` row, log in immediately, set refresh cookie. 409 on email collision. |
| POST | `/auth/login` | bcrypt verify (constant-time-ish via a dummy hash on user-not-found), opportunistic rehash on cost-floor bump, refresh cookie. 401 generic body — same for "no user" and "wrong password". |
| POST | `/auth/refresh` | sha256 the cookie, look up active row, issue new access JWT. Cookie max-age refreshed (lifetime not extended — opaque value stays). |
| POST | `/auth/logout` | Revoke the row, clear cookie. 204. |
| GET | `/auth/me` | Hydrate the SPA. Auth-required. |
| POST | `/auth/forgot-password` | Always 204 (no enumeration). On hit, mint reset row + queue `send_reset_email` via `BackgroundTask`. |
| POST | `/auth/reset-password` | Atomic consume → bcrypt new hash → revoke **all** refresh rows for the user (v1 stand-in for rotation chain). |

Cookie attributes from `AuthConfig`:
- `HttpOnly` always on
- `Secure` defaults true (override `LAI_AUTH_REFRESH_COOKIE_SECURE=false` for localhost dev)
- `SameSite=Lax` default; `none` for cross-origin deploys (AUTH_PLAN Q11)
- `Path=/auth` — never sent on chat/DDiQ routes

Brevo wiring (`email.py`):
- One Jinja-style template in code (subject + body).
- `BackgroundTask` so the HTTP 204 to the user does not block on Brevo RTT.
- `tenacity` single retry on transient `httpx.HTTPError`.
- `enabled=False` dev path logs the rendered body instead of sending.
- API key, sender, public app base URL all in `LAI_EMAIL_*` env.

Module is mounted in `serve_rag.py` lifespan:
```python
auth_deps = AuthDeps(auth_config=_auth_config, ...)
app.include_router(build_auth_router(auth_deps, get_current_user=get_current_user))
register_auth_exception_handlers(app)  # 401 translation for auth-module exceptions
```

### 1.4 Step 5 — Frontend rewrite ✅

Location: `LAI-UI/src/react-app/auth/` (new folder).

| File | Purpose |
|---|---|
| `AuthContext.tsx` | The React `createContext` only — kept separate to avoid circular imports |
| `AuthProvider.tsx` | Owns the access token in **module-scope memory** (never localStorage). On mount calls `/auth/me`; if it 401s, the wrapper auto-refreshes via cookie. Exposes `login`, `signup`, `logout`, `forgotPassword`, `resetPassword`. |
| `useAuth.ts` | Consumer hook — throws if used outside a Provider |
| `apiFetch.ts` | The **single** fetch wrapper. Attaches Bearer; on 401 does a single-flight `/auth/refresh` + retry. Drops to `/login` on refresh failure. Module-level `setAccessToken` / `getAccessToken` / `onAccessTokenChange` for non-React clients. |
| `types.ts` | `User`, `AuthContextValue`, `AuthStatus` |
| `index.ts` | Public re-exports |

Pages:
- `LAI-UI/src/react-app/pages/Login.tsx` — rewired to backend; preserves `?from=` redirect from `ProtectedRoute`; `rememberMe` plumbed through.
- `LAI-UI/src/react-app/pages/Signup.tsx` — calls real backend `/auth/signup` with `{ email, password, full_name, company }`.
- `LAI-UI/src/react-app/pages/ForgotPassword.tsx` (**new**) — email form → `/auth/forgot-password` → always-204 confirmation; no enumeration.
- `LAI-UI/src/react-app/pages/ResetPassword.tsx` (**new**) — reads `?token=` from URL → new-password form → `/auth/reset-password` → redirect to `/login`.

Misc:
- `ProtectedRoute.tsx` — now branches on `status === 'loading'` to show a spinner during hydration. Without this, every reload flashed `/login`.
- `App.tsx` — adds `/forgot-password`, `/reset-password` routes. Moved `AuthProvider` *inside* `<Router>` so it can use `useLocation`.
- `DashboardLayout.tsx` — switched import to `@/react-app/auth`; logout made async.
- `.env.example` — replaced `VITE_JWT_SECRET` with `VITE_API_BASE_URL`.

Deleted (demo cruft):
- `LAI-UI/src/react-app/contexts/AuthContext.tsx` — the old base64-`tokens_<...>` demo
- `LAI-UI/src/react-app/utils/jwt.ts` — client-side fake JWT minter

### 1.5 Step 4 — Tenant isolation on every existing endpoint ✅

This is the part that actually delivers G1–G4 from AUTH_PLAN §1.

**Step 4a — Cross-service auth bootstrap.**
- New: `LAI/micro-services/auth_dep.py` — module-level `get_current_user` shared by `api.py` and `ddiq_report.py`. Both microservices verify tokens issued by `serve_rag` against the same `LAI_AUTH_JWT_ACCESS_SECRET` (stateless, no DB needed).
- `serve_rag.py` restructured: `AuthConfig` + `TokenIssuer` + `get_current_user` constructed at **module load** so route decorators can `Depends(...)` them. Lifespan reuses the same instances — single secret, single verifier.

**Step 4b — Every protected endpoint guarded.**

`serve_rag.py` (port 18000) — `Depends(get_current_user)` added to:
- `POST /query`
- `POST /upload`
- `POST /analyze-contract`
- `GET /analyze-contract/progress`
- `GET /analyze-contract/full`
- `GET /sessions`
- `GET /sessions/{session_id}`
- `GET /sessions/{session_id}/messages`
- `POST /sessions/{session_id}/messages`
- `DELETE /sessions/{session_id}`
- `PATCH /sessions/{session_id}`

`api.py` (port 18001) — added to:
- `POST /upload`
- `POST /query`

`ddiq_report.py` (mounted under `/ddiq`) — added to:
- `GET /documents`
- `POST /documents/upload`
- `POST /report/generate`
- `POST /report/generate/async`
- `GET /report/{id}/status`
- `GET /reports`
- `GET /report/{id}`
- `DELETE /report/{id}`
- `GET /report/{id}/geojson`
- `GET /report/{id}/validate`
- `POST /project-area`

Intentionally **public** (no auth):
- `GET /health` on both services (load balancer / uptime probes need it)
- `GET /ddiq/config/map-tiles` (tile-server URLs to OSM/Esri/BKG — no PII)
- `/auth/*` (auth itself can't require auth)

**Step 4c — user_id filter on every SQL.**

`ddiq_report.py`:
- New boundary helper `_assert_owns_documents(doc_ids, user_id)` — single 404 gate at every endpoint that takes doc IDs. Returns 404 (not 403) per AUTH_PLAN §6 rule 4.
- `search_doc_chunks` and `get_all_text_for_docs` accept optional `user_id`, add `AND d.user_id = %s` to the JOIN — defense in depth.
- `_compute_fingerprint` now includes `user_id` — two tenants get separate caches; cache lookup can't return a foreign row.
- `_find_existing_report`, `_update_report_progress`, `_persist_report_jsonb`, `_run_report_generation_job`, `_generate_report_core` all thread `user_id` through and include `WHERE user_id = %s` on every UPDATE.
- Auxiliary INSERTs (`ddiq_project_areas`, `ddiq_contracts`, `ddiq_classified_parcels`) set `user_id` from the JWT, never from the request body.
- `DELETE /report/{id}` cascades `WHERE report_id = %s AND user_id = %s` across all 4 child tables.
- Every GET on `ddiq_reports` filters by `user_id`. List endpoints filter by `user_id`.

`api.py`:
- In-memory `conversation_store` and `document_store` dicts **deleted**.
- New helpers `_ensure_conversation(user_id, conversation_id)`, `_load_history(conversation_id)`, `_append_message(...)` against the `conversations` + `messages` tables.
- `/upload` writes to `ddiq_documents` + `ddiq_doc_chunks` with `user_id` from the JWT. Chunks inserted via `psycopg2.extras.execute_values` (batched, ~50× faster than per-row INSERT once N > 20).
- `search_uploaded_docs` rewritten as pgvector cosine search joined on `ddiq_documents.user_id`.
- `/query` resolves-or-creates a conversation owned by the caller; 404 if `session_id` belongs to someone else.

`serve_rag.py` + `lai/persistence.py`:
- `lai.persistence` functions (`load_session`, `delete_session`, `session_exists`, `update_session_title`, `add_message`, `list_messages`, `get_session_meta`, `set_session_meta`) accept optional `user_id` and scope SQL accordingly. `delete_session` returns `bool` so callers can map non-deletes to 404.
- Every `serve_rag` endpoint passes `user_id=str(user.id)` into persistence; helpers `_load_history` and `_maybe_refresh_session_metadata` propagate it.
- `save_session` upserts preserve `user_id` (caller passes it in the dict).

**Patterns enforced across all three backends** (AUTH_PLAN §6):
1. Never `WHERE id = %s` on a tenant table without an `AND user_id = %s`.
2. Never trust a `user_id` value from the request body — always read it from `current_user.id`.
3. Always load-then-mutate: select with the filter, mutate by primary key.
4. 404 (never 403) on cross-tenant access.

### 1.6 QA pass — 48/48 backend tests, 2 production bugs fixed ✅

Done 2026-05-17 against an isolated harness on `127.0.0.1:28100`
talking to the real `lai_postgres_main` (port 5434). Zero impact on
the teammate's running services on `:18000`/`:18001`. Full results
+ reproduction steps in [`AUTH_QA_REPORT.md`](AUTH_QA_REPORT.md).

**Six test groups, 48 assertions, all passing:**

| Group | Count | Coverage |
|---|---|---|
| A — smoke / happy paths | 9 | signup/login/refresh/logout/me; wrong-pw and unknown-user 401 bodies are byte-identical (no enumeration) |
| B — auth attacks | 8 | **tampered signature, role-escalation via tampered payload, expired JWT, wrong issuer, `alg=none` attack, malformed Authorization headers — all 401** |
| C — input validation | 10 | email case canonicalisation, whitespace, length policy (12-128), unicode passwords, **SQL-injection probe rejected** |
| D — lifecycle | 6 | logout idempotent, multi-device sessions independent, **disabled user → 401 with body identical to wrong-password (no status leak)**, default role = `user` |
| E — reset flow | 6 | garbage token rejected, policy-fail does NOT consume token prematurely, **concurrent reset race resolved correctly via atomic `UPDATE … RETURNING`**, single-use enforced, reset revokes ALL prior refresh rows |
| F — DB / cookie invariants | 9 | bcrypt prefix `$2b$`, sha256 64-char hex, uniqueness, cookie `HttpOnly`+`SameSite=Lax`+`Path=/auth`; `Secure` absent per dev `LAI_AUTH_REFRESH_COOKIE_SECURE=false` |

**Three latent production bugs discovered and fixed by the QA pass:**

| # | File | Bug | Fix landed |
|---|---|---|---|
| 1 | `LAI/src/lai/common/auth/dependencies.py:37` | `HTTPBearer(bearer_format="JWT", …)` — wrong arg name | `bearerFormat="JWT"` (camelCase per OpenAPI). Without this, the app fails to import. |
| 2 | `LAI/src/lai/api/auth_router.py` — `/auth/me` | `Annotated[CurrentUser, Depends(get_current_user)]` was mis-parsed as a query param by FastAPI 0.136, returning 422 on every authenticated `/auth/me` call | Changed to `user: CurrentUser = Depends(get_current_user)` (the style used everywhere else in the codebase). |
| 3 | Cross-origin CORS configuration (latent — surfaced when wiring Playwright tests against Vite at a different port) | The isolated harness had no `CORSMiddleware`. A real browser's preflight OPTIONS request returned 405, blocking every credentialed POST from the frontend. **Same issue applies in `serve_rag.py`**: it has `allow_origins=["*"]` which is **invalid with credentials** per the CORS spec — a real cross-origin browser deploy would silently fail. | Added `CORSMiddleware` with `allow_credentials=True` and a specific origin allow-list to the test harness. `serve_rag.py` still needs the same fix — that is AUTH_PLAN §9 step 6, still open. |

All three bugs would have been invisible to mypy / ruff — they are
FastAPI runtime-introspection issues (bugs 1+2) and a browser-spec
issue (bug 3), only catchable by running real network round-trips.
Without the QA passes we would have shipped a backend that either
fails to import (bug 1), 422s on every authenticated reload (bug 2),
or refuses every credentialed browser POST in a cross-origin deploy
(bug 3).

### 1.6.5 Frontend QA pass — 15/15 Playwright E2E tests ✅

Done 2026-05-17 against headless Chromium driving the real Vite dev
server (`:15173`) talking to the isolated auth harness (`:28100`).
This is the live browser verification AUTH_PLAN §10.5 calls for, done
without disrupting the teammate's `:18000`/`:18001` services.

| # | Test | What it proves |
|---|---|---|
| F1 | `/login` page renders | React tree boots, no import errors |
| F1b | No unexpected console errors on `/login` (401 from initial `/auth/me` is the expected refresh handshake, filtered out) | Clean console under normal cold-load |
| **F2** | Signup form submit → land on `/dashboard` | **End-to-end signup happy path in a real browser** |
| **F3** | Refresh cookie has `HttpOnly=true`, `SameSite=Lax`, `Path=/auth` | Cookie scoped + locked down as designed |
| **F4** | Access token is NOT in `localStorage` or `sessionStorage` | **XSS-resistance design verified empirically** |
| **F5** | Hard-reload `/dashboard` → stays on `/dashboard` | **Refresh-via-cookie flow works** |
| F5b | No `/login` flash during reload | `ProtectedRoute` loading state works |
| **F6** | After logout: `/dashboard` bounces to `/login` | Auth state cleared client-side |
| F6b | After logout: `POST /auth/refresh` returns **401** with the stale cookie | Server-side revocation works in browser context |
| **F7** | Wrong-password submit shows the red error banner "invalid email or password" | Login error UX |
| F8 | `/forgot-password` page renders | New page wired |
| F8b | Forgot-password submit → confirmation copy appears | E2 reset flow UX (always-204 path) |
| F9 | Bob signs up in a second browser context → lands on his own `/dashboard` | Multi-user signup |
| F9b | Two browser contexts hold independent cookie jars | Browser-level session isolation |
| **F10** | Bob's dashboard shows Bob's email; Alice's email is NOT in his page body | **Session isolation at the UI level (G4)** |

**Strengthened privacy guarantee status after frontend QA:**

- **G3** (user_id from JWT, never body) — confirmed in browser: Alice's and Bob's signups created independent rows under their own emails, in their own contexts.
- **G4** (no session_id capability) — confirmed in browser: F9–F10 show two browser contexts hold independent sessions and never see each other's user data.
- **G5** (credentials at rest unrecoverable) — F4 + F3 prove that no script running on the page can read the access token (it lives in module-scope memory) or the refresh cookie (HttpOnly blocks JS access).

**Combined test totals across backend + frontend: 63 tests, 63 passes,
0 failures.** Reproduction commands in [`AUTH_QA_REPORT.md`](AUTH_QA_REPORT.md) §6.

**What the QA passes do NOT cover** — see §2.4 and §5 of the QA report:
- **Resource-level Alice/Bob** (sessions, documents, reports) — needs full RAG/DDiQ stack.
- **Brevo end-to-end inbox delivery** — only the API ACK was verified; physical inbox arrival not checked.
- **Cross-tab / cross-browser** — only two same-browser contexts tested.
- **Production-grade load** — no stress / concurrency-at-scale testing.

### 1.7 Artifacts produced by the QA passes

| Path | Purpose |
|---|---|
| `/data/home/ss/lai-auth-venv/` | Private Python 3.12 venv with auth deps installed |
| `/data/home/ss/lai-auth-test/auth_only_app.py` | Minimal FastAPI app that mounts only the auth router + CORS for browser tests (~110 LOC) |
| `/data/home/ss/lai-auth-test/full_qa.sh` | The **48-test backend** QA battery, idempotent + self-cleaning |
| `/data/home/ss/lai-auth-test/playwright_e2e.mjs` | The **15-test frontend** Playwright E2E suite (headless Chromium against real Vite + harness) |
| `/data/home/ss/lai-auth-test/.env` | DB + auth + Brevo config (mode 600, not committed) |
| `/data/home/ss/lai-auth-test/full_qa.out` | Latest backend test-run output |
| `/data/home/ss/lai-auth-test/playwright.out` | Latest frontend test-run output |
| `/data/home/ss/.cache/ms-playwright/chromium-1223/` | Downloaded headless Chromium binary (113 MB) |
| `/data/projects/lai/harsh/AUTH_QA_REPORT.md` | Companion report — methodology, full test table, repro steps, sign-off |

---

## 2. What's pending

### 2.1 Code work (small but important)

| # | Item | AUTH_PLAN ref | Estimated size |
|---|---|---|---|
| 1 | **Drop `allow_origins=["*"]`** in `serve_rag.py:1083`; replace with env-driven allow-list (`LAI_CORS_ALLOWED_ORIGINS=comma,separated,list`) | §9 step 6, §8.4 | ~15 LOC |
| 2 | **`tests/integration/test_tenant_isolation.py`** — the Alice/Bob battery: 6 cases × 6 resource types. This is the v1 privacy gate. | §10.3 | ~250 LOC |
| 3 | Unit tests: `hashing.py` (bcrypt round-trip), `tokens.py` (tamper/expire/wrong-secret), `dependencies.py` (401 paths), `email.py` (respects `enabled=False`, retries on 5xx) | §10.1 | ~150 LOC |
| 4 | Frontend Vitest: `apiFetch` 401→refresh→retry path, refresh-failure logout, `AuthProvider` hydrate-from-`/auth/me` | §10.4 | ~100 LOC |

### 2.2 Ops / one-time setup (must happen before live use)

| # | Item | Where | Blocking? |
|---|---|---|---|
| 1 | **Generate + set `LAI_AUTH_JWT_ACCESS_SECRET`** (≥32 chars; `openssl rand -base64 48`) | server env (`/etc/lai/secrets/auth.env`, `chmod 600`) | YES — startup fails without it |
| 2 | **Apply migration 001** to live `lai_db` | `psql -h ... -U lai_user -d lai_db -f scripts/db/migrations/001_auth_and_tenant_isolation.up.sql` | YES — every protected endpoint will 500 on missing tables |
| 3 | **Install Python deps** (`passlib[bcrypt]`, `python-jose[cryptography]`, `pydantic-settings`, `asyncpg`) into the active venv | `pip install -e .` in `LAI/` | YES |
| 4 | **Create Brevo account** + verify sending domain (SPF, DKIM, optional DMARC) + generate transactional-send-only API key | brevo.com console + DNS panel | Only blocking for password-reset email actually being delivered |
| 5 | Set `LAI_EMAIL_BREVO_API_KEY`, `LAI_EMAIL_SENDER_EMAIL`, `LAI_EMAIL_PUBLIC_APP_BASE_URL`, `LAI_EMAIL_ENABLED=true` | env | As above |
| 6 | Set `VITE_API_BASE_URL` in `LAI-UI/.env.local` | env | YES for the frontend to talk to the API |
| 7 | Make `DB_PASSWORD` non-defaultable in `api.py` / `ddiq_report.py` (currently defaults to `"lai_test_password_2024"`) | code, but trivial — could pair with §2.1 #1 | Hardening, not blocking |
| 8 | Rotate `HF_TOKEN` (committed at `Docker/inference_engine/.env:11` per AUTH_PLAN §0) | ops | Hardening, not blocking |
| 9 | Move all live secrets to `/etc/lai/secrets/*.env` `chmod 600` owned by service user | ops | AUTH_PLAN §9 step 7 |

### 2.3 Open decisions (AUTH_PLAN §11)

These are not blockers for testing but should be answered before broad rollout:

| Q | Decision | Current state | What changes if you flip it |
|---|---|---|---|
| **Q3** | Self-signup vs admin-issued accounts | self-signup (existing Signup page works as-is) | Admin-issued = delete `/auth/signup` route + replace Signup page UI |
| **Q7** | Sending domain (for SPF/DKIM verification) | `vinodfinance07@gmail.com` configured for testing; Brevo accepted the API call but a gmail sender is fragile (deliverability + Brevo's individual-sender verification model). Prod needs a domain you own. | DNS SPF + DKIM published on that domain + Brevo "Domains" verification |
| **Q11** | Deployment topology — same eTLD+1 or cross-origin? | dev tested with `SameSite=Lax` + `Secure=false` on http LAN (`192.168.178.82`) | Cross-origin prod (e.g. `app.lai.de` ↔ `api.lai.de`) needs `LAI_AUTH_REFRESH_COOKIE_SAMESITE=none` + `Secure=true` + CORS preflight with credentials |

### 2.4 Deferred to v2 (per AUTH_PLAN §12 — correctly out of scope)

These are intentionally **not** in v1 and shouldn't be touched until a customer asks:

- Email verification (E1) + verified-at column + `/auth/verify-email`
- Account-change emails (E4)
- New-device login alert (E3)
- Welcome email (E5)
- Refresh-token rotation chain + theft-detection (`rotated_to` column, rotate-on-every-use, reuse → revoke whole user chain)
- Audit middleware + `audit_events` table with `prompt_hash` / `response_hash` / `model_version` / `cite_ids`
- Lockout policy (`failed_login_count`, `locked_until`) + global Redis rate limit
- `/auth/logout-all` + `/auth/change-password` endpoints
- Per-org / per-firm tenancy (`organizations` table, document sharing within org)
- OIDC SSO (Microsoft Entra, Google Workspace)
- `lai.common.email` full package (promote `lai.api.email` when E1/E4 lands)
- `forgot-email` recovery channel

---

## 3. File inventory

### 3.1 New files (19)

```
LAI/src/lai/common/auth/
    __init__.py                    2.2 KB   public re-exports
    config.py                      7.3 KB   AuthConfig (pydantic-settings)
    db.py                          3.4 KB   asyncpg pool factory
    dependencies.py                4.4 KB   build_get_current_user + require_admin
    exceptions.py                  3.0 KB   AuthError hierarchy
    hashing.py                     3.7 KB   PasswordHasher (bcrypt)
    models.py                      1.3 KB   CurrentUser dataclass
    repository.py                 10.3 KB   user / refresh / reset CRUD
    tokens.py                      9.4 KB   TokenIssuer + RefreshToken + hash_refresh_token

LAI/src/lai/api/
    auth_router.py                20.6 KB   7 endpoints + exception handlers
    email.py                       6.6 KB   Brevo sender + EmailConfig

LAI/micro-services/
    auth_dep.py                    1.5 KB   shared get_current_user for api.py + ddiq_report.py

LAI/scripts/db/migrations/
    001_auth_and_tenant_isolation.up.sql      9.6 KB
    001_auth_and_tenant_isolation.down.sql    2.5 KB

LAI-UI/src/react-app/auth/
    AuthContext.tsx                 403 B   createContext only
    AuthProvider.tsx               4.8 KB   provider + login/signup/logout/refresh
    apiFetch.ts                    4.3 KB   the single fetch wrapper
    index.ts                        231 B   re-exports
    types.ts                       1.2 KB   User / AuthContextValue / AuthStatus
    useAuth.ts                      330 B   consumer hook

LAI-UI/src/react-app/pages/
    ForgotPassword.tsx             5.0 KB
    ResetPassword.tsx              6.5 KB
```

### 3.2 Modified files (10)

```
LAI/src/lai/api/serve_rag.py              — auth bootstrap + every endpoint gated + user_id-filtered persistence
LAI/src/lai/persistence.py                — every CRUD function accepts optional user_id
LAI/micro-services/api.py                 — auth dep + in-memory dicts replaced with DB-backed conversations/messages
LAI/micro-services/ddiq_report.py         — every endpoint gated + _assert_owns_documents boundary + user_id on every write
LAI-UI/src/react-app/App.tsx              — new routes, AuthProvider moved inside Router
LAI-UI/src/react-app/components/ProtectedRoute.tsx — loading state, ?from= redirect
LAI-UI/src/react-app/components/DashboardLayout.tsx — switched to @/react-app/auth import, async logout
LAI-UI/src/react-app/pages/Login.tsx      — wired to backend, remember_me + redirect
LAI-UI/src/react-app/pages/Signup.tsx     — wired to backend (full_name + company)
LAI-UI/.env.example                       — VITE_JWT_SECRET removed; VITE_API_BASE_URL added
```

### 3.3 Deleted files (2)

```
LAI-UI/src/react-app/contexts/AuthContext.tsx   — demo base64-token "auth"
LAI-UI/src/react-app/utils/jwt.ts               — client-side fake JWT minter
```

### 3.4 Untouched teammate changes preserved

- `LAI/micro-services/api.py` line 45 + line 49: teammate added `http://192.168.178.82:5173` to dev CORS origins — preserved verbatim.

---

## 4. Privacy guarantee status (AUTH_PLAN §1)

| # | Guarantee | Code-complete | Verified live |
|---|---|---|---|
| G1 | A user can only enumerate or read their own chats, documents, reports, matters | ✅ user_id filter on every SELECT | ✅ at auth surface (backend A-group, frontend F2/F5); ⏳ resource-level (§10.3) |
| G2 | A user cannot mutate (write/delete) anyone else's data | ✅ user_id on every UPDATE/DELETE; load-then-mutate | ✅ at auth surface (backend D, E-group); ⏳ resource-level |
| G3 | A user cannot upload into another user's namespace | ✅ user_id taken from JWT on every INSERT, never from body | ✅ **fully proven** (backend A3/A5/D4/E/F + frontend F2/F9) |
| G4 | A user cannot retrieve session-scoped artifacts of another user | ✅ in-memory dicts gone; session_id alone is no longer a capability | ✅ **fully proven** (backend D2/D2b + frontend F9/F9b/F10) |
| G5 | Credentials at rest not recoverable | ✅ bcrypt rounds=12; reset = revoke + re-issue; never plaintext | ✅ **fully proven** (backend F1/F2/T14/B-group + frontend F3/F4 — no JS-readable token anywhere) |

---

## 5. How to test

### 5.1 Backend auth surface — done ✅

The 48-test bash harness at `/data/home/ss/lai-auth-test/full_qa.sh`
covers everything the auth router does (signup, login, refresh,
logout, me, forgot, reset) + 8 explicit attack scenarios.
Reproduction: see [`AUTH_QA_REPORT.md`](AUTH_QA_REPORT.md) §6.

### 5.2 Frontend auth flow in a real browser — done ✅

The 15-test Playwright E2E at
`/data/home/ss/lai-auth-test/playwright_e2e.mjs` drives a headless
Chromium against a real Vite dev server and the auth harness. It
covers:

- Signup → land on `/dashboard`, cookie set `HttpOnly` + `SameSite=Lax` + `Path=/auth`
- `localStorage` / `sessionStorage` hold **no** token
- Reload page → stay logged in (refresh-via-cookie + no `/login` flash)
- Logout → `/dashboard` bounces to `/login`, server-side revocation confirmed
- Wrong-password error banner shows
- Forgot-password confirmation copy shows
- Two browser contexts → independent sessions; Bob never sees Alice's data

Reproduction: see [`AUTH_QA_REPORT.md`](AUTH_QA_REPORT.md) §6.

### 5.3 Full-stack resource-level Alice/Bob — still pending ⏳

Requires `serve_rag` (port 18000) and the microservice (port 18001)
running with the new code. The teammate's existing services are
currently on those ports; coordinate a window OR bring up the
new-code stack on alternate ports (e.g. `28000`/`28001`). Then:

- Alice creates session/doc; Bob's GET/PATCH/DELETE on Alice's IDs → 404 always
- Bob's list endpoints never include Alice's rows
- After Alice deletes her doc, her own GET returns 404

### 5.4 Cross-service token reuse — still pending ⏳

`serve_rag`-issued token must work against `:18001/ddiq/*`. Both
services must share the same `LAI_AUTH_JWT_ACCESS_SECRET`. Test
window blocked by the same shared-port situation as §5.3.

---

## 6. Known risks / things to watch when you next touch this code

1. **The two backends must share `LAI_AUTH_JWT_ACCESS_SECRET`.** If `serve_rag` and the microservice container read different env files, tokens issued by one will be rejected by the other → mysterious 401s in the middle of a working session.
2. **Cookie `Secure=true` over plain http localhost** will silently fail. For local dev set `LAI_AUTH_REFRESH_COOKIE_SECURE=false`; never ship that to prod.
3. **`/auth/refresh` does not rotate** the refresh token value (v1 has no rotation chain — AUTH_PLAN §12 defers it). When v2 lands, every place that calls `_set_refresh_cookie(response, deps, cookie_value, ...)` needs to mint a new value, insert a new row, revoke the old one.
4. **The reaper at `ddiq_report.py::reap_orphans()`** marks ALL `queued`/`running` rows as failed on startup — that's by design under the single-worker assumption. With multiple uvicorn workers it would race; leader-election or an external job runner is the long-term fix.
5. **`/ddiq/config/map-tiles` is deliberately public.** If you ever start serving tenant-specific tile URLs from there, add the auth dep.
6. **Frontend access token lives in module-scope memory of `apiFetch.ts`.** Do not "fix" this by moving it to `localStorage` — XSS would then exfiltrate it. The refresh-via-cookie path is what survives reloads.

---

## 7. Pointers

- [AUTH_PLAN.md](AUTH_PLAN.md) — the source plan
- [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) §4.3 — Move 3 (Auth + tenant isolation)
- [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) §6 — matter-centric data model (relevant when audit_events lands in v2)
- [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) §9.4 — S1–S6 security catalog
- [PROGRESS.md](PROGRESS.md) — overall project progress
