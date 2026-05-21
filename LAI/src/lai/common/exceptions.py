"""Typed exception hierarchy for ``lai.common``.

All exceptions raised by ``lai.common`` inherit from :class:`LaiCommonError`
so callers can catch the entire package's failure surface with a single
``except`` clause when that is the right design. Subhierarchies let callers
distinguish recoverable from non-recoverable conditions and decide between
"retry", "fall through to typed empty", or "surface as a finding".

Design intent
-------------

* :class:`LlmCallError` is **transport-level** — HTTP error, timeout,
  connection refused. Retry candidates.
* :class:`LlmInvalidResponseError` is **content-level** — the response
  arrived but is unusable. The three subtypes
  (:class:`LlmEmptyResponseError`, :class:`LlmJsonParseError`,
  :class:`LlmSchemaValidationError`) give callers the granularity to
  retry transient empties, treat parse failures as data-quality findings,
  and route schema mismatches to a typed empty fallback.
* :class:`LlmGuidedDecodingError` flags vLLM-side rejection of a JSON
  Schema; the client catches it and falls back to looser JSON mode
  (ADR 0002).
* :class:`LlmRetryExhaustedError` wraps the *last* cause from a retry
  loop. Callers distinguish "tried and gave up" from "first-attempt
  transient error" because the recovery actions differ.

All exceptions carry kwarg-only context fields for structured logging.
Construction always preserves the ``__cause__`` chain when used with
``raise ... from``.
"""

from __future__ import annotations

__all__ = [
    "ChunkError",
    "ChunkInvalidInputError",
    "EmbeddingCallError",
    "EmbeddingDimensionMismatchError",
    "EmbeddingError",
    "EmbeddingInvalidResponseError",
    "EmbeddingRetryExhaustedError",
    "LaiCommonError",
    "LlmCallError",
    "LlmEmptyResponseError",
    "LlmError",
    "LlmGuidedDecodingError",
    "LlmInvalidResponseError",
    "LlmJsonParseError",
    "LlmRetryExhaustedError",
    "LlmSchemaValidationError",
    "PdfError",
    "PdfExtractError",
    "PdfOcrUnavailableError",
    "RerankerCallError",
    "RerankerError",
    "RerankerInvalidResponseError",
    "RerankerRetryExhaustedError",
    "RetrievalConnectionError",
    "RetrievalDimensionError",
    "RetrievalError",
    "RetrievalQueryError",
    "RetrievalRetryExhaustedError",
]


class LaiCommonError(Exception):
    """Root exception for the ``lai.common`` package."""


# ─────────────────────────────────────────────────────────────────────────────
# LLM-related
# ─────────────────────────────────────────────────────────────────────────────


class LlmError(LaiCommonError):
    """Base for any failure interacting with the LLM service."""


class LlmCallError(LlmError):
    """Transport-level failure when calling the LLM.

    Covers HTTP errors, request timeouts, and connection refusals. The
    LLM never produced a response (or the response could not be retrieved).

    Args:
        message: Human-readable description.
        status_code: HTTP status code, if the failure was a non-2xx response.
            ``None`` for pre-HTTP failures (timeout, DNS, connection refused).
        url: The endpoint that failed, for log attribution.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code
        self.url: str | None = url


class LlmInvalidResponseError(LlmError):
    """The LLM responded successfully but the content is unusable.

    Subclassed by the specific failure modes (empty, parse, schema). Carries
    the raw response text so logs and callers can inspect what the model
    actually returned.

    Args:
        message: Human-readable description.
        raw_response: The exact string the LLM returned, if available.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response: str | None = raw_response


class LlmEmptyResponseError(LlmInvalidResponseError):
    """The LLM returned empty or ``None`` content.

    Frequently seen on Qwen3 when a stop token is emitted immediately. A
    single retry usually recovers; persistent emptiness flows to the typed
    empty fallback.
    """


