-- LAI Database Initialization Script
-- This script runs when PostgreSQL container starts for the first time

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create schema if needed
CREATE SCHEMA IF NOT EXISTS public;

-- Create session table for conversation memory
CREATE TABLE IF NOT EXISTS lai_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) UNIQUE NOT NULL,
    user_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create conversation history table
CREATE TABLE IF NOT EXISTS lai_conversation_history (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL REFERENCES lai_sessions(session_id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create feedback table
CREATE TABLE IF NOT EXISTS lai_feedback (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255),
    question TEXT,
    answer TEXT,
    rating VARCHAR(50),  -- 'helpful', 'not_helpful', 'partially_helpful'
    feedback_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON lai_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_session_id ON lai_conversation_history(session_id);
CREATE INDEX IF NOT EXISTS idx_feedback_session_id ON lai_feedback(session_id);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON lai_feedback(rating);

-- ============================================================
-- Data Processing Pipeline v2 Tables
-- ============================================================

-- File inventory: tracks conversion progress per raw file
CREATE TABLE IF NOT EXISTS file_inventory (
    id              BIGSERIAL PRIMARY KEY,
    file_path       TEXT NOT NULL,
    bucket_name     TEXT NOT NULL DEFAULT 'lai-raw',
    etag            TEXT,
    file_size       BIGINT,
    language        VARCHAR(10),
    doc_type        VARCHAR(50),
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    stage           VARCHAR(30) DEFAULT 'conversion',
    error_message   TEXT,
    segment_count   INT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(file_path, bucket_name)
);

CREATE INDEX IF NOT EXISTS idx_inventory_status ON file_inventory(status);
CREATE INDEX IF NOT EXISTS idx_inventory_stage ON file_inventory(stage);

-- Parent chunks: complete legal sections for fine-tuning context
CREATE TABLE IF NOT EXISTS parent_chunks (
    id              BIGSERIAL PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    chunk_id        TEXT NOT NULL UNIQUE,
    section         TEXT,
    content         TEXT NOT NULL,
    char_count      INT NOT NULL,
    language        VARCHAR(10) NOT NULL,
    doc_type        VARCHAR(50) NOT NULL,
    source_file     TEXT NOT NULL,
    source_bucket   TEXT DEFAULT 'lai-raw',
    domain          TEXT[],
    page_start      INT,
    page_end        INT,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_parent_doc_id ON parent_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_parent_doc_type ON parent_chunks(doc_type);
CREATE INDEX IF NOT EXISTS idx_parent_language ON parent_chunks(language);
CREATE INDEX IF NOT EXISTS idx_parent_domain ON parent_chunks USING gin(domain);

-- Child chunks: embedded segments for RAG retrieval
CREATE TABLE IF NOT EXISTS child_chunks (
    id              BIGSERIAL PRIMARY KEY,
    parent_id       BIGINT REFERENCES parent_chunks(id) ON DELETE CASCADE,
    chunk_id        TEXT NOT NULL UNIQUE,
    content         TEXT NOT NULL,
    context_prefix  TEXT,
    char_count      INT NOT NULL,
    embedding       vector(1024),
    search_vector   tsvector,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_child_parent ON child_chunks(parent_id);

-- Training samples: fine-tuning data in ChatML format
CREATE TABLE IF NOT EXISTS training_samples (
    id              BIGSERIAL PRIMARY KEY,
    parent_id       BIGINT REFERENCES parent_chunks(id) ON DELETE SET NULL,
    domain          TEXT,
    task_type       VARCHAR(30),
    messages        JSONB NOT NULL,
    quality_score   FLOAT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_training_domain ON training_samples(domain);
CREATE INDEX IF NOT EXISTS idx_training_task ON training_samples(task_type);

-- NOTE: HNSW and GIN indexes for child_chunks are created AFTER bulk loading.
-- Run manually after Step 6:
--   CREATE INDEX idx_child_embedding ON child_chunks
--       USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200);
--   CREATE INDEX idx_child_search ON child_chunks USING gin (search_vector);

-- ============================================================
-- Permissions
-- ============================================================
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO lai_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lai_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO lai_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO lai_user;

DO $$
BEGIN
    RAISE NOTICE 'LAI database initialization complete!';
END $$;
