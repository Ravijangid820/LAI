-- ============================================================================
-- Migration 004 — DOWN
-- ----------------------------------------------------------------------------
-- Drops the org_invitations table and its indexes. Idempotent.
-- WARNING: destroys every pending invitation. Accepted invitations have
-- already produced user rows, so user accounts are NOT affected.
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS org_invitations_email_idx;
DROP INDEX IF EXISTS org_invitations_org_idx;
DROP INDEX IF EXISTS org_invitations_outstanding_idx;
DROP TABLE IF EXISTS org_invitations;

COMMIT;