class LlmJsonParseError(LlmInvalidResponseError):
    """The response could not be parsed as JSON, even after salvage."""


class LlmSchemaValidationError(LlmInvalidResponseError):
    """JSON parsed but failed Pydantic schema validation.

    Args:
        message: Human-readable description.
        raw_response: The exact string the LLM returned, if available.
        validation_errors: The Pydantic ``ValidationError`` ``.errors()``
            output, copied to a plain ``list[dict]`` for safe serialisation
            into structured logs.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
        validation_errors: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(message, raw_response=raw_response)
        self.validation_errors: list[dict[str, object]] = list(validation_errors or [])


class LlmGuidedDecodingError(LlmError):
    """vLLM rejected the supplied JSON Schema.

    Typically an HTTP 400 from vLLM when the schema uses a JSON Schema
    feature the active guided-decoding backend (xgrammar / outlines) does
    not yet support. The :class:`~lai.common.llm.client.LlmClient` catches
    this and falls back to looser JSON mode (see ADR 0002).

    Args:
        message: Human-readable description.
        schema_excerpt: A truncated snippet of the rejected schema, for log
            attribution. The full schema is rarely useful in logs and may
            contain customer-specific structure; keep it short.
    """

    def __init__(
        self,
        message: str,
        *,
        schema_excerpt: str | None = None,
    ) -> None:
        super().__init__(message)
        self.schema_excerpt: str | None = schema_excerpt


class LlmRetryExhaustedError(LlmError):
    """All retry attempts failed.

    The last underlying cause is chained via ``raise ... from``; access it
    through ``__cause__``. Callers distinguish this from a one-shot failure
    because the recovery actions usually differ (e.g., circuit-break vs.
    immediate fallback).

    Args:
        message: Human-readable description.
        attempts: Total number of attempts made (including the final failed
            one). Always ``>= 1``.

    Raises:
        ValueError: If ``attempts`` is less than 1.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
    ) -> None:
        if attempts < 1:
            raise ValueError(
                f"attempts must be >= 1, got {attempts}",
            )
        super().__init__(message)
        self.attempts: int = attempts


# ─────────────────────────────────────────────────────────────────────────────
# Reranker-related
#
# Parallel hierarchy to ``LlmError``: a base type for any failure interacting
# with the reranker service, plus three concrete subtypes mirroring the LLM
# call/content/retry-exhausted split. We do *not* reuse the LLM exceptions
# here because callers may want to distinguish a reranker failure from an
# LLM failure (different recovery actions: degrade ranking quality vs. abort
# generation entirely).
# ─────────────────────────────────────────────────────────────────────────────


class RerankerError(LaiCommonError):
    """Base for any failure interacting with the reranker service."""


class RerankerCallError(RerankerError):
    """Transport-level failure when calling the reranker.

    Covers HTTP errors, request timeouts, and connection refusals.

    Args:
        message: Human-readable description.
        status_code: HTTP status code, if the failure was a non-2xx response.
            ``None`` for pre-HTTP failures (timeout, DNS, connection refused).
        url: The endpoint that failed, for log attribution.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code
        self.url: str | None = url


class RerankerInvalidResponseError(RerankerError):
    """The reranker responded successfully but the content is unusable.

    Examples: response is not a JSON array; an entry is missing ``index``
    or ``score``; ``index`` is out of range for the input texts; ``score``
    is non-numeric.

    Args:
        message: Human-readable description.
        raw_response: The exact string the reranker returned, if available.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response: str | None = raw_response


