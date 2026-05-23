# LAI V1 — Multi-User (Firm-Tenant) Implementation Plan

**Date:** 2026-05-22
**Status:** Implementation plan for firm-wide multi-user access. Extends
`IMPLEMENTATION_GUIDE.md` §4.3 (auth + tenant isolation), §6 (matter-centric
data model), and §9.4 (security & tenant isolation). Builds on the auth work
already shipped in `scripts/db/migrations/001_auth_and_tenant_isolation.up.sql`
and described in `AUTH_PLAN.md`.

**Why this exists:** Today LAI is single-tenant *per user*. Every resource is
owned by exactly one `user_id` and gated `WHERE user_id = ?` with a 404 on
mismatch. Two associates at the same firm therefore cannot work the same
Matter — the second sees a 404. Every serious pilot involves a team, so this
is the single most likely pilot-killer. This plan makes the **firm** the unit
of access.

**Decision (locked for this plan):**
- **Firm-wide org tenancy.** The isolation key flips from `user_id` to
  `org_id`. Everyone in a firm sees every Matter, report, and document in that
  firm. `user_id` is retained on every row as `created_by` (attribution +
  audit), per the `audit_events` intent in `IMPLEMENTATION_GUIDE.md` §6.
- **Open signup, admin-curated membership.** Self-registration stays open
  (per product decision 2026-05-22). A new user lands **org-less** with a
  friendly "ask your firm admin to add you" empty state; they cannot create
  Matters until placed in a firm. Firm admins **search the user directory and
  add members** to the org — optimized search in §7.1. Gated by the
  already-defined-but-unused `require_admin` (`dependencies.py:99-121`).
- **Ethical walls = optional per-matter restriction over a firm-open default**
  (§7.2). This is the legal-DMS market norm: iManage and NetDocuments expose
  "need-to-know" matter/workspace security *layered over* firm-wide access,
  with audit trails for conflict-of-interest compliance (web-verified, §7.2).
  Default is open to the whole firm; a Matter can be switched to **Restricted**
  (named members + firm admins only). Default-open keeps the common case
  zero-friction.
- **Project model moves server-side** (in scope — see §6). The FE "project"
  grouping is localStorage-only today; firm-wide sharing is incoherent without
  this.

**Rules followed (per `IMPLEMENTATION_GUIDE.md` writing rules):**
- Every code-level claim cites `file:line`.
- Open decisions are surfaced explicitly (§13), not silently chosen.
- Migration follows the staged nullable → backfill → NOT-NULL + FK pattern
  proven in migration 001.

---

## Table of contents

1. The problem (what exists today)
2. The decision and its consequences
3. The org-tenant data model
4. Identity / JWT changes
5. The authorization rewrite (endpoint inventory)
6. The project model server-side migration (P0)
7. Membership, admin & access control (search + ethical walls)
8. Migration & backfill (002)
9. Concurrency & attribution
10. Implementation order (phases)
11. Test suite
12. Rollout & the deploy gap
13. Open decisions & out of scope
14. Appendix — key file references

---

# 1. The problem (what exists today)

Every resource is owned by exactly one user and gated on exact `user_id`
equality, returning **404 (never 403)** on mismatch so existence never leaks.
Probed directly this session:

| Resource | Store | Ownership gate | Evidence |
|----------|-------|----------------|----------|
| Matter / chat session | SQLite | `WHERE id=? AND user_id=?` | `persistence.py:277` |
| Session list | SQLite | `WHERE user_id=?` | `persistence.py:449` |
| Matter documents, messages, feedback, meta | SQLite | scoped via session ownership | `persistence.py:495,517,606,800` |
| DDiQ documents | Postgres | `WHERE user_id=%s` | `ddiq_report.py:1582` |
| DDiQ reports | Postgres | `WHERE id=%s AND user_id=%s` | `ddiq_report.py:2724` |
| DDiQ report dedup fingerprint | Postgres | keyed on `user_id` | `ddiq_report.py:1659-1672` |

