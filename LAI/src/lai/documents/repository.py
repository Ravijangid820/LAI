"""Document-domain database operations.

Handles chunk storage, retrieval, and user schema management.
"""

from uuid import UUID

from lai.core.exceptions import SchemaError
from lai.core.logging import get_logger
from lai.infra.database import get_pool

logger = get_logger("lai.documents.repository")


async def create_user_schema(user_id: str) -> str:
    """Create a per-user PostgreSQL schema (idempotent).

    Creates the schema and a chunks table mirroring public.chunks.
    """
    pool = get_pool()
    schema_name = f"user_{user_id.replace('-', '_')}"

    async with pool.acquire() as conn:
        try:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {schema_name}.chunks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    document_id UUID NOT NULL,
                    user_id TEXT NOT NULL,
                    text_clean TEXT NOT NULL,
                    text_tagged TEXT,
                    section TEXT,
                    subsection TEXT,
                    chunk_index INTEGER DEFAULT 0,
                    paragraph_refs TEXT[],
                    article_refs TEXT[],
                    law_refs TEXT[],
                    doc_type TEXT DEFAULT 'other',
                    court_level INTEGER,
                    effective_date DATE,
                    decision_date DATE,
                    is_current BOOLEAN DEFAULT true,
                    entities JSONB DEFAULT '{{}}',
                    metadata JSONB DEFAULT '{{}}',
                    embedding vector(1024),
                    search_vector tsvector,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{schema_name}_chunks_embedding
                ON {schema_name}.chunks USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 200)
            """)
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{schema_name}_chunks_search
                ON {schema_name}.chunks USING gin (search_vector)
            """)
            logger.info("User schema created: %s", schema_name)
        except Exception as e:
            logger.error("Failed to create schema %s: %s", schema_name, e)
            raise SchemaError(f"Failed to create user schema: {e}") from e

    return schema_name


async def insert_chunks(chunks: list[dict], schema: str = "public") -> int:
    """Insert chunks into the database. Returns count of inserted rows."""
    if not chunks:
        return 0

    pool = get_pool()
    table = f"{schema}.chunks"
    inserted = 0

    async with pool.acquire() as conn:
        for chunk in chunks:
            try:
                await conn.execute(
                    f"""
                    INSERT INTO {table}
                        (document_id, user_id, text_clean, section, chunk_index,
                         paragraph_refs, article_refs, law_refs, embedding, search_vector)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,
                            to_tsvector('german', $3))
                    """,
                    chunk.get("document_id"),
                    chunk.get("user_id", "system"),
                    chunk["text_clean"],
                    chunk.get("section", ""),
                    chunk.get("chunk_index", 0),
                    chunk.get("paragraph_refs", []),
                    chunk.get("article_refs", []),
                    chunk.get("law_refs", []),
                    chunk.get("embedding"),
                )
                inserted += 1
            except Exception as e:
                logger.error("Failed to insert chunk %d: %s", chunk.get("chunk_index", 0), e)

    logger.info("Inserted %d/%d chunks into %s", inserted, len(chunks), table)
    return inserted


async def delete_document_chunks(document_id: str, schema: str = "public") -> int:
    """Delete all chunks for a document. Returns count of deleted rows."""
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(f"DELETE FROM {schema}.chunks WHERE document_id = $1", document_id)
        count = int(result.split()[-1])
        logger.info("Deleted %d chunks for document %s from %s", count, document_id, schema)
        return count
