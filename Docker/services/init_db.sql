-- =============================================================================
-- LAIV4 RAG Test Database Initialization
-- Runs on first PostgreSQL container startup (profile "db" only)
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Trigger function: auto-generate tsvector on insert/update
CREATE OR REPLACE FUNCTION chunks_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('german', COALESCE(NEW.text_clean, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Chunks table (matches existing lai_postgres schema)
CREATE TABLE IF NOT EXISTS chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     TEXT NOT NULL,
    text_clean      TEXT NOT NULL,
    text_tagged     TEXT,
    content_hash    TEXT NOT NULL UNIQUE,
    section         TEXT,
    chunk_index     INTEGER DEFAULT 0,
    law_refs        TEXT[],
    entities        JSONB DEFAULT '{}'::JSONB,
    effective_date  DATE,
    doc_type        TEXT DEFAULT 'other',
    court_level     INTEGER,
    jurisdiction    TEXT,
    embedding       VECTOR(1024),
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    user_id         UUID,
    search_vector   TSVECTOR,
    subsection      TEXT,
    page_start      INTEGER,
    page_end        INTEGER,
    paragraph_refs  TEXT[],
    article_refs    TEXT[],
    is_current      BOOLEAN DEFAULT TRUE,
    decision_date   DATE,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'::JSONB
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_chunks_document_id     ON chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash    ON chunks (content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_type        ON chunks (doc_type);
CREATE INDEX IF NOT EXISTS idx_chunks_effective_date  ON chunks (effective_date);
CREATE INDEX IF NOT EXISTS idx_chunks_decision_date   ON chunks (decision_date DESC);
CREATE INDEX IF NOT EXISTS idx_chunks_court_level     ON chunks (court_level);
CREATE INDEX IF NOT EXISTS idx_chunks_is_current      ON chunks (is_current) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_chunks_user_id         ON chunks (user_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user_current    ON chunks (user_id, is_current) WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_chunks_search_vector   ON chunks USING GIN (search_vector);
CREATE INDEX IF NOT EXISTS idx_chunks_paragraph_refs  ON chunks USING GIN (paragraph_refs);
CREATE INDEX IF NOT EXISTS idx_chunks_article_refs    ON chunks USING GIN (article_refs);

-- HNSW vector index for fast approximate nearest-neighbor search
-- NOTE: On large tables (>1M rows), build with CONCURRENTLY outside a transaction:
--   CREATE INDEX CONCURRENTLY idx_chunks_embedding_hnsw ON chunks
--     USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

-- Search vector auto-update trigger
DROP TRIGGER IF EXISTS chunks_search_vector_trigger ON chunks;
CREATE TRIGGER chunks_search_vector_trigger
    BEFORE INSERT OR UPDATE OF text_clean ON chunks
    FOR EACH ROW EXECUTE FUNCTION chunks_search_vector_update();

-- =============================================================================
-- LAIV4 Phase 2: Feedback / Self-Learning Tables
-- =============================================================================

-- Interactions: every query+response logged for feedback reference
CREATE TABLE IF NOT EXISTS interactions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    request_id      TEXT UNIQUE NOT NULL,
    user_id         TEXT,
    query           TEXT NOT NULL,
    response_text   TEXT,
    response_status TEXT NOT NULL,
    chunk_ids_used  UUID[],
    retrieval_quality TEXT,
    faithfulness_passed BOOLEAN,
    relevance_passed BOOLEAN,
    citations_verified BOOLEAN,
    node_timings    JSONB,
    total_tokens    INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_interactions_request_id ON interactions (request_id);
CREATE INDEX IF NOT EXISTS idx_interactions_user_id ON interactions (user_id);
CREATE INDEX IF NOT EXISTS idx_interactions_created_at ON interactions (created_at DESC);

-- Feedback: user corrections and system self-corrections
CREATE TABLE IF NOT EXISTS feedback (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    interaction_id  UUID REFERENCES interactions(id),
    feedback_type   TEXT NOT NULL,
    correction_text TEXT,
    correct_answer  TEXT,
    source          TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    actions_taken   JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feedback_interaction_id ON feedback (interaction_id);
CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback (status);
CREATE INDEX IF NOT EXISTS idx_feedback_source ON feedback (source);

-- Chunk quality scores (updated by feedback loop)
CREATE TABLE IF NOT EXISTS chunk_quality (
    chunk_id        UUID PRIMARY KEY,
    quality_score   FLOAT DEFAULT 1.0,
    feedback_count  INTEGER DEFAULT 0,
    last_feedback   TIMESTAMPTZ,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunk_quality_score ON chunk_quality (quality_score);
CREATE INDEX IF NOT EXISTS idx_chunk_quality_feedback_count ON chunk_quality (feedback_count DESC);

-- Verify
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        RAISE EXCEPTION 'pgvector extension is required but not installed';
    END IF;
    RAISE NOTICE 'LAIV4 RAG database initialized successfully (with feedback tables)';
END $$;
