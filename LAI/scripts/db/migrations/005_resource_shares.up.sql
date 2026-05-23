-- ============================================================================
-- Migration 005 — Per-resource view-only shares (Path A Step 2)
-- ----------------------------------------------------------------------------
-- Phase B firm-wide visibility was reverted to private-by-default in Step 1.
-- This migration adds the EXPLICIT-SHARE layer on top: an owner can grant
-- view access to specific colleagues (same-org only — enforced in the route
-- layer, not the schema, so the schema stays generic).
--
-- Two tables, one per shareable DDiQ resource:
--
--   ddiq_report_shares     — share a generated DDiQ report
--   ddiq_document_shares   — share an uploaded source document
--
-- (Chat session sharing lives in SQLite alongside the sessions table; see
-- ``session_shares`` in persistence.py.)
--
-- v1 semantics: a share grants READ access only. Owner-only operations
-- (delete, re-share) are enforced in the route layer — the schema imposes
-- no row-level write protection beyond the existing user_id check.
--
-- ON DELETE CASCADE on both ``resource_id`` and ``user_id``: when the
-- underlying resource is deleted, its share rows go with it; when a user
-- account is deleted, all shares granted to them disappear.
--
-- Idempotent. Safe to re-run.
-- ============================================================================

BEGIN;

-- ── ddiq_report_shares ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ddiq_report_shares (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id   UUID NOT NULL REFERENCES ddiq_reports (id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    granted_by  UUID NOT NULL REFERENCES users (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ddiq_report_shares_uq UNIQUE (report_id, user_id)
);

-- "What reports are shared with me?" — drives the report list-widening.
CREATE INDEX IF NOT EXISTS ddiq_report_shares_user_idx
    ON ddiq_report_shares (user_id);
-- "Who has access to this report?" — drives the share-list endpoint.
CREATE INDEX IF NOT EXISTS ddiq_report_shares_report_idx
    ON ddiq_report_shares (report_id);

-- ── ddiq_document_shares ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ddiq_document_shares (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES ddiq_documents (id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    granted_by  UUID NOT NULL REFERENCES users (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ddiq_document_shares_uq UNIQUE (document_id, user_id)
);

CREATE INDEX IF NOT EXISTS ddiq_document_shares_user_idx
    ON ddiq_document_shares (user_id);
CREATE INDEX IF NOT EXISTS ddiq_document_shares_document_idx
    ON ddiq_document_shares (document_id);

COMMIT;
