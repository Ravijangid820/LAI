-- ============================================================================
-- Migration 007 — Statute feed state + id sequence (Phase 4.3 Phase B)
-- ----------------------------------------------------------------------------
-- Supports the daily gesetze-im-internet.de ingestion feed.
--
-- ``statute_feed_state`` records a content hash per law so the feed can detect
-- new / changed / removed statutes — the existing corpus ``ON CONFLICT DO
-- NOTHING`` ingest path silently skips amendments, which would leave the corpus
-- stale the moment a law is amended.
--
-- ``corpus_feed_id_seq`` provisions ids for feed-inserted corpus_* rows. Those
-- PKs are supplied BIGINTs with no DEFAULT (the corpus was bulk-copied from
-- SQLite, max id ~50M). Starting the sequence at 9,000,000,000 leaves that
-- id space untouched, so feed rows can never collide with migrated rows.
--
-- Additive + idempotent. Safe to re-run.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS statute_feed_state (
    slug          TEXT PRIMARY KEY,            -- gesetze-im-internet.de URL slug, e.g. "bimschg"
    source_url    TEXT NOT NULL,               -- absolute xml.zip URL
    jurabk        TEXT,                         -- citable abbreviation, e.g. "BImSchG"
    doc_id        TEXT NOT NULL,                -- corpus doc_id (hash of slug) linking the corpus_* rows
    content_hash  TEXT NOT NULL,                -- sha256 of the parsed law text; the change-detection key
    domain        TEXT,                         -- assigned category (lai.common.connectors.statute_categories)
    n_sections    INTEGER,                      -- parsed section count (observability)
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),  -- last run that saw this law in the TOC
    last_changed  TIMESTAMPTZ NOT NULL DEFAULT NOW()   -- last run that re-ingested (hash changed)
);

CREATE INDEX IF NOT EXISTS statute_feed_state_doc_id_idx ON statute_feed_state (doc_id);

-- Id source for feed-inserted corpus_parent_chunks / corpus_child_chunks rows.
CREATE SEQUENCE IF NOT EXISTS corpus_feed_id_seq START WITH 9000000000;

COMMIT;
