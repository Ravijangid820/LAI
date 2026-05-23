# LAI — Tracked Security Risks

Living register of accepted/known security risks that are **not yet
remediated** because the fix requires coordination beyond a code edit
(credential rotation, infra restarts, owner decisions). Each entry: the
finding, why it's open, the blast radius, and the remediation path.

---

## SR-1 — Live Postgres password is the committed dev default (OPEN, HIGH)

**Date logged:** 2026-05-20 · **Source:** §9.4 S6 audit, Wave 3.

**Finding.** `src/lai/core/config.py:38` —
`PostgresSettings.password` defaults to `SecretStr("lai_test_password_2024")`.
This is not merely a fallback default: the string **is the live
`lai_postgres_main` password currently in use** by the running Step 6
embedding job, the DDiQ microservice, and serve_rag. Verified 2026-05-20
by comparing the default against `micro-services/.env`'s `DB_PASSWORD`
(they match). A weak password is committed in source AND in production
use.

**Related, lower-severity:**
- `core/config.py:84` — MinIO `secret_key = "superStrongPassword123!"`,
  committed. Not currently active: no LAI MinIO container runs (the
  pipeline is in local-storage mode), but it would be live if MinIO is
  enabled.
- `core/config.py:273` — `JWTSettings.secret_key = "CHANGE-ME-IN-PRODUCTION"`.
  **Dead config** — live auth uses `lai.common.auth.AuthConfig`
  (`LAI_AUTH_JWT_ACCESS_SECRET`), nothing reads this field. Safe to
  fail-close or delete `JWTSettings` entirely.

**Why it's open (not auto-fixed).** The guide's "remove the default /
fail closed" is *necessary but insufficient and risky on its own*:
removing the default only moves the SAME weak password into env, which
is cosmetic for security; and making the field required would break the
running 14-day Step 6 job's next restart (plus DDiQ and serve_rag)
unless every consumer's env is set to the password first. The real fix
is a credential rotation, which is a coordinated operational change
against a live shared DB — out of scope for a unilateral code edit while
the stack is serving.

**Blast radius of the real fix (rotation).** Everything that connects to
`lai_postgres_main`: the Step 6 embed job (PID 3465973, ~14-day run),
the migration topup daemon, the DDiQ `lai-backend`/`lai-worker`
containers, and serve_rag. All need the new password in their env and a
coordinated restart.

**Remediation path (when scheduled):**
1. Rotate the `lai_postgres_main` role password to a strong generated
   secret (DB owner: rj/sa).
2. Set it via env for every consumer — `PGPASSWORD` for the
   `core.config` pipeline, `DB_PASSWORD` in `micro-services/.env`, and
   serve_rag's env.
3. Restart consumers (coordinate with the Step 6 run — ideally at a
   checkpoint boundary).
4. **Then** land the code change: drop the hardcoded defaults in
   `core/config.py` (PG + MinIO) so the field is required and fails
   closed — matching the microservice compose's
   `DB_PASSWORD:?Set DB_PASSWORD in .env` pattern. The fail-closed code
   change is prepared to land *with* the rotation, not before.

**Interim mitigation.** None applied — the password remains the
committed default. Tracked here pending the rotation window.
