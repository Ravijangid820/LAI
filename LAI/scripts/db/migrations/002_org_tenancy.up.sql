-- ============================================================================
-- Migration 002 — Firm (organization) tenancy: identity + scoping columns
-- ----------------------------------------------------------------------------
-- See harsh/MULTIUSER_PLAN.md for the design. This is the Phase A migration:
-- it is **purely additive** (new tables + nullable columns + backfill). The
-- isolation key flips from user_id → org_id in the application code (Phase B);
-- the NOT NULL + FK *lockdown* on resource tables is deliberately deferred to
-- a later migration (Phase E) so that the window between deploying this
-- migration and the Phase B code — during which inserts still write only
-- user_id — does not violate a NOT NULL org_id constraint.
--
-- New tables:
--   organizations     the firm; the tenant boundary
--   projects          server-side replacement for the localStorage-only FE
--                     project grouping; org-scoped, with an ethical-wall mode
--   project_members   per-matter ethical wall (consulted only when
--                     projects.access = 'restricted')
--
-- Column additions:
--   users                       ADD org_id UUID  (nullable; open signup means
--                               org-less users are valid — never locked down)
--   ddiq_documents, ddiq_reports,
--   ddiq_project_areas, ddiq_contracts,
--   ddiq_classified_parcels     ADD org_id UUID  (nullable here; lockdown later)
--   ddiq_documents, ddiq_reports ADD project_id UUID
--
-- Backfill: a single legacy org owns every pre-existing user; each DDiQ row's
-- org_id is derived from its owner's org_id. No data is lost; existing rows
-- stay queryable and the smoke-test report remains retrievable.
--
-- Idempotent. Safe to re-run; uses IF NOT EXISTS / IF EXISTS / ON CONFLICT.
-- ============================================================================

BEGIN;

-- ── Extensions ───────────────────────────────────────────────────────────────
-- pg_trgm backs the admin member-search typeahead (MULTIUSER_PLAN.md §7.1):
-- GIN trigram indexes make substring ILIKE on name/email index-accelerated
-- instead of a sequential scan.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── organizations (the firm = the tenant boundary) ───────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT organizations_status_chk CHECK (status IN ('active', 'disabled'))
);

-- The legacy org owns all pre-multi-user accounts and their data, so existing
-- users keep seeing their matters (and now see each other's, within this one
-- org). Fixed UUID so the backfill + a re-run resolve to the same row.
INSERT INTO organizations (id, name, status)
VALUES (
    '00000000-0000-0000-0000-0000000000a1',
    'LAI Legacy (pre-multi-user)',
    'active'
)
ON CONFLICT (id) DO NOTHING;

-- ── users.org_id ─────────────────────────────────────────────────────────────
-- Nullable forever: open signup creates org-less users who are placed into a
-- firm later by an admin (MULTIUSER_PLAN.md §7). Never SET NOT NULL.
ALTER TABLE users ADD COLUMN IF NOT EXISTS org_id UUID;

CREATE INDEX IF NOT EXISTS users_org_idx ON users (org_id);

-- Backfill: every account that predates multi-user joins the legacy org.
UPDATE users SET org_id = '00000000-0000-0000-0000-0000000000a1' WHERE org_id IS NULL;

-- FK after backfill (nullable FK — org-less users allowed).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'users' AND constraint_name = 'fk_users_org'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT fk_users_org FOREIGN KEY (org_id) REFERENCES organizations (id);
    END IF;
END$$;

