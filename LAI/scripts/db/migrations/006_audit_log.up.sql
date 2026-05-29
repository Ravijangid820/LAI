-- ============================================================================
-- Migration 006 — Append-only audit log
-- ----------------------------------------------------------------------------
-- A single, queryable trail of security- and compliance-relevant actions
-- across the platform (login, query, upload, report, export, ...). It lives in
-- the shared ``lai_db`` Postgres so every component writes to ONE table:
-- auth_router (asyncpg), serve_rag (psycopg2), and the DDiQ worker (psycopg2).
--
-- Append-only: a BEFORE UPDATE trigger rejects mutations, so persisted records
-- cannot be silently altered (tamper-evidence). DELETE is intentionally NOT
-- blocked — that is left to a privileged retention job (EU AI Act Art. 12
-- minimum retention is 6 months; longer is a policy choice).
--
-- ``user_id`` / ``org_id`` are nullable: failed logins and anonymous probes
-- have no resolved principal. ON DELETE SET NULL keeps the audit row even if
-- the account is later removed — the trail outlives the account.
--
-- Idempotent. Safe to re-run.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id     UUID REFERENCES users (id) ON DELETE SET NULL,
    org_id      UUID REFERENCES organizations (id) ON DELETE SET NULL,
    action      TEXT NOT NULL,                       -- login | query | upload | report | export | ...
    outcome     TEXT NOT NULL DEFAULT 'success',     -- success | failure | denied | ...
    session_id  TEXT,                                -- serve_rag session uuid / DDiQ report id / NULL
    latency_ms  INTEGER,                             -- request latency where measured
    detail      JSONB                                -- extra context (filename, doc_index, mode, error, ...)
);

-- Recent-first listing + the common admin filters (by principal, org, action).
CREATE INDEX IF NOT EXISTS audit_log_ts_idx        ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_user_ts_idx   ON audit_log (user_id, ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_org_ts_idx    ON audit_log (org_id, ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_action_ts_idx ON audit_log (action, ts DESC);

-- Append-only enforcement: reject UPDATE so persisted records are immutable.
CREATE OR REPLACE FUNCTION audit_log_no_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only; UPDATE is not permitted';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_no_update_trg ON audit_log;
CREATE TRIGGER audit_log_no_update_trg
    BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_no_update();

COMMIT;
