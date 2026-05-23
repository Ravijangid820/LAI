-- ============================================================================
-- Migration 004 — org_invitations table (Phase C.1)
-- ----------------------------------------------------------------------------
-- MULTIUSER_PLAN §7 / Phase C.1: admins invite by email. An invitation is a
-- single-use, time-limited token (sha256-hashed at rest, same primitive as
-- ``password_reset_tokens``) that maps an email → org. On accept, the
-- recipient sets their own ``full_name`` + ``password`` and is created as a
-- user already inside the inviting org. We never email plaintext passwords.
--
-- Schema mirrors password_reset_tokens deliberately so the existing
-- ResetTokenRepository pattern transfers cleanly:
--
--   id              uuid pk
--   org_id          fk organizations(id) ON DELETE CASCADE
--   email_canonical lowercased/trimmed at insert (matches users.email_canonical)
--   role            'user' | 'admin'   (super_admin grants stay super-only)
--   invited_by      fk users(id)        (audit)
--   token_hash      sha256 hex, unique  (never store the raw token)
--   expires_at      timestamptz
--   accepted_at     timestamptz | null  (single-use marker)
--   created_at      timestamptz
--
-- Idempotent. Safe to re-run.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS org_invitations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    email_canonical  TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'user',
    invited_by       UUID REFERENCES users (id),
    token_hash       TEXT NOT NULL UNIQUE,
    expires_at       TIMESTAMPTZ NOT NULL,
    accepted_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT org_invitations_role_chk CHECK (role IN ('user', 'admin'))
);

-- One outstanding invitation per (org, email). Re-invite refreshes the token
-- in place rather than duplicating; accepted/old rows are excluded from the
-- uniqueness so the audit trail is preserved.
CREATE UNIQUE INDEX IF NOT EXISTS org_invitations_outstanding_idx
    ON org_invitations (org_id, email_canonical)
    WHERE accepted_at IS NULL;

CREATE INDEX IF NOT EXISTS org_invitations_org_idx
    ON org_invitations (org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS org_invitations_email_idx
    ON org_invitations (email_canonical);

COMMIT;
