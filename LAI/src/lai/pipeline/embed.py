"""
Step 6: Embeddings → pgvector

Generates embeddings for child chunks using Qwen3-Embedding-8B via
OpenAI-compatible API (vLLM), then stores vectors in child_chunks.embedding
and generates tsvector for BM25 hybrid search.

After bulk loading, HNSW and GIN indexes should be created:
    CREATE INDEX idx_child_embedding ON child_chunks
        USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=200);
    CREATE INDEX idx_child_search ON child_chunks USING gin (search_vector);
"""

from typing import Any, Dict, List

import httpx

from lai.core.config import get_settings
from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.embed")


def embed_batch(
    texts: List[str],
    *,
    embed_url: str,
    embed_model: str,
    batch_size: int = 32,
    timeout: float = 120.0,
) -> List[List[float]]:
    """
    Embed a list of texts via OpenAI-compatible /v1/embeddings endpoint.
    Returns list of embedding vectors (1024-dim each).
    """
    all_embeddings: List[List[float]] = []
    total_batches = (len(texts) + batch_size - 1) // batch_size
    logger.info(f"Embedding {len(texts)} texts in {total_batches} batches (model={embed_model})")

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_num = i // batch_size + 1

        payload = {
            "model": embed_model,
            "input": batch,
            # Belt-and-suspenders truncation at the tokenizer level. The
            # embedding service runs with max-model-len=32768, so this only
            # kicks in for pathologically long chunks.
            "truncate_prompt_tokens": 32000,
        }

        try:
            resp = httpx.post(
                f"{embed_url}/v1/embeddings",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(
                f"Embedding API request failed (batch {batch_num}/{total_batches}): {e}"
            )
            raise

        data = resp.json()["data"]
        # Sort by index to maintain order
        data.sort(key=lambda x: x["index"])
        all_embeddings.extend([item["embedding"] for item in data])

        if batch_num % 10 == 0 or batch_num == total_batches:
            logger.debug(f"Embedding progress: batch {batch_num}/{total_batches}")

    logger.info(f"Embedding complete: {len(all_embeddings)} vectors generated")
    return all_embeddings


def build_search_text(content: str, context_prefix: str = "") -> str:
    """Build the text to embed: context_prefix + content."""
    if context_prefix:
        return f"{context_prefix}\n\n{content}"
    return content


def generate_tsvector_sql(text: str) -> str:
    """
    Generate a tsvector expression for German full-text search.
    Uses 'german' dictionary for stemming.
    """
    # Truncate very long texts for tsvector (PostgreSQL has limits)
    if len(text) > 50000:
        text = text[:50000]
    return text
