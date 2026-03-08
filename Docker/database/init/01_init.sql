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

-- Note: The law_chunks table with vector embeddings should already exist
-- from your data processing pipeline. If not, uncomment below:

-- CREATE TABLE IF NOT EXISTS law_chunks (
--     id SERIAL PRIMARY KEY,
--     content TEXT NOT NULL,
--     embedding vector(1024),
--     source VARCHAR(500),
--     title VARCHAR(500),
--     section VARCHAR(500),
--     metadata JSONB DEFAULT '{}'::jsonb,
--     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
-- );
-- CREATE INDEX IF NOT EXISTS idx_law_chunks_embedding ON law_chunks USING ivfflat (embedding vector_cosine_ops);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO lai_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO lai_user;

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'LAI database initialization complete!';
END $$;
