-- ============================================================================
-- Migration 003 — DOWN
-- ----------------------------------------------------------------------------
-- Reverses 003_super_admin_role.up.sql. Demotes any existing super_admin to
-- admin (the CHECK constraint forbids the wider set, so they must be migrated
-- first), then restores the narrow ``('user','admin')`` constraint.
-- Idempotent: safe to re-run.
-- ============================================================================

BEGIN;

-- Demote any existing super_admin so the narrower CHECK can be re-added.
UPDATE users SET role = 'admin', updated_at = NOW() WHERE role = 'super_admin';

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_chk;

ALTER TABLE users
    ADD CONSTRAINT users_role_chk
    CHECK (role = ANY (ARRAY['user'::text, 'admin'::text]));

COMMIT;
