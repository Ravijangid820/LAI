-- ============================================================================
-- Migration 002 — DOWN
-- ----------------------------------------------------------------------------
-- Reverses 002_org_tenancy.up.sql. Drops the org/project tables and removes
-- the org_id / project_id columns (+ their FKs and indexes) from users and the
-- DDiQ tables. Idempotent: safe to re-run.
--
-- WARNING: destroys all organizations, projects, and project memberships.
-- Existing users and DDiQ rows are preserved (only the org_id/project_id
-- columns are removed, not the rows). The pg_trgm extension is left in place
-- (other features may rely on it; dropping it is not reversible-by-data).
-- ============================================================================

BEGIN;

-- ── DDiQ tables: drop project_id / org_id FKs, indexes, columns ──────────────
ALTER TABLE ddiq_documents DROP CONSTRAINT IF EXISTS fk_ddiq_documents_project;
ALTER TABLE ddiq_reports   DROP CONSTRAINT IF EXISTS fk_ddiq_reports_project;

ALTER TABLE ddiq_documents          DROP CONSTRAINT IF EXISTS fk_ddiq_documents_org;
ALTER TABLE ddiq_reports            DROP CONSTRAINT IF EXISTS fk_ddiq_reports_org;
ALTER TABLE ddiq_project_areas      DROP CONSTRAINT IF EXISTS fk_ddiq_project_areas_org;
ALTER TABLE ddiq_contracts          DROP CONSTRAINT IF EXISTS fk_ddiq_contracts_org;
ALTER TABLE ddiq_classified_parcels DROP CONSTRAINT IF EXISTS fk_ddiq_classified_parcels_org;

DROP INDEX IF EXISTS ddiq_documents_project_idx;
DROP INDEX IF EXISTS ddiq_reports_project_idx;
DROP INDEX IF EXISTS ddiq_documents_org_idx;
DROP INDEX IF EXISTS ddiq_reports_org_idx;
DROP INDEX IF EXISTS ddiq_project_areas_org_idx;
DROP INDEX IF EXISTS ddiq_contracts_org_idx;
DROP INDEX IF EXISTS ddiq_classified_parcels_org_idx;

ALTER TABLE ddiq_documents          DROP COLUMN IF EXISTS project_id;
ALTER TABLE ddiq_reports            DROP COLUMN IF EXISTS project_id;
ALTER TABLE ddiq_documents          DROP COLUMN IF EXISTS org_id;
ALTER TABLE ddiq_reports            DROP COLUMN IF EXISTS org_id;
ALTER TABLE ddiq_project_areas      DROP COLUMN IF EXISTS org_id;
ALTER TABLE ddiq_contracts          DROP COLUMN IF EXISTS org_id;
ALTER TABLE ddiq_classified_parcels DROP COLUMN IF EXISTS org_id;

-- ── project tables ───────────────────────────────────────────────────────────
DROP INDEX IF EXISTS project_members_user_idx;
DROP TABLE IF EXISTS project_members;

DROP INDEX IF EXISTS projects_org_idx;
DROP TABLE IF EXISTS projects;

-- ── users.org_id ─────────────────────────────────────────────────────────────
ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_org;
DROP INDEX IF EXISTS users_email_trgm;
DROP INDEX IF EXISTS users_full_name_trgm;
DROP INDEX IF EXISTS users_org_idx;
ALTER TABLE users DROP COLUMN IF EXISTS org_id;

-- ── organizations ────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS organizations;

COMMIT;
