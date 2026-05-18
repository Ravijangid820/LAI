-- ============================================================================
-- Migration 001 — DOWN
-- ----------------------------------------------------------------------------
-- Reverses 001_auth_and_tenant_isolation.up.sql. Drops the new auth
-- tables and removes the user_id column + FK + index from each DDiQ
-- table. Idempotent: safe to re-run.
--
-- WARNING: This destroys all user accounts, refresh tokens, password
-- reset tokens, conversations, and messages. Existing DDiQ rows are
-- preserved (user_id is removed, not the rows themselves).
-- ============================================================================

BEGIN;

-- ── DDiQ tables: drop FK + index + column ────────────────────────────────────
ALTER TABLE ddiq_documents          DROP CONSTRAINT IF EXISTS fk_ddiq_documents_user;
ALTER TABLE ddiq_reports            DROP CONSTRAINT IF EXISTS fk_ddiq_reports_user;
ALTER TABLE ddiq_project_areas      DROP CONSTRAINT IF EXISTS fk_ddiq_project_areas_user;
ALTER TABLE ddiq_contracts          DROP CONSTRAINT IF EXISTS fk_ddiq_contracts_user;
ALTER TABLE ddiq_classified_parcels DROP CONSTRAINT IF EXISTS fk_ddiq_classified_parcels_user;

DROP INDEX IF EXISTS ddiq_documents_user_idx;
DROP INDEX IF EXISTS ddiq_reports_user_idx;
DROP INDEX IF EXISTS ddiq_project_areas_user_idx;
DROP INDEX IF EXISTS ddiq_contracts_user_idx;
DROP INDEX IF EXISTS ddiq_classified_parcels_user_idx;

ALTER TABLE ddiq_documents          DROP COLUMN IF EXISTS user_id;
ALTER TABLE ddiq_reports            DROP COLUMN IF EXISTS user_id;
ALTER TABLE ddiq_project_areas      DROP COLUMN IF EXISTS user_id;
ALTER TABLE ddiq_contracts          DROP COLUMN IF EXISTS user_id;
ALTER TABLE ddiq_classified_parcels DROP COLUMN IF EXISTS user_id;

-- ── auth tables ──────────────────────────────────────────────────────────────
DROP INDEX IF EXISTS messages_conv_idx;
DROP TABLE IF EXISTS messages;

DROP INDEX IF EXISTS conversations_user_idx;
DROP TABLE IF EXISTS conversations;

DROP INDEX IF EXISTS password_reset_tokens_user_idx;
DROP TABLE IF EXISTS password_reset_tokens;

DROP INDEX IF EXISTS refresh_tokens_user_idx;
DROP TABLE IF EXISTS refresh_tokens;

DROP INDEX IF EXISTS users_email_canonical_idx;
DROP TABLE IF EXISTS users;

COMMIT;
