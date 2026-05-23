-- ============================================================================
-- Migration 005 — DOWN
-- ----------------------------------------------------------------------------
-- Drops the per-resource share tables. Idempotent.
-- WARNING: destroys every grant — recipients lose access immediately.
-- ============================================================================

BEGIN;

DROP INDEX IF EXISTS ddiq_document_shares_document_idx;
DROP INDEX IF EXISTS ddiq_document_shares_user_idx;
DROP TABLE IF EXISTS ddiq_document_shares;

DROP INDEX IF EXISTS ddiq_report_shares_report_idx;
DROP INDEX IF EXISTS ddiq_report_shares_user_idx;
DROP TABLE IF EXISTS ddiq_report_shares;

COMMIT;