-- Trigram indexes for admin member search (§7.1).
CREATE INDEX IF NOT EXISTS users_full_name_trgm
    ON users USING gin (lower(full_name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS users_email_trgm
    ON users USING gin (email_canonical gin_trgm_ops);

-- ── projects (server-side matter grouping; replaces localStorage) ────────────
CREATE TABLE IF NOT EXISTS projects (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations (id),
    created_by  UUID REFERENCES users (id),   -- attribution, not access
    name        TEXT NOT NULL,
    bundesland  TEXT,                          -- 2-char ISO; aligns with guide §6
    -- 'open'  → visible to the whole org (default)
    -- 'restricted' → only project_members + firm admins (the ethical wall, §7.2)
    access      TEXT NOT NULL DEFAULT 'open',
    archived    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT projects_access_chk CHECK (access IN ('open', 'restricted'))
);
CREATE INDEX IF NOT EXISTS projects_org_idx ON projects (org_id, updated_at DESC);

-- ── project_members (the ethical wall — consulted only when restricted) ──────
CREATE TABLE IF NOT EXISTS project_members (
    project_id  UUID NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    added_by    UUID REFERENCES users (id),    -- who placed them (audit)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS project_members_user_idx ON project_members (user_id);

-- ── DDiQ tables: add nullable org_id (+ project_id on the artifact tables) ───
ALTER TABLE ddiq_documents          ADD COLUMN IF NOT EXISTS org_id UUID;
ALTER TABLE ddiq_reports            ADD COLUMN IF NOT EXISTS org_id UUID;
ALTER TABLE ddiq_project_areas      ADD COLUMN IF NOT EXISTS org_id UUID;
ALTER TABLE ddiq_contracts          ADD COLUMN IF NOT EXISTS org_id UUID;
ALTER TABLE ddiq_classified_parcels ADD COLUMN IF NOT EXISTS org_id UUID;

ALTER TABLE ddiq_documents ADD COLUMN IF NOT EXISTS project_id UUID;
ALTER TABLE ddiq_reports   ADD COLUMN IF NOT EXISTS project_id UUID;

CREATE INDEX IF NOT EXISTS ddiq_documents_org_idx          ON ddiq_documents          (org_id);
CREATE INDEX IF NOT EXISTS ddiq_reports_org_idx            ON ddiq_reports            (org_id);
CREATE INDEX IF NOT EXISTS ddiq_project_areas_org_idx      ON ddiq_project_areas      (org_id);
CREATE INDEX IF NOT EXISTS ddiq_contracts_org_idx          ON ddiq_contracts          (org_id);
CREATE INDEX IF NOT EXISTS ddiq_classified_parcels_org_idx ON ddiq_classified_parcels (org_id);
CREATE INDEX IF NOT EXISTS ddiq_documents_project_idx      ON ddiq_documents          (project_id);
CREATE INDEX IF NOT EXISTS ddiq_reports_project_idx        ON ddiq_reports            (project_id);

-- Backfill org_id from the owning user's org (migration 001 already set
-- ddiq_*.user_id NOT NULL, and the UPDATE above gave every user an org).
UPDATE ddiq_documents          d SET org_id = u.org_id FROM users u WHERE d.user_id = u.id AND d.org_id IS NULL;
UPDATE ddiq_reports            d SET org_id = u.org_id FROM users u WHERE d.user_id = u.id AND d.org_id IS NULL;
UPDATE ddiq_project_areas      d SET org_id = u.org_id FROM users u WHERE d.user_id = u.id AND d.org_id IS NULL;
UPDATE ddiq_contracts          d SET org_id = u.org_id FROM users u WHERE d.user_id = u.id AND d.org_id IS NULL;
UPDATE ddiq_classified_parcels d SET org_id = u.org_id FROM users u WHERE d.user_id = u.id AND d.org_id IS NULL;

-- FK org_id → organizations on each DDiQ table (nullable until Phase E lockdown).
DO $$
DECLARE
    pair RECORD;
BEGIN
    FOR pair IN SELECT * FROM (VALUES
        ('ddiq_documents',          'fk_ddiq_documents_org'),
        ('ddiq_reports',            'fk_ddiq_reports_org'),
        ('ddiq_project_areas',      'fk_ddiq_project_areas_org'),
        ('ddiq_contracts',          'fk_ddiq_contracts_org'),
        ('ddiq_classified_parcels', 'fk_ddiq_classified_parcels_org')
    ) AS t(table_name, constraint_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_name = pair.table_name AND constraint_name = pair.constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (org_id) REFERENCES organizations(id)',
                pair.table_name, pair.constraint_name
            );
        END IF;
    END LOOP;
END$$;

-- project_id → projects FK on the two artifact tables (nullable).
DO $$
DECLARE
    pair RECORD;
BEGIN
    FOR pair IN SELECT * FROM (VALUES
        ('ddiq_documents', 'fk_ddiq_documents_project'),
        ('ddiq_reports',   'fk_ddiq_reports_project')
    ) AS t(table_name, constraint_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE table_name = pair.table_name AND constraint_name = pair.constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL',
                pair.table_name, pair.constraint_name
            );
        END IF;
    END LOOP;
END$$;

COMMIT;
