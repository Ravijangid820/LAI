"""Async-primary embedding client with a sync façade.

Wraps the OpenAI-compatible ``POST /v1/embeddings`` endpoint served by the
vLLM container hosting ``Qwen/Qwen3-Embedding-8B``:

    Request:  {"model": "...", "input": "..." | [...]}
    Response: {"object": "list",
               "data":   [{"object": "embedding", "embedding": [...],
                           "index": int}, ...],
               "model":  "...",
               "usage":  {"prompt_tokens": int, "total_tokens": int}}

Two clients — :class:`EmbeddingClient` (async) and
:class:`SyncEmbeddingClient` (sync) — share module-level pure helpers.
Each owns its own :class:`~httpx.AsyncClient` / :class:`~httpx.Client`.
The split mirrors :mod:`lai.common.llm.client` and
:mod:`lai.common.reranker.client` (ADR 0001 rationale applies identically).

Batching
--------

vLLM accepts large batches but the live ``resume_step6.sh`` pipeline
settled on 32 inputs per call as the working batch size. The client
auto-splits inputs larger than :attr:`EmbeddingConfig.max_batch_size` into
sequential requests and merges the results, preserving the caller's input
order. Indices in the returned :class:`EmbeddingResult` objects always
refer to the *original* ``inputs`` list the caller supplied.

Dimension validation
--------------------

Every returned vector is validated against
:attr:`EmbeddingConfig.dimension`. A mismatch raises
:class:`EmbeddingDimensionMismatchError` so the caller surfaces a
configuration drift before bad vectors reach pgvector. The single-string
convenience method :meth:`EmbeddingClient.embed_one` returns the bare
``list[float]`` for ergonomic callers.
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

from lai.common.embedding.config import EmbeddingConfig
from lai.common.embedding.metrics import EmbeddingMetrics, default_embedding_metrics
from lai.common.exceptions import (
    EmbeddingCallError,
    EmbeddingDimensionMismatchError,
    EmbeddingInvalidResponseError,
    EmbeddingRetryExhaustedError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["EmbeddingClient", "EmbeddingResult", "SyncEmbeddingClient"]

_log = structlog.get_logger(__name__)


class EmbeddingResult(BaseModel):
    """One embedded input with its original index and vector.

    The ``index`` field always refers to the caller's input list, even
    when batching splits the request across multiple HTTP calls — the
    client re-maps each batch's local indices back to global indices
    before returning.
    """

    index: int = Field(..., ge=0, description="Original position in the input list.")
    embedding: list[float] = Field(..., description="The embedding vector.")


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


def _validate_inputs(inputs: Sequence[str], max_input_chars: int) -> None:
    """Validate caller-supplied inputs before any HTTP work.

    Raises :class:`ValueError` for empty inputs or oversized strings. The
    per-input length check is preventative — vLLM would otherwise reject
    the request with an opaque HTTP 400, and surfacing the violation here
    gives the caller a clear error site.
    """
    if not inputs:
        raise ValueError("inputs must not be empty")
    for idx, text in enumerate(inputs):
        if not isinstance(text, str):
            raise ValueError(
                f"inputs[{idx}] must be a string, got {type(text).__name__}",
            )
        if len(text) > max_input_chars:
            raise ValueError(
                f"inputs[{idx}] has {len(text):,} chars; max_input_chars is "
                f"{max_input_chars:,}. Chunk the text before embedding.",
            )


def _build_request_body(*, model: str, inputs: Sequence[str]) -> dict[str, Any]:
    """Build the OpenAI-compatible ``/embeddings`` request body."""
    return {
        "model": model,
        "input": list(inputs),
    }


def _parse_embedding_response(
    raw: object,
    batch_size: int,
    expected_dimension: int,
) -> list[EmbeddingResult]:
    """Convert the ``/embeddings`` JSON response into :class:`EmbeddingResult` objects.

    Raises :class:`EmbeddingInvalidResponseError` on shape problems and
    :class:`EmbeddingDimensionMismatchError` on a vector with the wrong
    dimension.
    """
    if not isinstance(raw, dict):
        raise EmbeddingInvalidResponseError(
            f"expected a JSON object, got {type(raw).__name__}",
            raw_response=str(raw)[:500],
        )
    data = raw.get("data")
    if not isinstance(data, list):
        raise EmbeddingInvalidResponseError(
            f"response missing 'data' array (got {type(data).__name__})",
            raw_response=str(raw)[:500],
        )
    if len(data) != batch_size:
        raise EmbeddingInvalidResponseError(
            f"expected {batch_size} embeddings in response, got {len(data)}",
            raw_response=str(raw)[:500],
        )

    # Map by index so we never rely on the server's ordering matching ours.
    # The OpenAI spec says ``data`` is ordered by ``index`` ascending, but
    # we read ``index`` explicitly rather than trust the order.
    by_index: dict[int, list[float]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise EmbeddingInvalidResponseError(
                f"data entry was not an object: {entry!r}",
                raw_response=str(raw)[:500],
            )
        index_value = entry.get("index")
        embedding_value = entry.get("embedding")
        if not isinstance(index_value, int) or isinstance(index_value, bool):
            raise EmbeddingInvalidResponseError(
                f"entry missing int 'index': {entry!r}",
                raw_response=str(raw)[:500],
            )
        if not isinstance(embedding_value, list):
            raise EmbeddingInvalidResponseError(
                f"entry missing list 'embedding': {entry!r}",
                raw_response=str(raw)[:500],
            )
        if not 0 <= index_value < batch_size:
            raise EmbeddingInvalidResponseError(
                f"index {index_value} out of range for batch size {batch_size}",
                raw_response=str(raw)[:500],
            )
        if index_value in by_index:
            raise EmbeddingInvalidResponseError(
                f"duplicate index {index_value} in response",
                raw_response=str(raw)[:500],
            )
        if len(embedding_value) != expected_dimension:
            raise EmbeddingDimensionMismatchError(
                f"embedding for index {index_value} has dimension "
                f"{len(embedding_value)}, expected {expected_dimension}",
                expected_dimension=expected_dimension,
                actual_dimension=len(embedding_value),
                raw_response=str(raw)[:500],
            )
        # Coerce int → float here so callers get a uniform numeric type.
        # vLLM emits floats but the JSON parser will materialise integers
        # for any zero-fraction value, and downstream pgvector/numpy code
        # expects pure floats.
        by_index[index_value] = [float(v) for v in embedding_value]

    return [EmbeddingResult(index=i, embedding=by_index[i]) for i in range(batch_size)]


def _chunk_inputs(
    inputs: Sequence[str],
    max_batch_size: int,
) -> list[tuple[int, list[str]]]:
    """Split ``inputs`` into ``(offset, batch)`` chunks of at most ``max_batch_size``."""
    chunks: list[tuple[int, list[str]]] = []
    for offset in range(0, len(inputs), max_batch_size):
        chunks.append((offset, list(inputs[offset : offset + max_batch_size])))
    return chunks


def _merge_batches(
    batch_results: list[list[EmbeddingResult]],
    offsets: list[int],
) -> list[EmbeddingResult]:
    """Merge per-batch results, re-mapping indices to globals.

    The merged list is sorted by global index ascending so the caller can
    rely on ``result[i].index == i``.
    """
    merged: list[EmbeddingResult] = []
    for results, offset in zip(batch_results, offsets, strict=True):
        for r in results:
            merged.append(EmbeddingResult(index=r.index + offset, embedding=r.embedding))
    merged.sort(key=lambda r: r.index)
    return merged


def _classify_http_error(exc: httpx.HTTPStatusError, url: str) -> EmbeddingCallError:
    """Map :class:`httpx.HTTPStatusError` to :class:`EmbeddingCallError`."""
    response = exc.response
    return EmbeddingCallError(
        f"HTTP {response.status_code} from {url}: {response.text[:500]}",
        status_code=response.status_code,
        url=url,
    )


def _auth_headers(config: EmbeddingConfig) -> dict[str, str]:
    """Build the optional Authorization header dict."""
    if config.api_key is None:
        return {}
    return {"Authorization": f"Bearer {config.api_key.get_secret_value()}"}


# ─────────────────────────────────────────────────────────────────────────────
# Async client
# ─────────────────────────────────────────────────────────────────────────────


class EmbeddingClient:
    """Async embedding client.

    Args:
        config: :class:`EmbeddingConfig`; defaults to ``EmbeddingConfig()``
            which reads from environment variables.
        metrics: Optional :class:`EmbeddingMetrics`; defaults to the
            module-level singleton.
        transport: Optional :class:`httpx.AsyncBaseTransport` for tests.
    """

    def __init__(
        self,
        config: EmbeddingConfig | None = None,
        *,
        metrics: EmbeddingMetrics | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config or EmbeddingConfig()
        self._metrics = metrics or default_embedding_metrics
        self._http = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            headers=_auth_headers(self._config),
            transport=transport,
        )

    async def __aenter__(self) -> EmbeddingClient:
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

    async def embed(self, inputs: Sequence[str]) -> list[EmbeddingResult]:
        """Embed each string in ``inputs`` into its vector.

        Args:
            inputs: The strings to embed. Inputs longer than
                :attr:`EmbeddingConfig.max_batch_size` are split into
                sequential requests and merged on return.

        Returns:
            A list of :class:`EmbeddingResult` objects in caller-input
            order — ``result[i].index == i``.

        Raises:
            ValueError: ``inputs`` is empty, contains non-strings, or any
                element exceeds :attr:`EmbeddingConfig.max_input_chars`.
            EmbeddingCallError: Transport-level failure (after retries).
            EmbeddingInvalidResponseError: Response shape was unparseable.
            EmbeddingDimensionMismatchError: A vector had the wrong dimension.
            EmbeddingRetryExhaustedError: All retries failed.
        """
        _validate_inputs(inputs, self._config.max_input_chars)

        chunks = _chunk_inputs(inputs, self._config.max_batch_size)
        offsets = [offset for offset, _ in chunks]
        batch_results: list[list[EmbeddingResult]] = []
        for _offset, batch in chunks:
            body = _build_request_body(model=self._config.model, inputs=batch)
            batch_results.append(await self._call_with_retry(body, len(batch)))

        merged = _merge_batches(batch_results, offsets)
        self._metrics.inputs_total.labels(model=self._config.model).inc(len(inputs))
        return merged

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single string and return the raw vector.

        Convenience method for the very common single-query case (chat
        retrieval, ad-hoc similarity). Internally calls :meth:`embed` and
        unwraps the single result.

        Raises:
            ValueError: ``text`` is empty or exceeds the per-input limit.
        """
        if not text:
            raise ValueError("text must not be empty")
        results = await self.embed([text])
        return results[0].embedding

    async def _call_with_retry(self, body: dict[str, Any], batch_size: int) -> list[EmbeddingResult]:
        """Retry loop around :meth:`_call_once`."""
        attempt_number = 0
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(EmbeddingCallError),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.labels(model=self._config.model).inc()
                    return await self._call_once(body, batch_size)
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise EmbeddingRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
            ) from cause
        raise EmbeddingRetryExhaustedError(  # pragma: no cover
            "AsyncRetrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    async def _call_once(self, body: dict[str, Any], batch_size: int) -> list[EmbeddingResult]:
        """One HTTP round-trip + metric / log emission."""
        path = "embeddings"
        full_url = f"{self._config.base_url}/{path}"
        start = time.perf_counter()
        status_label = "error"
        model_label = self._config.model
        try:
            response = await self._http.post(path, json=body)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise _classify_http_error(exc, full_url) from exc
            try:
                raw = response.json()
            except ValueError as exc:
                raise EmbeddingCallError(
                    f"non-JSON response body: {response.text[:200]}",
                    status_code=response.status_code,
                    url=full_url,
                ) from exc
            try:
                parsed = _parse_embedding_response(raw, batch_size, self._config.dimension)
            except EmbeddingDimensionMismatchError:
                self._metrics.dimension_mismatch_total.labels(model=model_label).inc()
                raise
            status_label = "success"
            return parsed
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise EmbeddingCallError(
                f"transport error to {full_url}: {exc}",
                url=full_url,
            ) from exc
        finally:
            duration = time.perf_counter() - start
            self._metrics.calls_total.labels(model=model_label, status=status_label).inc()
            self._metrics.request_duration_seconds.labels(
                model=model_label,
                status=status_label,
            ).observe(duration)
            _log.info(
                "embedding.call.complete",
                duration_seconds=round(duration, 4),
                status=status_label,
                batch_size=batch_size,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Sync façade
# ─────────────────────────────────────────────────────────────────────────────


class SyncEmbeddingClient:
    """Sync version of :class:`EmbeddingClient`.

    Holds its own :class:`httpx.Client`. Same surface modulo
    ``async``/``await``. Justified by ADR 0001 (same rationale as
    :class:`~lai.common.llm.client.SyncLlmClient`).
    """

    def __init__(
        self,
        config: EmbeddingConfig | None = None,
        *,
        metrics: EmbeddingMetrics | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config or EmbeddingConfig()
        self._metrics = metrics or default_embedding_metrics
        self._http = httpx.Client(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            headers=_auth_headers(self._config),
            transport=transport,
        )

    def __enter__(self) -> SyncEmbeddingClient:
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

    def embed(self, inputs: Sequence[str]) -> list[EmbeddingResult]:
        """Sync counterpart to :meth:`EmbeddingClient.embed`."""
        _validate_inputs(inputs, self._config.max_input_chars)

        chunks = _chunk_inputs(inputs, self._config.max_batch_size)
        offsets = [offset for offset, _ in chunks]
        batch_results: list[list[EmbeddingResult]] = []
        for _offset, batch in chunks:
            body = _build_request_body(model=self._config.model, inputs=batch)
            batch_results.append(self._call_with_retry(body, len(batch)))

        merged = _merge_batches(batch_results, offsets)
        self._metrics.inputs_total.labels(model=self._config.model).inc(len(inputs))
        return merged

    def embed_one(self, text: str) -> list[float]:
        """Sync counterpart to :meth:`EmbeddingClient.embed_one`."""
        if not text:
            raise ValueError("text must not be empty")
        results = self.embed([text])
        return results[0].embedding

    def _call_with_retry(self, body: dict[str, Any], batch_size: int) -> list[EmbeddingResult]:
        attempt_number = 0
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(EmbeddingCallError),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.labels(model=self._config.model).inc()
                    return self._call_once(body, batch_size)
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise EmbeddingRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
            ) from cause
        raise EmbeddingRetryExhaustedError(  # pragma: no cover
            "Retrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    def _call_once(self, body: dict[str, Any], batch_size: int) -> list[EmbeddingResult]:
        path = "embeddings"
        full_url = f"{self._config.base_url}/{path}"
        start = time.perf_counter()
        status_label = "error"
        model_label = self._config.model
        try:
            response = self._http.post(path, json=body)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise _classify_http_error(exc, full_url) from exc
            try:
                raw = cast("object", response.json())
            except ValueError as exc:
                raise EmbeddingCallError(
                    f"non-JSON response body: {response.text[:200]}",
                    status_code=response.status_code,
                    url=full_url,
                ) from exc
            try:
                parsed = _parse_embedding_response(raw, batch_size, self._config.dimension)
            except EmbeddingDimensionMismatchError:
                self._metrics.dimension_mismatch_total.labels(model=model_label).inc()
                raise
            status_label = "success"
            return parsed
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise EmbeddingCallError(
                f"transport error to {full_url}: {exc}",
                url=full_url,
            ) from exc
        finally:
            duration = time.perf_counter() - start
            self._metrics.calls_total.labels(model=model_label, status=status_label).inc()
            self._metrics.request_duration_seconds.labels(
                model=model_label,
                status=status_label,
            ).observe(duration)
            _log.info(
                "embedding.call.complete",
                duration_seconds=round(duration, 4),
                status=status_label,
                batch_size=batch_size,
            )
