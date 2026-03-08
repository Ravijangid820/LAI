"""Cross-encoder reranker via vLLM score endpoint.

Rescores search results using cross-encoder model for more
accurate query-document relevance scoring.
"""

import time

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from lai.core.config import get_settings
from lai.core.exceptions import RerankerError
from lai.core.logging import get_logger

logger = get_logger("lai.search.reranker")


class Reranker:
    """Cross-encoder reranker using vLLM /v1/score endpoint."""

    def __init__(self) -> None:
        settings = get_settings().reranker
        self._url = settings.url
        self._model = settings.model
        self._timeout = settings.timeout
        self._batch_size = settings.batch_size
        self._max_text_length = settings.max_text_length
        self._client: httpx.Client | None = None
        logger.info("Reranker initialized: model=%s, url=%s", self._model, self._url)

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    )
    def rerank(self, query: str, chunks: list[dict], top_k: int | None = None) -> list[dict]:
        """Rerank chunks by cross-encoder score.

        Args:
            query: Query text.
            chunks: List of chunk dicts from hybrid search.
            top_k: Number of results to return (defaults to retrieval.final_k).

        Returns:
            Reranked chunks with rerank_score added.
        """
        if not chunks:
            return []

        settings = get_settings().retrieval
        top_k = top_k or settings.final_k
        start = time.perf_counter()

        # Prepare texts (truncate to max length)
        texts = [c.get("text_clean", "")[:self._max_text_length] for c in chunks]

        # Call vLLM /v1/score endpoint
        client = self._get_client()
        try:
            response = client.post(
                f"{self._url}/score",
                json={"model": self._model, "text_1": query, "text_2": texts},
            )
            if response.status_code != 200:
                raise RerankerError(f"Reranker returned {response.status_code}: {response.text[:200]}")

            data = response.json()
            scores = [item["score"] for item in data.get("data", data if isinstance(data, list) else [])]

            if len(scores) != len(chunks):
                raise RerankerError(f"Score count mismatch: {len(scores)} vs {len(chunks)}")

        except (httpx.TimeoutException, httpx.ConnectError):
            raise
        except RerankerError:
            raise
        except Exception as e:
            logger.warning("Reranker call failed, falling back to hybrid scores: %s", e)
            scores = [c.get("hybrid_score", 0.5) for c in chunks]

        # Attach scores and sort
        for chunk, score in zip(chunks, scores):
            chunk["rerank_score"] = float(score)

        chunks.sort(key=lambda c: c["rerank_score"], reverse=True)
        result = chunks[:top_k]

        # Normalize scores to 0-1
        if result:
            max_s = max(c["rerank_score"] for c in result)
            min_s = min(c["rerank_score"] for c in result)
            spread = max_s - min_s if max_s != min_s else 1
            for i, c in enumerate(result):
                c["rerank_score"] = (c["rerank_score"] - min_s) / spread
                c["final_rank"] = i + 1

        duration = (time.perf_counter() - start) * 1000
        logger.info("Reranked %d -> %d chunks in %.1fms", len(chunks), len(result), duration)
        return result

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def check_health(self) -> dict:
        try:
            client = self._get_client()
            start = time.perf_counter()
            response = client.get(f"{self._url.rstrip('/score')}/health")
            latency = (time.perf_counter() - start) * 1000
            return {"status": "healthy" if response.status_code == 200 else "unhealthy", "latency_ms": latency}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker
