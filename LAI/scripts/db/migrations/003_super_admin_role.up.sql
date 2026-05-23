-- ============================================================================
-- Migration 003 — Add `super_admin` to users.role
-- ----------------------------------------------------------------------------
-- Phase C (MULTIUSER_PLAN.md §10.4) introduces a platform-level role above the
-- firm admin:
--
--   user         — regular member of one org
--   admin        — firm admin (manages members of *their* org only)
--   super_admin  — platform admin (creates orgs, assigns firm admins, has
--                  cross-org visibility for support; only a super_admin can
--                  ever create another super_admin)
--
-- Migration 001 created the role CHECK as ``role IN ('user','admin')``. This
-- migration widens it to allow ``'super_admin'`` so the role can be persisted.
-- The application-side ``is_super_admin`` / ``require_super_admin`` checks
-- (added in this phase) are what actually GATE platform-level actions; the
-- DB constraint is just an invariant on the column.
--
-- Idempotent: ``IF EXISTS`` on the drop, and the ADD CONSTRAINT skips when an
-- equivalent constraint is already present.
-- ============================================================================

BEGIN;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_chk;

ALTER TABLE users
    ADD CONSTRAINT users_role_chk
    CHECK (role = ANY (ARRAY['user'::text, 'admin'::text, 'super_admin'::text]));

COMMIT;
