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
    "LaiCommonError",
    "LlmCallError",
    "LlmEmptyResponseError",
    "LlmError",
    "LlmGuidedDecodingError",
    "LlmInvalidResponseError",
    "LlmJsonParseError",
    "LlmRetryExhaustedError",
    "LlmSchemaValidationError",
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
