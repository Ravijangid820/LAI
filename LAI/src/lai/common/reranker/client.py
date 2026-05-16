"""Async-primary reranker client with a sync façade.

Wraps the HuggingFace Text-Embeddings-Inference (TEI) ``POST /rerank``
endpoint:

    Request:  {"query": "...", "texts": [...], "truncate": bool}
    Response: [{"index": int, "score": float}, ...]  # sorted by score desc

Two clients — :class:`RerankerClient` (async) and :class:`SyncRerankerClient`
(sync) — share module-level pure helpers. Each owns its own
:class:`~httpx.AsyncClient` / :class:`~httpx.Client`. The split mirrors
:mod:`lai.common.llm.client` (ADR 0001 rationale applies identically).

Batching
--------

TEI imposes ``max_client_batch_size`` (32 for ms-marco-MiniLM-L-12-v2).
The client automatically splits inputs larger than
:attr:`RerankerConfig.max_batch_size` into multiple sequential requests
and merges the results, re-sorting by score descending. Indices in the
returned :class:`RerankResult` objects always refer to the *original*
``texts`` list the caller supplied.

Top-N filtering
---------------

When the caller passes ``top_n``, the client returns at most ``top_n``
items, taking the highest-scoring across all batches. Without ``top_n``,
every item is returned.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from pydantic import BaseModel, Field
from tenacity import (
    AsyncRetrying,
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lai.common.exceptions import (
    RerankerCallError,
    RerankerInvalidResponseError,
    RerankerRetryExhaustedError,
)
from lai.common.reranker.config import RerankerConfig
from lai.common.reranker.metrics import RerankerMetrics, default_reranker_metrics

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["RerankResult", "RerankerClient", "SyncRerankerClient"]

_log = structlog.get_logger(__name__)


class RerankResult(BaseModel):
    """One reranked item with its original index and relevance score.

    The ``index`` field always refers to the caller's input ``texts``
    list, even when batching splits the request across multiple HTTP
    calls — the client re-maps each batch's local indices back to global
    indices before returning.
    """

    index: int = Field(..., ge=0, description="Original position in the input texts list.")
    score: float = Field(..., description="Relevance score; higher is more relevant.")


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_request_body(
    *,
    query: str,
    texts: Sequence[str],
    truncate: bool,
) -> dict[str, Any]:
    """Build the TEI ``/rerank`` request body."""
    return {
        "query": query,
        "texts": list(texts),
        "truncate": truncate,
    }


def _parse_rerank_response(
    raw: object,
    batch_size: int,
) -> list[RerankResult]:
    """Convert TEI's ``/rerank`` JSON response into :class:`RerankResult` objects.

    Raises :class:`RerankerInvalidResponseError` on:
    - non-list response
    - non-dict entries
    - missing or non-int ``index`` / non-float ``score``
    - ``index`` outside ``[0, batch_size)``
    """
    if not isinstance(raw, list):
        raise RerankerInvalidResponseError(
            f"expected a JSON array, got {type(raw).__name__}",
            raw_response=str(raw)[:500],
        )
    out: list[RerankResult] = []
    for item in raw:
        if not isinstance(item, dict):
            raise RerankerInvalidResponseError(
                f"array entry was not an object: {item!r}",
                raw_response=str(raw)[:500],
            )
        index_value = item.get("index")
        score_value = item.get("score")
        if not isinstance(index_value, int) or isinstance(index_value, bool):
            raise RerankerInvalidResponseError(
                f"entry missing int 'index': {item!r}",
                raw_response=str(raw)[:500],
            )
        if not isinstance(score_value, int | float) or isinstance(score_value, bool):
            raise RerankerInvalidResponseError(
                f"entry missing numeric 'score': {item!r}",
                raw_response=str(raw)[:500],
            )
        if not 0 <= index_value < batch_size:
            raise RerankerInvalidResponseError(
                f"index {index_value} out of range for batch size {batch_size}",
                raw_response=str(raw)[:500],
            )
        out.append(RerankResult(index=index_value, score=float(score_value)))
    return out


def _chunk_texts(
    texts: Sequence[str],
    max_batch_size: int,
) -> list[tuple[int, list[str]]]:
    """Split ``texts`` into ``(offset, batch)`` chunks of at most ``max_batch_size``.

    Returns a list of ``(global_offset, batch_list)`` tuples so the caller
    can re-map each batch's local indices to global indices after the
    parallel rerank.
    """
    chunks: list[tuple[int, list[str]]] = []
    for offset in range(0, len(texts), max_batch_size):
        chunks.append((offset, list(texts[offset : offset + max_batch_size])))
    return chunks


def _merge_batches(
    batch_results: list[list[RerankResult]],
    offsets: list[int],
    top_n: int | None,
) -> list[RerankResult]:
    """Merge per-batch results, re-mapping indices and applying ``top_n``.

    Each batch's local ``index`` becomes a global index by adding the
    batch's offset. The merged list is sorted by score descending.
    """
    merged: list[RerankResult] = []
    for results, offset in zip(batch_results, offsets, strict=True):
        for r in results:
            merged.append(RerankResult(index=r.index + offset, score=r.score))
    merged.sort(key=lambda r: r.score, reverse=True)
    if top_n is not None:
        merged = merged[:top_n]
    return merged


def _classify_http_error(exc: httpx.HTTPStatusError, url: str) -> RerankerCallError:
    """Map :class:`httpx.HTTPStatusError` to :class:`RerankerCallError`."""
    response = exc.response
    return RerankerCallError(
        f"HTTP {response.status_code} from {url}: {response.text[:500]}",
        status_code=response.status_code,
        url=url,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Async client
# ─────────────────────────────────────────────────────────────────────────────


class RerankerClient:
    """Async reranker client.

    Args:
        config: :class:`RerankerConfig`; defaults to ``RerankerConfig()``
            which reads from environment variables.
        metrics: Optional :class:`RerankerMetrics`; defaults to the
            module-level singleton.
        transport: Optional :class:`httpx.AsyncBaseTransport` for tests.
    """

    def __init__(
        self,
        config: RerankerConfig | None = None,
        *,
        metrics: RerankerMetrics | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config or RerankerConfig()
        self._metrics = metrics or default_reranker_metrics
        self._http = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> RerankerClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP transport."""
        await self._http.aclose()

    async def rerank(
        self,
        query: str,
        texts: Sequence[str],
        *,
        top_n: int | None = None,
        truncate: bool = False,
    ) -> list[RerankResult]:
        """Score and rank ``texts`` by relevance to ``query``.

        Args:
            query: The query the reranker scores documents against.
            texts: The candidate documents. Inputs longer than
                :attr:`RerankerConfig.max_batch_size` are split into
                sequential requests and merged on the way back.
            top_n: If supplied, return at most this many results
                (highest-scoring across all batches). Otherwise return
                every input ranked.
            truncate: If ``True``, ask TEI to truncate documents that
                exceed its model's ``max_input_length`` rather than
                erroring. Default ``False`` to surface oversized inputs
                as a clear failure.

        Returns:
            A list of :class:`RerankResult` objects sorted by score
            descending. ``index`` refers to the original ``texts`` list.

        Raises:
            ValueError: ``texts`` is empty.
            RerankerCallError: Transport-level failure (after retries).
            RerankerInvalidResponseError: Response shape was not parseable.
            RerankerRetryExhaustedError: All retries failed.
        """
        if not texts:
            raise ValueError("texts must not be empty")

        chunks = _chunk_texts(texts, self._config.max_batch_size)
        offsets = [offset for offset, _ in chunks]
        batch_results: list[list[RerankResult]] = []
        for _offset, batch in chunks:
            body = _build_request_body(query=query, texts=batch, truncate=truncate)
            batch_results.append(await self._call_with_retry(body, len(batch)))

        merged = _merge_batches(batch_results, offsets, top_n)

        self._metrics.documents_total.labels(kind="input").inc(len(texts))
        self._metrics.documents_total.labels(kind="returned").inc(len(merged))
        return merged

    async def _call_with_retry(self, body: dict[str, Any], batch_size: int) -> list[RerankResult]:
        """Retry loop around :meth:`_call_once`."""
        attempt_number = 0
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(RerankerCallError),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.inc()
                    return await self._call_once(body, batch_size)
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise RerankerRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
            ) from cause
        raise RerankerRetryExhaustedError(  # pragma: no cover
            "AsyncRetrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    async def _call_once(self, body: dict[str, Any], batch_size: int) -> list[RerankResult]:
        """One HTTP round-trip + metric/log emission."""
        path = "rerank"
        full_url = f"{self._config.base_url}/{path}"
        start = time.perf_counter()
        status_label = "error"
        try:
            response = await self._http.post(path, json=body)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise _classify_http_error(exc, full_url) from exc
            try:
                raw = response.json()
            except ValueError as exc:
                # ``response.json()`` raises ``ValueError``
                # (``json.JSONDecodeError``) on a non-JSON body.
                raise RerankerCallError(
                    f"non-JSON response body: {response.text[:200]}",
                    status_code=response.status_code,
                    url=full_url,
                ) from exc
            parsed = _parse_rerank_response(raw, batch_size)
            status_label = "success"
            return parsed
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RerankerCallError(
                f"transport error to {full_url}: {exc}",
                url=full_url,
            ) from exc
        finally:
            duration = time.perf_counter() - start
            self._metrics.calls_total.labels(status=status_label).inc()
            self._metrics.request_duration_seconds.labels(status=status_label).observe(duration)
            _log.info(
                "reranker.call.complete",
                duration_seconds=round(duration, 4),
                status=status_label,
                batch_size=batch_size,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Sync façade
# ─────────────────────────────────────────────────────────────────────────────


class SyncRerankerClient:
    """Sync version of :class:`RerankerClient`.

    Holds its own :class:`httpx.Client`. Same surface modulo
    ``async``/``await``. Justified by ADR 0001 (same rationale as
    :class:`~lai.common.llm.client.SyncLlmClient`).
    """

    def __init__(
        self,
        config: RerankerConfig | None = None,
        *,
        metrics: RerankerMetrics | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config or RerankerConfig()
        self._metrics = metrics or default_reranker_metrics
        self._http = httpx.Client(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> SyncRerankerClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP transport."""
        self._http.close()

    def rerank(
        self,
        query: str,
        texts: Sequence[str],
        *,
        top_n: int | None = None,
        truncate: bool = False,
    ) -> list[RerankResult]:
        """Sync counterpart to :meth:`RerankerClient.rerank`."""
        if not texts:
            raise ValueError("texts must not be empty")

        chunks = _chunk_texts(texts, self._config.max_batch_size)
        offsets = [offset for offset, _ in chunks]
        batch_results: list[list[RerankResult]] = []
        for _offset, batch in chunks:
            body = _build_request_body(query=query, texts=batch, truncate=truncate)
            batch_results.append(self._call_with_retry(body, len(batch)))

        merged = _merge_batches(batch_results, offsets, top_n)

        self._metrics.documents_total.labels(kind="input").inc(len(texts))
        self._metrics.documents_total.labels(kind="returned").inc(len(merged))
        return merged

    def _call_with_retry(self, body: dict[str, Any], batch_size: int) -> list[RerankResult]:
        attempt_number = 0
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(RerankerCallError),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.inc()
                    return self._call_once(body, batch_size)
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise RerankerRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
            ) from cause
        raise RerankerRetryExhaustedError(  # pragma: no cover
            "Retrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    def _call_once(self, body: dict[str, Any], batch_size: int) -> list[RerankResult]:
        path = "rerank"
        full_url = f"{self._config.base_url}/{path}"
        start = time.perf_counter()
        status_label = "error"
        try:
            response = self._http.post(path, json=body)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise _classify_http_error(exc, full_url) from exc
            try:
                raw = cast("object", response.json())
            except ValueError as exc:
                raise RerankerCallError(
                    f"non-JSON response body: {response.text[:200]}",
                    status_code=response.status_code,
                    url=full_url,
                ) from exc
            parsed = _parse_rerank_response(raw, batch_size)
            status_label = "success"
            return parsed
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RerankerCallError(
                f"transport error to {full_url}: {exc}",
                url=full_url,
            ) from exc
        finally:
            duration = time.perf_counter() - start
            self._metrics.calls_total.labels(status=status_label).inc()
            self._metrics.request_duration_seconds.labels(status=status_label).observe(duration)
            _log.info(
                "reranker.call.complete",
                duration_seconds=round(duration, 4),
                status=status_label,
                batch_size=batch_size,
            )
