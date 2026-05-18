-- ============================================================================
-- Migration 001 — Auth identity tables + tenant_id columns on DDiQ tables
-- ----------------------------------------------------------------------------
-- See LAI/harsh/AUTH_PLAN.md §3 for the design rationale.
--
-- Five new tables:
--   users                   identity, password hash, role, status
--   refresh_tokens          opaque-token store (sha256 hashed) for sessions
--   password_reset_tokens   one-shot, 30-min TTL
--   conversations           replaces in-memory session_id dicts in api.py
--   messages                child of conversations
--
-- Migrations on existing DDiQ tables:
--   ddiq_documents, ddiq_reports, ddiq_project_areas,
--   ddiq_contracts, ddiq_classified_parcels
--     ADD COLUMN user_id UUID  (nullable in this migration)
--     index on (user_id)
--
-- A legacy account (legacy@lai.local) owns existing demo rows so smoke
-- tests still pass. Lockdown to NOT NULL + FK happens in migration
-- 002 once 001 has been applied and the application code is writing
-- user_id on every insert.
--
-- Idempotent. Safe to re-run; uses ``IF NOT EXISTS`` / ``IF EXISTS``.
-- ============================================================================

BEGIN;

-- ── Extensions ───────────────────────────────────────────────────────────────
-- gen_random_uuid() ships with pgcrypto. Required for the UUID defaults
-- below and already enabled in production, but make the dependency
-- explicit so a freshly-created database can apply this migration.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── identity ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL,
    email_canonical     TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    full_name           TEXT NOT NULL,
    company             TEXT,
    role                TEXT NOT NULL DEFAULT 'user',
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at       TIMESTAMPTZ,
    CONSTRAINT users_role_chk   CHECK (role IN ('user', 'admin')),
    CONSTRAINT users_status_chk CHECK (status IN ('active', 'disabled'))
);
CREATE INDEX IF NOT EXISTS users_email_canonical_idx
    ON users (email_canonical);

-- ── refresh tokens (simple revocation; no rotation chain in v1) ──────────────
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS refresh_tokens_user_idx
    ON refresh_tokens (user_id, revoked_at)
    WHERE revoked_at IS NULL;

-- ── one-shot password reset tokens ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS password_reset_tokens_user_idx
    ON password_reset_tokens (user_id);

-- ── conversations / messages (replaces in-memory session_id dicts) ───────────
CREATE TABLE IF NOT EXISTS conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title           TEXT,
    pinned_facts    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS conversations_user_idx
    ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id  UUID NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    citations        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT messages_role_chk CHECK (role IN ('user', 'assistant', 'system'))
);
CREATE INDEX IF NOT EXISTS messages_conv_idx
    ON messages (conversation_id, created_at);

-- ── DDiQ tables: add nullable user_id columns + per-table indexes ────────────
-- Nullable first so existing rows remain queryable while the backfill
-- runs. Migration 002 flips these to NOT NULL once application code
-- and the backfill below have populated every row.
ALTER TABLE ddiq_documents          ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE ddiq_reports            ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE ddiq_project_areas      ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE ddiq_contracts          ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE ddiq_classified_parcels ADD COLUMN IF NOT EXISTS user_id UUID;

CREATE INDEX IF NOT EXISTS ddiq_documents_user_idx          ON ddiq_documents          (user_id);
CREATE INDEX IF NOT EXISTS ddiq_reports_user_idx            ON ddiq_reports            (user_id);
CREATE INDEX IF NOT EXISTS ddiq_project_areas_user_idx      ON ddiq_project_areas      (user_id);
CREATE INDEX IF NOT EXISTS ddiq_contracts_user_idx          ON ddiq_contracts          (user_id);
CREATE INDEX IF NOT EXISTS ddiq_classified_parcels_user_idx ON ddiq_classified_parcels (user_id);

-- ── legacy account: owns the pre-auth demo rows ──────────────────────────────
-- Password hash is the bcrypt of a randomly-generated rotation key that
-- is *not* documented here on purpose — the legacy account is not meant
-- to be logged in to. If you need to revive it, reset the password via
-- the /auth/forgot-password flow against legacy@lai.local. The status
-- is intentionally 'disabled' so a forgotten admin cannot accidentally
-- log in as the legacy owner.
INSERT INTO users (
    id,
    email,
    email_canonical,
    password_hash,
    full_name,
    role,
    status
)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'legacy@lai.local',
    'legacy@lai.local',
    -- bcrypt of a 32-byte random string generated at migration write time;
    -- never to be reused. Bypass with status='disabled' (login refuses).
    '$2b$12$0bp4cMTjU3oLW9k3Gx6mGuk5Tt9.cgEy1QFW.uTPmBl6.yc1cIfwG',
    'Legacy demo data',
    'user',
    'disabled'
)
ON CONFLICT (email_canonical) DO NOTHING;

-- ── backfill: assign existing rows to the legacy account ─────────────────────
UPDATE ddiq_documents          SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;
UPDATE ddiq_reports            SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;
UPDATE ddiq_project_areas      SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;
UPDATE ddiq_contracts          SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;
UPDATE ddiq_classified_parcels SET user_id = '00000000-0000-0000-0000-000000000001' WHERE user_id IS NULL;

-- ── lockdown: NOT NULL + FK ──────────────────────────────────────────────────
ALTER TABLE ddiq_documents          ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE ddiq_reports            ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE ddiq_project_areas      ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE ddiq_contracts          ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE ddiq_classified_parcels ALTER COLUMN user_id SET NOT NULL;

-- PostgreSQL has no ``ADD CONSTRAINT IF NOT EXISTS``; emulate it via
-- a DO block so re-running this migration is safe.
DO $$
DECLARE
    pair RECORD;
BEGIN
    FOR pair IN SELECT * FROM (VALUES
        ('ddiq_documents',          'fk_ddiq_documents_user'),
        ('ddiq_reports',            'fk_ddiq_reports_user'),
        ('ddiq_project_areas',      'fk_ddiq_project_areas_user'),
        ('ddiq_contracts',          'fk_ddiq_contracts_user'),
        ('ddiq_classified_parcels', 'fk_ddiq_classified_parcels_user')
    ) AS t(table_name, constraint_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.table_constraints
            WHERE table_name = pair.table_name
              AND constraint_name = pair.constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (user_id) REFERENCES users(id)',
                pair.table_name,
                pair.constraint_name
            );
        END IF;
    END LOOP;
END$$;

COMMIT;