class RerankerRetryExhaustedError(RerankerError):
    """All retry attempts against the reranker failed.

    Cause-chained via ``raise ... from`` like :class:`LlmRetryExhaustedError`.

    Args:
        message: Human-readable description.
        attempts: Total number of attempts made. Always ``>= 1``.

    Raises:
        ValueError: If ``attempts`` is less than 1.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
    ) -> None:
        if attempts < 1:
            raise ValueError(
                f"attempts must be >= 1, got {attempts}",
            )
        super().__init__(message)
        self.attempts: int = attempts


# ─────────────────────────────────────────────────────────────────────────────
# Embedding-related
#
# Parallel hierarchy to ``LlmError`` and ``RerankerError``. The embedding
# service is the third upstream the runtime talks to (after LLM and
# reranker); separating its failure modes lets callers degrade gracefully
# (e.g., fall through to BM25-only retrieval when embeddings are down).
# ─────────────────────────────────────────────────────────────────────────────


class EmbeddingError(LaiCommonError):
    """Base for any failure interacting with the embedding service."""


class EmbeddingCallError(EmbeddingError):
    """Transport-level failure when calling the embedding service.

    Covers HTTP errors, request timeouts, and connection refusals.

    Args:
        message: Human-readable description.
        status_code: HTTP status code, if the failure was a non-2xx response.
            ``None`` for pre-HTTP failures.
        url: The endpoint that failed, for log attribution.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code: int | None = status_code
        self.url: str | None = url