There is **no organization / team / firm / workspace primitive anywhere.**
`users.company` is free text (`migration 001`, `users` table), not a grouping
mechanism; `users.role` is only `user`/`admin`.

**The project layer is worse than single-tenant — it is single-browser.** The
FE "Project" the user actually interacts with is persisted only in
localStorage (`LAI-UI/src/react-app/components/project/data.ts:10` — *"The
projects workspace is local-only (not backend-synced)"*). Individual
conversations map to real backend sessions, but the project grouping exists
nowhere on the server. So even if Matters were shared, a teammate would see
orphaned sessions/reports with no project structure.

**Note on the recent cross-account fix (`LAI-UI` commit `f41079b`):** it
hardens the *single-browser* wall (account-switching no longer leaks
localStorage / in-memory chat). That is correct for confidentiality on a
shared machine and is **orthogonal** to firm sharing — it stays. Multi-user is
a backend-authorization change, not a frontend one.

---

# 2. The decision and its consequences

**The change in one sentence:** isolation flips from *"owned by `user_id`"* to
*"belongs to `org_id`"*; `user_id` survives as `created_by`.

Consequences that ripple through the build:

- Every `WHERE user_id = ?` (≈12 endpoints, two stores) becomes
  `WHERE org_id = ?`. The question changes from "is the caller the owner" to
  "is the caller in the owning firm."
- Ownership lives in **two stores** — SQLite (chat) and Postgres (DDiQ) — plus
  pgvector chunk tags. All three must carry `org_id` or one half stays
  invisible to teammates.
- The DDiQ report dedup fingerprint must key on `org_id`, so two associates
  requesting the identical report collapse to one job instead of two
  per-user copies.
- The project grouping must become a server-side, org-scoped entity (§6).

---

# 3. The org-tenant data model

*(Schema in the `IMPLEMENTATION_GUIDE.md` §6 style. New tables in Postgres
`lai_postgres_main`; SQLite chat store gets mirror `org_id` columns until the
keystone unification in §4.1 of the guide lands.)*

```
organizations                         -- the firm; the tenant boundary
├── id (uuid, PK)
├── name (text)                       -- "Nordlicht Wind Rechtsabteilung"
├── status (text)                     -- 'active' | 'disabled'
├── created_at, updated_at

users  (existing — migration 001; ADD org_id)
├── id (uuid, PK)
├── org_id (uuid, FK organizations)   -- NEW: every user belongs to one firm
├── email, email_canonical, password_hash, full_name, company
├── role (text)                       -- 'user' | 'admin' (admin = firm-admin)
├── status (text)
└── ...

projects                              -- NEW: server-side, replaces the
├── id (uuid, PK)                        localStorage-only FE grouping (§6)
├── org_id (uuid, FK organizations)   -- the firm owns the project
├── created_by (uuid, FK users)       -- attribution, not access
├── name (text)
├── bundesland (text, 2-char ISO)     -- aligns with matters in guide §6
├── access (text)                     -- 'open' (default; whole firm) |
│                                         'restricted' (members + admins only)
├── archived (bool)
├── created_at, updated_at

project_members                       -- the ethical wall (§7.2)
├── project_id (uuid, FK projects)
├── user_id (uuid, FK users)
├── added_by (uuid, FK users)         -- who placed them on the matter (audit)
├── created_at
└── (project_id, user_id) UNIQUE
-- Consulted ONLY when projects.access = 'restricted'. When 'open' (default),
-- the whole org sees the project and this table is ignored.
```

Resource tables gain `org_id` (mirrors the migration-001 `user_id` rollout):

```
-- Postgres (DDiQ), each ADD COLUMN org_id UUID + index:
ddiq_documents, ddiq_reports, ddiq_project_areas,
ddiq_contracts, ddiq_classified_parcels
  + a project_id FK so a report/document hangs off a shared project

-- SQLite (serve_rag chat), each ADD COLUMN org_id TEXT via the
-- forward-compat ALTER block in persistence.init() (persistence.py:160-205):
sessions, matter_documents, feedback
  + project_id TEXT so a chat session belongs to a shared project
```

**Access rule, uniformly:** a row is visible/editable iff
`row.org_id = current_user.org_id`. `created_by` is display-only.

**Backward compatibility** (same posture as guide §6): existing rows backfill
`org_id` from their current owner's `org_id`; the legacy account
(`00000000-…-0001`, migration 001) maps to a `legacy` org so the smoke-test
report stays retrievable. No data lost.

---

# 4. Identity / JWT changes

The access token is stateless (no per-request DB lookup —
`dependencies.py:75-89`), so org membership is carried in the token.

- **Add `org_id` to access-token claims** — `tokens.py:138-150`
  (`issue_access_token` claims dict, alongside `sub`/`email`/`role`).
- **`CurrentUser` gains `org_id: UUID`** — `models.py:17-40`; populated in
  `build_get_current_user` at `dependencies.py:88`.
- **DDiQ inherits it for free** — it imports the same `get_current_user`
  (`micro-services/auth_dep.py`); no second secret, no second decoder.
- **Org change ⇒ re-login.** Since org is baked at mint time and TTL is short,
  moving a user between firms takes effect on the next token refresh. Acceptable
  — org membership is effectively permanent for a pilot.

---

# 5. The authorization rewrite (endpoint inventory)

Every handler swaps the scope value from `user.id` to `user.org_id` and stamps
`created_by = user.id` on insert. The persistence functions already take an
optional scope arg, so signatures are stable — only the column name and the
value passed change.

| Endpoint | File | Change |
|----------|------|--------|
| `GET /sessions` | `serve_rag.py:3681` | scope by `org_id` |
| `GET /sessions/{id}` | `serve_rag.py:3691` | scope by `org_id` |
| `GET /sessions/{id}/messages` | `serve_rag.py:3710` | scope by `org_id` |
| `GET /sessions/{id}/documents[/{n}]` | `serve_rag.py:3970,4003` | scope by `org_id` |
| `DELETE /sessions/{id}` | `serve_rag.py:4034` | scope by `org_id` |
| `PATCH /sessions/{id}` (rename) | `serve_rag.py:4057` | scope by `org_id` |
| `POST /upload` | `serve_rag.py:3389` | `org_id` + `created_by` on insert |
| `POST /feedback` | `serve_rag.py:3794` | scope by `org_id` |
| `GET /ddiq/documents` | `ddiq_report.py:1576` | `WHERE org_id=%s` |
| `DELETE /ddiq/documents/{id}` | `ddiq_report.py:1623` | `WHERE org_id=%s` |
| `GET /ddiq/reports` | `ddiq_report.py:2677` | `WHERE org_id=%s` |
| `GET/DELETE /ddiq/report/{id}` | `ddiq_report.py:2720,2733` | `WHERE org_id=%s` |
| report fingerprint / dedup | `ddiq_report.py:1659` | key on `org_id` |
| `_assert_owns_documents` → `_assert_in_org` | `ddiq_report.py` | org membership check |

Persistence layer (`persistence.py`) — the `user_id` scope param becomes
`org_id` in: `load_session`, `list_sessions`, `session_exists`,
`delete_session`, `update_session_title`, `list_messages`, `add_message`,
`list_matter_documents`, `get_matter_document`, `record_feedback`,
`list_feedback`, `get/set_session_meta`.

**404-on-miss preserved** — now "not in your firm" rather than "not yours." No
existence leak (guide §9.4 / AUTH_PLAN rule 4).

**Two added predicates:**
- **Org-less create guard.** Because signup is open, a user may have
  `org_id IS NULL`. Read endpoints return an empty workspace for them; *create*
  endpoints (`/upload`, `/projects` POST, `/ddiq/*` generate) reject with 403
  *"join a firm to create matters"*. This keeps every resource's `org_id`
  NOT NULL (no orphan rows) and gives a clear UX (§7).
- **Ethical-wall predicate.** For project-scoped reads, the org filter gains
  `AND (projects.access = 'open' OR EXISTS project_members(project_id, me) OR
  me.is_admin)`. Sessions/reports inherit the wall through their `project_id`.

---

# 6. The project model server-side migration (P0)

This is the largest single piece and the prerequisite for a *coherent* shared
experience. Today `LAI-UI/.../project/data.ts:8-81` reads/writes the project
list to `localStorage` under a per-user key. We move it to the backend.

- **Schema:** the `projects` table in §3, plus `project_id` FKs on
  `sessions`/`ddiq_*` so a chat session and a DDiQ report both hang off the
  same shared project.
- **Endpoints (org-scoped):** `GET/POST/PATCH/DELETE /projects`,
  `GET /projects/{id}/conversations`, `GET /projects/{id}/reports`.
- **FE cutover:** `loadProjects`/`saveProjects` (`data.ts:54,71`) swap from
  `localStorage` to the new endpoints. The per-user localStorage namespacing
  shipped in `f41079b` degrades to an optional offline cache (or is removed).
- **Migration of existing local projects:** localStorage data lives only in
  each browser and cannot be read server-side. On first post-deploy load, the
  FE does a one-time push of any locally-cached projects to the backend
  (best-effort), then switches to server as source of truth. Document this as a
  known one-time reconciliation; pilot data volume is small.

---

# 7. Membership, admin & access control (search + ethical walls)

**Open signup is retained.** `auth_router.py:246-287` is unchanged except that
it does **not** assign an org — a self-registered user is `status='active'` but
`org_id IS NULL`. Their JWT carries a null `org_id`; org-scoped reads return
empty and creates are blocked (§5), so they land on a friendly empty state:
*"Your account isn't linked to a firm yet — ask your firm admin to add you."*
No data, no confusion, no orphan rows.

Firm admins (`role='admin'`, gated by `require_admin`,
`dependencies.py:99-121`) manage membership:

- `POST /admin/orgs` — create a firm org (also a seed script
  `scripts/db/seed_pilot_org.py` for the pilot, mirroring the legacy-account
  seed in migration 001).
- `GET /admin/users/search?q=…` — find addable users (§7.1).
- `POST /admin/orgs/{id}/members` — add a found user to the org (sets
  `users.org_id`). Reuses `UserRepository` (`repository.py:102-156`); add an
  `org_id` setter.
- `DELETE /admin/orgs/{id}/members/{user_id}` — remove (clears `org_id`; the
  user's `created_by` rows stay with the firm).
- `PATCH /admin/users/{id}` — disable a user, promote to firm-admin.

## 7.1 Optimized member search

Admins find people to add by typing a name or email. Performance and privacy
requirements:

- **Scope (privacy/GDPR, guide §9.4):** search returns only **org-less users**
  (`org_id IS NULL`) plus the admin's **own-org members** (for management).
  Other firms' members are never returned — an admin cannot enumerate a rival
  firm's roster. Adding is effectively "claim an unaffiliated account."
- **Index:** enable `pg_trgm` and add GIN trigram indexes so substring search
  is index-backed, not a sequential scan:
  ```sql
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
  CREATE INDEX users_full_name_trgm ON users USING gin (lower(full_name) gin_trgm_ops);
  CREATE INDEX users_email_trgm     ON users USING gin (email_canonical  gin_trgm_ops);
  ```
  (`email_canonical` already has a btree index from migration 001 for exact
  login lookups; the trigram index serves typeahead.)
- **Query:** `WHERE org_id IS NULL AND (lower(full_name) ILIKE '%'||q||'%' OR
  email_canonical ILIKE '%'||q||'%')`, ranked by `similarity()`, **min 2-char**
  `q` (reject shorter to avoid scanning the whole table), keyset-paginated,
  `LIMIT 20`. Returns minimal fields only (`id, full_name, email, company`).
- **Abuse/compliance:** rate-limit per admin; write an admin **audit event**
  per search and per add (conflict-of-interest audit trails are a market
  expectation — §7.2 sources).
- **UX:** 250 ms debounced typeahead; already-added users are filtered out of
  results; one-click **Add**. User-friendly and fast even as the directory
  grows.

## 7.2 Ethical walls (optional per-matter restriction)

**Market basis (web-verified 2026-05-22):** legal DMS implement ethical walls
as granular "need-to-know" access at the user/document/workspace level *layered
over* default firm access, with audit trails for managing conflicts of
interest. NetDocuments markets a Workspace Security Manager for exactly this;
iManage ships ethical walls (information barriers) + document-level security +
audit trails; AI-legal vendors (Harvey) treat ethical walls as a first-class
agent constraint. The market shape is **firm-open by default, restrict on
demand** — which is what we build.

- **Model:** `projects.access ∈ {open, restricted}`, default `open`. When
  `restricted`, only `project_members` + firm admins can see/edit the project
  and everything under it (sessions, documents, DDiQ reports inherit via
  `project_id`). Enforced by the §5 ethical-wall predicate.
- **Management (project owner or firm admin):**
  - `PATCH /projects/{id}/access` — toggle open ↔ restricted.
  - `POST /projects/{id}/members` / `DELETE /projects/{id}/members/{user_id}` —
    manage the wall, using the same optimized search but scoped to **own-org
    members** (you can only wall-in people already in the firm).
- **Audit:** every access-mode change and member add/remove writes an audit
  event (conflict-of-interest defensibility, per the market sources).
- **UX:** a single **"Restrict access"** toggle on the Matter; flipping it on
  reveals a member picker (reusing §7.1). Off by default → firms that don't
  need walls never see the complexity; firms that do get a one-screen control.

Sources: [NetDocuments — Ethical Walls](https://www.netdocuments.com/products/ethical-walls),
[NetDocuments — Security & Governance](https://www.netdocuments.com/solutions/security-data-governance/),
[iManage vs NetDocuments DMS comparison](https://automatedintelligentsolutions.com/imanage-vs-netdocuments-choosing-the-best-legal-dms/),
[Harvey — Long Horizon Agents and Ethical Walls](https://www.harvey.ai/blog/long-horizon-agents-and-ethical-walls).

---

# 8. Migration & backfill (002)

New migration pair `002_org_tenancy.{up,down}.sql`, idempotent, staged exactly
like 001:

1. `CREATE TABLE organizations`; create the pilot org row.
2. `ALTER TABLE users ADD COLUMN org_id UUID` (**stays nullable** — open
   signup means org-less users are valid; do not lock this to NOT NULL).
3. `CREATE EXTENSION pg_trgm`; trigram GIN indexes on `users` for member
   search (§7.1).
4. `CREATE TABLE projects` and `CREATE TABLE project_members`.
5. `ADD COLUMN org_id` (+ `project_id` where applicable) to each `ddiq_*`
   table, nullable, with indexes.
6. **Backfill:** set `users.org_id` to the pilot org (legacy user → legacy
   org); set each resource's `org_id` from its owner's `org_id`.
7. **Lockdown (resource tables only):** `ALTER COLUMN org_id SET NOT NULL` + FK
   to `organizations` on the `ddiq_*` / `projects` / chat tables (DO-block
   guard, copying the 001 pattern). `users.org_id` is intentionally left
   nullable; the §5 create-guard prevents org-less users from making
   `org_id`-NULL resources, so the resource lockdown is still safe.

SQLite side: the `org_id` (+ `project_id`) columns are added in
`persistence.init()`'s forward-compat block (`persistence.py:160-205`) and
backfilled from `sessions.user_id` → owner org on first boot.

`002_org_tenancy.down.sql` drops the columns/tables in reverse, matching the
001 down-migration convention.

---

# 9. Concurrency & attribution

- **Reads:** safe. SQLite WAL handles concurrent readers; the existing
  `_STATE["lock"]` (`persistence.py:288`) serializes writes within the single
  serve_rag process.
- **Two associates in the same chat session:** messages interleave in
  timestamp order. Acceptable for v1. If you want isolated per-user threads
  inside a shared Matter, that is a larger model — flagged in §13.
- **Simultaneous DDiQ report generation:** the org-scoped fingerprint
  (`ddiq_report.py:1659`) dedups identical requests to one job. Good by
  construction.
- **Attribution UI:** because everyone sees everything, the Matter/report/
  project lists must show *"created by X"* (from `created_by`). Small FE
  addition; without it a shared workspace is confusing.

---

# 10. Implementation order (phases)

Phases describe sequence and dependencies, not weeks.

## 10.1 Phase compatibility — what must run before what

| Item | Must come after | Why |
|------|-----------------|-----|
| Migration 002 (org tables + `org_id` columns) | migration 001 applied | Adds onto the auth schema. |
| `org_id` in JWT + `CurrentUser` | migration 002 backfill done | Tokens must resolve to a real org. |
| Endpoint authorization swap (§5) | `org_id` in `CurrentUser` | Handlers read `user.org_id`. |
| Admin + membership search (§7, §7.1) | org tables + pg_trgm index | Admin curates who is in the firm. |
| Project model server-side (§6) | org tables + endpoints | `projects` are org-scoped. |
| FE project cutover | `/projects` endpoints live | FE reads server, not localStorage. |
| Ethical walls (§7.2) | project model live (§6) + member mgmt | Walls restrict a project to named members. |
| NOT-NULL + FK lockdown (resources) | every insert path writes `org_id` | Same staging as 001; `users.org_id` stays nullable. |

## 10.2 Phase A — Schema & identity
Migration 002 (nullable + backfill); `org_id` into claims/`CurrentUser`;
`UserRepository.create` learns `org_id`. No behavior change yet.

## 10.3 Phase B — Authorization swap
Flip all §5 endpoints + persistence scope params to `org_id`. Cross-org tests
go green. This is where sharing actually starts working at the Matter/report
level.

## 10.4 Phase C — Admin & membership
`/admin/orgs*` behind `require_admin`; pilot seed script; the optimized member
search (§7.1, pg_trgm) + add/remove. Open signup stays; org-less users get the
empty-state UX and the create-guard.

## 10.5 Phase D — Project model server-side (P0)
`projects` table + endpoints + FE cutover + one-time localStorage push.
This delivers the *coherent* shared workspace.

## 10.6 Phase E — Lockdown & attribution
`SET NOT NULL` + FK on resource tables; "created by X" in the FE lists.

## 10.7 Phase F — Ethical walls (optional, default-open)
`projects.access` toggle + `project_members` enforcement (§7.2), the FE
"Restrict access" toggle + member picker, and audit events. Ships last:
default-open means core firm sharing already works without it, so this is a
non-blocking add of the conflict-screen capability firms expect in procurement
review.

---

# 11. Test suite

*(In the `IMPLEMENTATION_GUIDE.md` §13 style.)*

- **Cross-org isolation:** org B gets 404 on org A's session, document, report,
  and project — both stores. (Mirrors the existing cross-tenant 404 tests.)
- **Same-org sharing:** user B in org A *can* list/read/continue user A's
  Matter, see its documents, open its DDiQ report, and see the same projects.
- **Backfill correctness:** every pre-existing row resolves to the right
  `org_id`; legacy rows → legacy org.
- **Fingerprint dedup is org-scoped:** two users, same firm, identical report
  request ⇒ one `ddiq_reports` row.
- **JWT carries `org_id`:** decode asserts the claim; a token minted before the
  change (no `org_id`) is rejected / forces re-login.
- **Admin gating:** non-admin gets 403 on `/admin/*`; admin can create org +
  add a member.
- **Open-signup holding state:** a freshly-signed-up org-less user lists empty
  everywhere and gets 403 on create (`/upload`, `/projects`, `/ddiq` generate).
- **Member search:** returns org-less users + own-org members only — never
  another firm's members; rejects `q` shorter than 2 chars; uses the trigram
  index (assert via `EXPLAIN` in an integration test, not a seq scan); excludes
  already-added users.
- **Ethical wall:** an `open` project is visible to the whole org; flipping it
  to `restricted` makes a non-member same-org user get 404 on it and its
  sessions/reports, while listed members + admins still see it; access-mode
  and member changes write audit events.
- **Migration 002 is idempotent** and has a working `.down.sql`.

Add under `tests/unit/api/`, `tests/unit/micro_services/`, and a
`tests/unit/auth/test_org_tenancy.py`.

---

# 12. Rollout & the deploy gap

Order matters; respect the deploy mechanics (guide §1, `WIRING_AUDIT.md`):

1. Apply migration 002 to `lai_postgres_main` (backfill before lockdown).
2. SQLite `ALTER`s run automatically on serve_rag boot.
3. Deploy serve_rag — **rj restarts the host process**
   (`scripts/ops/restart_serve_rag.sh`); committed code is not live until then.
4. Rebuild + redeploy the DDiQ **baked image** (backend + worker) — committed
   code is not live until the image is rebuilt.
5. Deploy FE (project cutover).
6. Run the §11 suite live (cross-org 404 + same-org sharing) before declaring
   the pilot multi-user.

---

# 13. Open decisions & out of scope

1. **Open signup vs admin-only — RESOLVED (2026-05-22): open signup.**
   Self-registration stays on; new users land org-less in the holding state
   (§7) until a firm admin searches for and adds them. No signup change beyond
   not auto-assigning an org.
2. **Ethical walls — RESOLVED (2026-05-22): build, default-open.** Per legal-DMS
   market practice (§7.2, web-verified), Matters are firm-open by default with
   an optional per-Matter **Restricted** mode backed by `project_members`.
   Built as Phase F (non-blocking, ships last). Per-user chat threads inside a
   restricted Matter remain out of scope (item 3).
3. **Per-user chat threads inside a shared Matter.** v1 interleaves messages in
   one shared session. Isolated threads-per-user is a larger model change —
   deferred.
4. **Storage unification.** This plan keeps the SQLite-chat + Postgres-DDiQ
   split and mirrors `org_id` into both. The guide's §4.1 keystone (one
   Postgres) would subsume the SQLite side later; not pulled forward here.

---

# 14. Appendix — key file references

| Concern | Location |
|---------|----------|
| SQLite chat ownership + scope params | `LAI/src/lai/persistence.py` (`:160-205` migrations, `:277` load gate, `:449` list gate, `:288` write lock) |
| serve_rag session/upload/feedback endpoints | `LAI/src/lai/api/serve_rag.py:3389,3681-4066` |
| DDiQ document/report endpoints + fingerprint | `LAI/micro-services/ddiq_report.py:1576-1646,1659-1672,2677-2775` |
| JWT mint + claims | `LAI/src/lai/common/auth/tokens.py:111-150` |
| get_current_user / CurrentUser | `LAI/src/lai/common/auth/dependencies.py:58-91`; `models.py:17-40` |
| require_admin (defined, unused) | `LAI/src/lai/common/auth/dependencies.py:99-121` |
| Signup handler + hook point | `LAI/src/lai/api/auth_router.py:77-81,246-287` |
| User CRUD | `LAI/src/lai/common/auth/repository.py:43-67,70-156` |
| DDiQ shared auth | `LAI/micro-services/auth_dep.py` |
| Existing auth/tenant migration | `LAI/scripts/db/migrations/001_auth_and_tenant_isolation.up.sql` |
| FE project store (localStorage-only) | `LAI-UI/src/react-app/components/project/data.ts:8-81` |
| Cross-account browser isolation (shipped) | `LAI-UI` commit `f41079b` |
