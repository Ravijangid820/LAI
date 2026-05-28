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

from concurrent.futures import ThreadPoolExecutor

import httpx

from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.embed")


def _parse_urls(embed_url) -> list[str]:
    """embed_url may be a single URL string, a CSV of URLs, or a list."""
    if isinstance(embed_url, (list, tuple)):
        return [u.rstrip("/") for u in embed_url if u]
    if isinstance(embed_url, str) and "," in embed_url:
        return [u.strip().rstrip("/") for u in embed_url.split(",") if u.strip()]
    return [embed_url.rstrip("/")]


def _post_embed(url: str, model: str, batch: list[str], timeout: float) -> list:
    payload = {
        "model": model,
        "input": batch,
        # Belt-and-suspenders truncation at the tokenizer level. The
        # embedding service runs with max-model-len=32768, so this only
        # kicks in for pathologically long chunks.
        "truncate_prompt_tokens": 32000,
    }
    resp = httpx.post(f"{url}/v1/embeddings", json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda x: x["index"])
    return [item["embedding"] for item in data]


def embed_batch(
    texts: list[str],
    *,
    embed_url,
    embed_model: str,
    batch_size: int = 32,
    timeout: float = 120.0,
) -> list[list[float]]:
    """
    Embed a list of texts via OpenAI-compatible /v1/embeddings endpoint(s).

    `embed_url` may be a single URL string, a list of URLs, or a CSV of URLs.
    When multiple URLs are given, batches are dispatched across them in
    parallel (one worker per URL) so a multi-server pool is used concurrently.

    Returns list of embedding vectors (4096-dim each for Qwen3-Embedding-8B).
    """
    urls = _parse_urls(embed_url)
    all_embeddings: list[list[float]] = [None] * len(texts)  # type: ignore[list-item]
    total_batches = (len(texts) + batch_size - 1) // batch_size
    logger.info(f"Embedding {len(texts)} texts in {total_batches} batches (model={embed_model}, urls={len(urls)})")

    # Build a list of (start, end) slice indices per batch
    slices = [(i, min(i + batch_size, len(texts))) for i in range(0, len(texts), batch_size)]

    def run_batch(bi: int, s: int, e: int):
        url = urls[bi % len(urls)]
        return bi, s, _post_embed(url, embed_model, texts[s:e], timeout)

    # Parallelism: one worker per URL is enough — httpx is blocking, and vLLM
    # servers do their own internal batching, so we just need to keep each
    # server busy. Oversubscribing doesn't help and increases tail latency.
    workers = max(len(urls), 1)
    if workers == 1:
        for bi, (s, e) in enumerate(slices):
            _, _, vectors = run_batch(bi, s, e)
            for off, v in enumerate(vectors):
                all_embeddings[s + off] = v
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_batch, bi, s, e) for bi, (s, e) in enumerate(slices)]
            for fut in futures:
                try:
                    _, s, vectors = fut.result()
                except httpx.HTTPError as exc:
                    logger.error(f"Embedding API request failed: {exc}")
                    raise
                for off, v in enumerate(vectors):
                    all_embeddings[s + off] = v

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