class EmbeddingInvalidResponseError(EmbeddingError):
    """The embedding service responded but the content is unusable.

    Examples: response is not a JSON object; missing ``data`` array; an
    entry lacks ``embedding``; ``index`` mapping is inconsistent.

    Args:
        message: Human-readable description.
        raw_response: The exact string the service returned, if available.
    """

    def __init__(
        self,
        message: str,
        *,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response: str | None = raw_response


class EmbeddingDimensionMismatchError(EmbeddingInvalidResponseError):
    """An embedding vector arrived with the wrong dimension.

    Surfaces a configuration drift (e.g., the served model changed) early,
    before the vector reaches pgvector and corrupts the index. Callers
    typically alert and refuse to serve further requests until reconciled.

    Args:
        message: Human-readable description.
        expected_dimension: The dimension :class:`EmbeddingConfig` was
            constructed with (e.g., 4096 for Qwen3-Embedding-8B).
        actual_dimension: The dimension of the offending vector.
        raw_response: Optional raw response excerpt for log attribution.
    """

    def __init__(
        self,
        message: str,
        *,
        expected_dimension: int,
        actual_dimension: int,
        raw_response: str | None = None,
    ) -> None:
        super().__init__(message, raw_response=raw_response)
        self.expected_dimension: int = expected_dimension
        self.actual_dimension: int = actual_dimension


class EmbeddingRetryExhaustedError(EmbeddingError):
    """All retry attempts against the embedding service failed.

    Cause-chained via ``raise ... from``. Same shape as
    :class:`LlmRetryExhaustedError` and :class:`RerankerRetryExhaustedError`.

    Args:
        message: Human-readable description.
        attempts: Total number of attempts made. Always ``>= 1``.

    Raises:
        ValueError: If ``attempts`` is less than 1.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
    ) -> None:
        if attempts < 1:
            raise ValueError(
                f"attempts must be >= 1, got {attempts}",
            )
        super().__init__(message)
        self.attempts: int = attempts


# ─────────────────────────────────────────────────────────────────────────────
# PDF-extraction-related
#
# The PDF extractor is a pure-local operation (no upstream service), so the
# hierarchy is shallower than the network-client families above. Failure
# modes split between unrecoverable input (corrupt / encrypted / not-a-PDF)
# and configuration (OCR engine unavailable).
# ─────────────────────────────────────────────────────────────────────────────


class PdfError(LaiCommonError):
    """Base for any failure in the PDF extractor."""


class PdfExtractError(PdfError):
    """The supplied bytes / path could not be processed as a PDF.

    Covers: malformed PDF header, password-protected document, zero pages,
    PyMuPDF / fitz raising on an unsupported feature. Callers route this to
    a user-visible "this file could not be processed" message rather than
    treating it as a transient error worth retrying.

    Args:
        message: Human-readable description.
        path: Filesystem path the extractor was asked to read, if any. Set
            to ``None`` when the extractor was given raw bytes.
        page_index: Zero-based page index where extraction failed, if the
            failure was page-localised. ``None`` if the document failed to
            open at all.
    """

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        page_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.path: str | None = path
        self.page_index: int | None = page_index


class PdfOcrUnavailableError(PdfError):
    """OCR fallback was needed but the OCR engine is not installed.

    Tesseract is an optional system dependency: production hosts have it,
    but unit-test containers may not. Surfacing this distinctly lets the
    caller decide whether a per-page text-quality miss is fatal or a
    graceful-degradation event.

    Args:
        message: Human-readable description.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Chunker-related
#
# Like the PDF extractor, chunking is local. The only realistic failure
# mode is invalid input (caller passed non-text, or a configuration that
# would produce no valid chunks). We surface this as a typed error so
# callers do not silently emit empty chunk lists.
# ─────────────────────────────────────────────────────────────────────────────


class ChunkError(LaiCommonError):
    """Base for any failure in the text chunker."""


class ChunkInvalidInputError(ChunkError):
    """The chunker was given input it cannot process.

    Examples: ``None`` instead of a string; a chunk-size configuration that
    would produce zero chunks; non-finite values in numeric parameters.

    Args:
        message: Human-readable description.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval-related (pgvector corpus search)
#
# The retrieval client talks to Postgres (pgvector). Failure modes split
# between connectivity (pool exhausted, server down, auth) and query-time
# errors (bad SQL, dimension mismatch on the query vector). Same shape as
# the network-client families above so callers can catch the base
# :class:`RetrievalError` or a specific subtype.
# ─────────────────────────────────────────────────────────────────────────────


class RetrievalError(LaiCommonError):
    """Base for any failure in the pgvector retrieval client."""


class RetrievalConnectionError(RetrievalError):
    """The client could not obtain or use a Postgres connection.

    Covers: pool exhausted, server unreachable, authentication failure,
    connection dropped mid-query. Callers treat this as transient — it is
    the error type the retry policy is built around.

    Args:
        message: Human-readable description.
    """


class RetrievalQueryError(RetrievalError):
    """A retrieval query was rejected or failed at the SQL level.

    Covers: malformed SQL, missing ``corpus_child_chunks`` table /
    ``vector`` extension, a query vector whose dimension does not match
    the indexed column. Not transient — retrying the same query produces
    the same failure, so the retry policy excludes this type.

    Args:
        message: Human-readable description.
    """


class RetrievalDimensionError(RetrievalQueryError):
    """The query vector dimension does not match the indexed column.

    The ``corpus_child_chunks.embedding`` column is ``halfvec(4000)``;
    a query vector must be truncatable to exactly that width. Raised
    before the query is sent so a misconfigured embedding model surfaces
    as a clear error rather than a Postgres type error.

    Args:
        message: Human-readable description.
        expected: The dimension the index expects (4000).
        actual: The dimension the supplied query vector had.

    Raises:
        ValueError: If either dimension is negative.
    """

    def __init__(
        self,
        message: str,
        *,
        expected: int,
        actual: int,
    ) -> None:
        if expected < 0 or actual < 0:
            raise ValueError(
                f"dimensions must be >= 0, got expected={expected} actual={actual}",
            )
        super().__init__(message)
        self.expected: int = expected
        self.actual: int = actual


class RetrievalRetryExhaustedError(RetrievalError):
    """All retry attempts against the retrieval backend failed.

    Cause-chained via ``raise ... from``. Same shape as
    :class:`EmbeddingRetryExhaustedError`.

    Args:
        message: Human-readable description.
        attempts: Total number of attempts made. Always ``>= 1``.

    Raises:
        ValueError: If ``attempts`` is less than 1.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
    ) -> None:
        if attempts < 1:
            raise ValueError(
                f"attempts must be >= 1, got {attempts}",
            )
        super().__init__(message)
        self.attempts: int = attempts
