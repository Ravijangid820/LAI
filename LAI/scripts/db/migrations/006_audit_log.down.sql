-- ============================================================================
-- Migration 006 (down) — drop the append-only audit log
-- ============================================================================

BEGIN;

DROP TRIGGER IF EXISTS audit_log_no_update_trg ON audit_log;
DROP FUNCTION IF EXISTS audit_log_no_update();
DROP TABLE IF EXISTS audit_log;

COMMIT;
