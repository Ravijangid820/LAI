-- =============================================================================
-- Corpus migration to pgvector — schema (Phase 1b Track B)
-- =============================================================================
-- Replaces the SQLite in-RAM mat-mul retrieval (lai.search.eval) with pgvector
-- + halfvec(4096) + HNSW. See harsh/TRACK_B_TIMING.md for the rationale and
-- the timing math.
--
-- Run via:  migrate_corpus.py init
-- (which executes this file inside a single transaction).
--
-- Idempotent — every statement uses IF NOT EXISTS so re-running on an
-- already-initialised DB is a no-op.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ── parent_chunks (text-only; ~13.8 M rows expected) ──────────────────────
-- Mirror of the SQLite parent_chunks table. Parent text is the unit RAG
-- returns to the LLM; child chunks carry the embedding that points back here.
CREATE TABLE IF NOT EXISTS corpus_parent_chunks (
    id            BIGINT PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    chunk_id      TEXT,
    section       TEXT,
    content       TEXT NOT NULL,
    char_count    INTEGER,
    language      TEXT,
    doc_type      TEXT,
    source_file   TEXT,
    source_bucket TEXT,
    domain        TEXT,
    page_start    INTEGER,
    page_end      INTEGER,
    metadata      JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS corpus_parent_doc_id_idx    ON corpus_parent_chunks(doc_id);
CREATE INDEX IF NOT EXISTS corpus_parent_doc_type_idx  ON corpus_parent_chunks(doc_type);

-- ── child_chunks (text + embedding; ~50 M rows when Step 6 completes) ────
-- ``embedding halfvec(4000)`` = fp16 in pgvector, truncated to 4000-d.
--
-- Why 4000 not the full 4096 that Qwen3-Embedding emits: pgvector caps
-- HNSW indexes at 4000 dimensions for halfvec (2000 for vector). Qwen3-
-- Embedding uses Matryoshka representation learning — the model is
-- trained to be truncatable to any prefix length without recall loss
-- (per Qwen's HuggingFace model card). The migrate_corpus.py loader
-- slices ``[:4000]`` on the fp16 array before insert. Index built on
-- the same column, in a tablespace on the overlay disk so /data doesn't
-- balloon.
--
-- NOT NULL because every row we migrate already has an embedding by
-- definition (we filter on child_embeddings JOIN).
CREATE TABLE IF NOT EXISTS corpus_child_chunks (
    id          BIGINT PRIMARY KEY,
    parent_id   BIGINT REFERENCES corpus_parent_chunks(id) ON DELETE CASCADE,
    chunk_id    TEXT,
    content     TEXT NOT NULL,
    embedding   halfvec(4000) NOT NULL,
    char_count  INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS corpus_child_parent_idx ON corpus_child_chunks(parent_id);
-- NOTE: HNSW index on ``embedding`` is INTENTIONALLY NOT created here. It is
-- built post-migration via ``migrate_corpus.py build-index`` because creating
-- it up-front would slow the bulk load by 5-10×. See the build-index path for
-- the tuning knobs.

-- ── migration state (singleton row) ───────────────────────────────────────
-- Tracks high-water mark for the children migration + the topup daemon so a
-- restart resumes from where it left off. Singleton enforced via CHECK
-- constraint — exactly one row, id always = 1.
CREATE TABLE IF NOT EXISTS corpus_migration_state (
    id                SMALLINT PRIMARY KEY DEFAULT 1,
    last_child_id     BIGINT NOT NULL DEFAULT 0,   -- high-water mark for children
    parents_loaded    BIGINT NOT NULL DEFAULT 0,   -- cumulative count
    children_loaded   BIGINT NOT NULL DEFAULT 0,   -- cumulative count
    parents_started_at   TIMESTAMPTZ,
    parents_finished_at  TIMESTAMPTZ,
    children_started_at  TIMESTAMPTZ,
    children_finished_at TIMESTAMPTZ,
    index_started_at     TIMESTAMPTZ,
    index_finished_at    TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT singleton_row CHECK (id = 1)
);
INSERT INTO corpus_migration_state (id)
    VALUES (1)
    ON CONFLICT (id) DO NOTHING;
