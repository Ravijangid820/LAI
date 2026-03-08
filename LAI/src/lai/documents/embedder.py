"""Embedding generator using BGE-M3 via vLLM OpenAI-compatible API.

Supports single and batch embedding with retry logic and Redis caching.
"""

import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lai.core.config import get_settings
from lai.core.exceptions import EmbeddingError
from lai.core.logging import get_logger
from lai.infra import redis as cache

logger = get_logger("lai.documents.embedder")


class Embedder:
    """Embedding generator using BGE-M3 via vLLM."""

    def __init__(self) -> None:
        settings = get_settings().embedding
        self._url = settings.url
        self._model = settings.model
        self._batch_size = settings.batch_size
        self._timeout = settings.timeout
        self._dimension = settings.dimension
        self._client: httpx.AsyncClient | None = None
        logger.info("Embedder initialized: model=%s, url=%s, dim=%d", self._model, self._url, self._dimension)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    )
    async def _embed_batch_remote(self, texts: list[str]) -> list[list[float]]:
        """Call vLLM /v1/embeddings endpoint."""
        client = self._get_client()
        response = await client.post(f"{self._url}/v1/embeddings", json={"input": texts, "model": self._model})
        if response.status_code != 200:
            raise EmbeddingError(f"Embedding service returned {response.status_code}: {response.text[:200]}")
        data = response.json()
        if "data" not in data:
            raise EmbeddingError(f"Unexpected response format: {list(data.keys())}")
        embeddings = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
        if len(embeddings) != len(texts):
            raise EmbeddingError(f"Count mismatch: got {len(embeddings)}, expected {len(texts)}")
        return embeddings

    async def embed(self, text: str) -> list[float]:
        """Embed a single text with Redis caching."""
        cached = await cache.get_embedding(text)
        if cached is not None:
            return cached

        start = time.perf_counter()
        embeddings = await self._embed_batch_remote([text])
        embedding = embeddings[0]
        duration = (time.perf_counter() - start) * 1000

        if all(v == 0.0 for v in embedding):
            logger.warning("Zero vector for text: %s...", text[:80])
        else:
            await cache.set_embedding(text, embedding)

        logger.debug("Embedded 1 text in %.1fms", duration)
        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in batches with caching."""
        if not texts:
            return []

        results: list[list[float]] = [[] for _ in texts]
        uncached_indices: list[int] = []

        # Check cache first
        for i, text in enumerate(texts):
            cached = await cache.get_embedding(text)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            logger.info("All %d embeddings served from cache", len(texts))
            return results

        # Batch-embed uncached texts
        uncached_texts = [texts[i] for i in uncached_indices]
        total_start = time.perf_counter()
        all_embeddings: list[list[float]] = []

        for batch_start in range(0, len(uncached_texts), self._batch_size):
            batch = uncached_texts[batch_start:batch_start + self._batch_size]
            try:
                batch_embeddings = await self._embed_batch_remote(batch)
                all_embeddings.extend(batch_embeddings)
            except EmbeddingError as e:
                logger.error("Batch embedding failed: %s", e)
                all_embeddings.extend([[0.0] * self._dimension] * len(batch))

        # Fill results and cache
        for idx, embedding in zip(uncached_indices, all_embeddings):
            results[idx] = embedding
            if not all(v == 0.0 for v in embedding):
                await cache.set_embedding(texts[idx], embedding)

        duration = (time.perf_counter() - total_start) * 1000
        zero_count = sum(1 for e in all_embeddings if all(v == 0.0 for v in e))
        logger.info("Embedded %d texts in %.0fms (cached=%d, zeros=%d)", len(uncached_texts), duration, len(texts) - len(uncached_indices), zero_count)
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def check_health(self) -> dict:
        try:
            client = self._get_client()
            start = time.perf_counter()
            response = await client.get(f"{self._url}/health")
            latency = (time.perf_counter() - start) * 1000
            return {"status": "healthy" if response.status_code == 200 else "unhealthy", "latency_ms": latency, "model": self._model}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
