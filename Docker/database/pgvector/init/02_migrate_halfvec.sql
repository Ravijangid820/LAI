-- Migration: vector(1024) → halfvec(4096) for Qwen3-Embedding-8B
--
-- Qwen3-Embedding-8B outputs 4096-dim vectors natively (hidden_size=4096).
-- It does not support Matryoshka truncation, so we keep full dimensions
-- and use halfvec (fp16) to cut storage in half vs vector (fp32).
--
-- Index: no HNSW — pgvector's HNSW limit is 4000 dims for halfvec,
-- 2000 for vector. Exact cosine search is fast enough for 217K rows
-- with pre-filters on domain/doc_type.
--
-- Safe to run multiple times: only alters if current type is vector(1024).

DO $$
DECLARE
    current_type TEXT;
BEGIN
    SELECT format_type(atttypid, atttypmod)
      INTO current_type
      FROM pg_attribute
     WHERE attrelid = 'public.child_chunks'::regclass
       AND attname  = 'embedding';

    IF current_type = 'vector(1024)' THEN
        RAISE NOTICE 'Migrating child_chunks.embedding: vector(1024) -> halfvec(4096)';
        -- Drop any old vector(1024) indexes that would block the ALTER
        DROP INDEX IF EXISTS idx_child_embedding;
        ALTER TABLE child_chunks
            ALTER COLUMN embedding TYPE halfvec(4096)
            USING NULL::halfvec(4096);
        RAISE NOTICE 'Column type is now halfvec(4096)';
    ELSIF current_type = 'halfvec(4096)' THEN
        RAISE NOTICE 'child_chunks.embedding already halfvec(4096), skipping';
    ELSE
        RAISE WARNING 'Unexpected current type: %. Manual review needed.', current_type;
    END IF;
END $$;

-- NOTE: Do NOT create HNSW on halfvec(4096) — pgvector HNSW max is 4000 dims.
-- Use exact search at query time. Example hybrid-search call site should do
-- SELECT with ORDER BY embedding <=> $1::halfvec LIMIT k, possibly with
-- domain/doc_type filters to keep latency reasonable.
