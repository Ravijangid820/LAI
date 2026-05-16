"""Async-primary LLM client with a sync façade.

Implements ADRs 0001 (async-primary surface), 0002 (guided-JSON schema
enforcement via vLLM ``extra_body.guided_json``), and 0003 (server-side
``<think>``-trace stripping by default). Uses:

- :mod:`httpx` for HTTP transport (async + sync flavours).
- :mod:`tenacity` for retry with exponential backoff.
- :class:`~lai.common.llm.config.LlmConfig` for every knob.
- :class:`~lai.common.llm.metrics.LlmMetrics` for observability.
- :func:`~lai.common.llm.strip_think` and
  :func:`~lai.common.llm.salvage_json` as the post-response helpers.
- :class:`~lai.common.exceptions.LlmError` and subclasses for every
  failure mode.

The two clients are concrete classes that share module-level helper
functions (composition over inheritance). Each owns its own
:class:`~httpx.AsyncClient` / :class:`~httpx.Client` for connection
pooling. :class:`SyncLlmClient` is the migration aid for callers that
cannot yet adopt async (DDiQ's :class:`~concurrent.futures.ThreadPoolExecutor`
worker, the contract analyzer's sync entry points).
"""

from __future__ import annotations

import json
import time
from types import TracebackType
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast

import httpx
import structlog
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lai.common.exceptions import (
    LlmCallError,
    LlmEmptyResponseError,
    LlmGuidedDecodingError,
    LlmJsonParseError,
    LlmRetryExhaustedError,
    LlmSchemaValidationError,
)
from lai.common.llm.config import LlmConfig
from lai.common.llm.json_salvage import salvage_json
from lai.common.llm.metrics import LlmMetrics, default_metrics
from lai.common.llm.think_strip import strip_think

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["ChatMessage", "LlmClient", "SyncLlmClient"]

_log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)
"""Schema type variable for :meth:`LlmClient.generate_json`."""


class ChatMessage(BaseModel):
    """One message in a chat-completion request.

    Roles are deliberately narrowed to the three Qwen3 actually uses; if
    we later need ``tool`` / ``function`` we widen this enum with an ADR.
    """

    role: Literal["system", "user", "assistant"]
    content: str


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers — shared by the async and sync clients
# ─────────────────────────────────────────────────────────────────────────────


def _normalise_prompt(
    prompt: str | Sequence[ChatMessage],
    system: str | None,
) -> list[dict[str, str]]:
    """Build the OpenAI-style ``messages`` array from the public surface.

    A bare ``str`` becomes a single user message. A sequence of
    :class:`ChatMessage` is converted to OpenAI dict form. ``system`` is
    prepended (or merged, if the sequence already starts with a system
    message — last-writer-wins is rarely useful, so we **error** in that
    case so the caller is explicit).
    """
    if isinstance(prompt, str):
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    else:
        messages = [{"role": m.role, "content": m.content} for m in prompt]

    if system is None:
        return messages

    if messages and messages[0]["role"] == "system":
        raise ValueError(
            "system message supplied via both `system=` and the messages list; " "choose one",
        )
    return [{"role": "system", "content": system}, *messages]


def _build_request_body(
    *,
    config: LlmConfig,
    messages: list[dict[str, str]],
    max_tokens: int | None,
    temperature: float | None,
    stop: Sequence[str] | None,
    guided_json: dict[str, Any] | None,
    json_object_mode: bool,
    keep_thinking: bool,
) -> dict[str, Any]:
    """Assemble the JSON body for the OpenAI-compatible chat-completions endpoint.

    All vLLM-specific knobs live under ``extra_body`` so the OpenAI Python
    SDK could be swapped in later without changing the surface.
    """
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else config.default_max_tokens,
        "temperature": (temperature if temperature is not None else config.default_temperature),
        "stream": False,
    }
    if stop is not None:
        body["stop"] = list(stop)

    extra_body: dict[str, Any] = {}
    if guided_json is not None:
        extra_body["guided_json"] = guided_json
        extra_body["guided_decoding_backend"] = config.guided_decoding_backend
    if json_object_mode:
        body["response_format"] = {"type": "json_object"}
    # ``keep_thinking`` is a response-side concern (whether the client
    # strips the trace before returning); ``config.thinking_mode_enabled``
    # is the request-side concern (whether the model emits the trace at
    # all). We only forward the model-side flag when the caller has
    # disabled thinking globally. ``keep_thinking`` here is unused —
    # accepted in the helper signature so callers don't have to
    # special-case it.
    _ = keep_thinking
    if not config.thinking_mode_enabled:
        extra_body.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
    if extra_body:
        body["extra_body"] = extra_body
    return body


def _headers(config: LlmConfig) -> dict[str, str]:
    """Build the request headers, including bearer auth if configured."""
    headers = {"Content-Type": "application/json"}
    if config.api_key is not None:
        headers["Authorization"] = f"Bearer {config.api_key.get_secret_value()}"
    return headers


def _parse_chat_response(
    raw: dict[str, Any],
    config: LlmConfig,
) -> tuple[str, dict[str, int]]:
    """Extract ``(content, usage)`` from an OpenAI-style chat-completion.

    Raises :class:`LlmEmptyResponseError` if the content is null / empty.
    """
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmEmptyResponseError(
            "response had no choices",
            raw_response=json.dumps(raw),
        )
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or content == "":
        raise LlmEmptyResponseError(
            f"empty content from model {config.model!r}",
            raw_response=json.dumps(raw),
        )
    usage_raw = raw.get("usage") or {}
    # vLLM returns usage as ints but defensively cast — older endpoints
    # sometimes emit them as strings.
    usage: dict[str, int] = {
        "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
    }
    return content, usage


def _classify_http_error(
    exc: httpx.HTTPStatusError,
    url: str,
) -> LlmCallError | LlmGuidedDecodingError:
    """Map an :class:`httpx.HTTPStatusError` to a typed LAI exception.

    HTTP 400 against a request that carried ``guided_json`` is the
    documented signal for vLLM rejecting the schema; we surface it as
    :class:`LlmGuidedDecodingError` so :meth:`LlmClient.generate_json`
    can fall back to loose JSON mode.
    """
    response = exc.response
    body_excerpt = response.text[:500]
    if response.status_code == 400:
        return LlmGuidedDecodingError(
            f"vLLM rejected guided_json: {body_excerpt}",
            schema_excerpt=body_excerpt,
        )
    return LlmCallError(
        f"HTTP {response.status_code} from {url}: {body_excerpt}",
        status_code=response.status_code,
        url=url,
    )


def _validate_against_schema(text: str, schema: type[T]) -> T:
    """Parse ``text`` as JSON (with salvage) and validate against ``schema``.

    Raises :class:`LlmJsonParseError` on parse failure (already raised by
    :func:`salvage_json`) and :class:`LlmSchemaValidationError` on Pydantic
    validation failure.
    """
    parsed = salvage_json(text)  # raises LlmJsonParseError on failure
    try:
        return schema.model_validate(parsed)
    except ValidationError as exc:
        raise LlmSchemaValidationError(
            f"response did not match {schema.__name__}",
            raw_response=text,
            validation_errors=[{k: v for k, v in err.items() if k in ("loc", "msg", "type")} for err in exc.errors()],
        ) from exc


def _estimate_thinking_tokens(raw: str, stripped: str) -> int:
    """Rough char-delta-based estimate of tokens consumed by ``<think>`` blocks.

    A four-character-per-token heuristic. Used only for the
    ``lai_llm_tokens_total{kind=thinking}`` Prometheus counter to track
    *trend* over time; not accurate enough to bill against.
    """
    delta = max(0, len(raw) - len(stripped))
    return delta // 4


# ─────────────────────────────────────────────────────────────────────────────
# Async client (the canonical surface — ADR 0001)
# ─────────────────────────────────────────────────────────────────────────────


class LlmClient:
    """Async LLM client.

    Construct once at process start, share across coroutines. The
    underlying :class:`httpx.AsyncClient` is created in ``__init__`` and
    closed by :meth:`aclose` (or via ``async with`` usage).

    Args:
        config: :class:`LlmConfig` instance; defaults to ``LlmConfig()``
            which reads from environment variables.
        metrics: Optional :class:`LlmMetrics` bundle for observability;
            defaults to the module-level singleton registered against the
            default Prometheus registry.
        transport: Optional :class:`httpx.AsyncHTTPTransport` for tests
            that need to inject a mock transport. Production callers
            should leave this unset.
    """

    def __init__(
        self,
        config: LlmConfig | None = None,
        *,
        metrics: LlmMetrics | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config or LlmConfig()
        self._metrics = metrics or default_metrics
        self._http = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            headers=_headers(self._config),
            transport=transport,
        )

    async def __aenter__(self) -> LlmClient:
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

    # ── Public methods ─────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str | Sequence[ChatMessage],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
        keep_thinking: bool = False,
    ) -> str:
        """Free-form completion.

        Args:
            prompt: A bare ``str`` (single user message) or a sequence of
                :class:`ChatMessage` for multi-turn.
            system: Optional system-message prefix. Mutually exclusive
                with a ``ChatMessage(role="system", ...)`` at the start of
                ``prompt``.
            max_tokens: Override ``config.default_max_tokens``.
            temperature: Override ``config.default_temperature``.
            stop: Optional stop-sequence list.
            keep_thinking: If ``True``, preserve ``<think>...</think>``
                blocks in the returned text. Default is to strip them
                (ADR 0003).

        Returns:
            The model's response text, with reasoning traces stripped
            unless ``keep_thinking=True``.

        Raises:
            LlmCallError: Transport-level failure (after retries exhausted).
            LlmEmptyResponseError: Response was empty / null (after retries).
            LlmRetryExhaustedError: All retries failed.
        """
        messages = _normalise_prompt(prompt, system)
        body = _build_request_body(
            config=self._config,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            guided_json=None,
            json_object_mode=False,
            keep_thinking=keep_thinking,
        )
        raw_content, usage = await self._call_with_retry(body)
        final = raw_content if keep_thinking else strip_think(raw_content)
        self._record_token_usage(usage, raw_content, final)
        return final

    async def generate_json(
        self,
        schema: type[T],
        prompt: str | Sequence[ChatMessage],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> T:
        """Schema-enforced structured-output completion.

        Uses vLLM's ``extra_body.guided_json`` (ADR 0002) so the model
        cannot emit JSON that fails the schema's structural constraints.
        Falls back to looser JSON mode + :func:`salvage_json` if vLLM
        rejects the schema (HTTP 400).

        Args:
            schema: A :class:`pydantic.BaseModel` subclass — the structure
                the response must satisfy.
            prompt: Same shape as :meth:`generate`.
            system: Optional system message.
            max_tokens: Override default.
            temperature: Override default.
            stop: Optional stop-sequence list.

        Returns:
            A validated instance of ``schema``.

        Raises:
            LlmCallError: Transport-level failure.
            LlmJsonParseError: Could not parse JSON even after salvage.
            LlmSchemaValidationError: Parsed but failed schema validation.
            LlmRetryExhaustedError: All retries failed.
        """
        messages = _normalise_prompt(prompt, system)
        schema_dict = schema.model_json_schema()

        # Primary: guided decoding. vLLM enforces the schema at sampler
        # level — invalid JSON is structurally impossible.
        body = _build_request_body(
            config=self._config,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            guided_json=schema_dict,
            json_object_mode=False,
            keep_thinking=False,
        )
        try:
            raw_content, usage = await self._call_with_retry(body)
        except LlmGuidedDecodingError as exc:
            # Fallback: re-issue without guided_json but with
            # response_format=json_object, then salvage + validate.
            self._metrics.schema_failures_total.labels(
                model=self._config.model,
                kind="guided_decoding_rejected",
            ).inc()
            _log.warning(
                "llm.guided_decoding.rejected",
                model=self._config.model,
                schema=schema.__name__,
                excerpt=exc.schema_excerpt,
            )
            body = _build_request_body(
                config=self._config,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                guided_json=None,
                json_object_mode=True,
                keep_thinking=False,
            )
            raw_content, usage = await self._call_with_retry(body)

        cleaned = strip_think(raw_content)
        self._record_token_usage(usage, raw_content, cleaned)

        try:
            return _validate_against_schema(cleaned, schema)
        except LlmJsonParseError:
            self._metrics.schema_failures_total.labels(
                model=self._config.model,
                kind="parse",
            ).inc()
            raise
        except LlmSchemaValidationError:
            self._metrics.schema_failures_total.labels(
                model=self._config.model,
                kind="validation",
            ).inc()
            raise

    # ── Internals ──────────────────────────────────────────────────────

    async def _call_with_retry(self, body: dict[str, Any]) -> tuple[str, dict[str, int]]:
        """Apply the configured retry policy around :meth:`_call_once`.

        Retries on :class:`LlmCallError` (transport errors) and on
        :class:`LlmEmptyResponseError` (Qwen3's spurious-empty completions).
        Does *not* retry on :class:`LlmGuidedDecodingError` — schema
        rejection is deterministic and the fallback path handles it.
        """
        attempt_number = 0
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(
                    (LlmCallError, LlmEmptyResponseError),
                ),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.labels(
                            model=self._config.model,
                        ).inc()
                    return await self._call_once(body)
        except RetryError as exc:
            # tenacity guarantees `last_attempt` is set whenever RetryError
            # is raised. The cause-chain is what makes `from cause` show
            # the underlying transport / empty-response failure in tracebacks.
            cause = exc.last_attempt.exception()
            raise LlmRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
            ) from cause
        # AsyncRetrying always yields at least once; this line is
        # unreachable but the type-checker can't prove it.
        raise LlmRetryExhaustedError(  # pragma: no cover
            "AsyncRetrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    async def _call_once(self, body: dict[str, Any]) -> tuple[str, dict[str, int]]:
        """One HTTP round-trip + metric/log emission."""
        url = "chat/completions"
        full_url = f"{self._config.base_url}/{url}"
        start = time.perf_counter()
        status_label = "error"
        try:
            response = await self._http.post(url, json=body)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise _classify_http_error(exc, full_url) from exc
            try:
                raw = response.json()
            except json.JSONDecodeError as exc:
                raise LlmCallError(
                    f"non-JSON response body: {response.text[:200]}",
                    status_code=response.status_code,
                    url=full_url,
                ) from exc
            content, usage = _parse_chat_response(raw, self._config)
            status_label = "success"
            return content, usage
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LlmCallError(
                f"transport error to {full_url}: {exc}",
                url=full_url,
            ) from exc
        except LlmEmptyResponseError:
            self._metrics.empty_responses_total.labels(model=self._config.model).inc()
            raise
        finally:
            duration = time.perf_counter() - start
            self._metrics.calls_total.labels(model=self._config.model, status=status_label).inc()
            self._metrics.request_duration_seconds.labels(model=self._config.model, status=status_label).observe(
                duration
            )
            _log.info(
                "llm.call.complete",
                model=self._config.model,
                duration_seconds=round(duration, 3),
                status=status_label,
            )

    def _record_token_usage(self, usage: dict[str, int], raw: str, final: str) -> None:
        """Update token counters, including the thinking-token estimate."""
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        if prompt:
            self._metrics.tokens_total.labels(model=self._config.model, kind="prompt").inc(prompt)
        if completion:
            self._metrics.tokens_total.labels(model=self._config.model, kind="completion").inc(completion)
        thinking_estimate = _estimate_thinking_tokens(raw, final)
        if thinking_estimate:
            self._metrics.tokens_total.labels(model=self._config.model, kind="thinking").inc(thinking_estimate)


# ─────────────────────────────────────────────────────────────────────────────
# Sync façade — migration aid for callers that cannot adopt async yet
# ─────────────────────────────────────────────────────────────────────────────


class SyncLlmClient(Generic[T]):
    """Sync version of :class:`LlmClient`.

    Holds its own :class:`httpx.Client` (sync). Shares no state with
    :class:`LlmClient`; the two clients are interchangeable from the
    caller's perspective modulo ``async``/``await``.

    Existence justified by ADR 0001 — DDiQ's
    :class:`~concurrent.futures.ThreadPoolExecutor` worker and the
    contract analyzer's sync entry points cannot yet adopt async.
    """

    def __init__(
        self,
        config: LlmConfig | None = None,
        *,
        metrics: LlmMetrics | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config or LlmConfig()
        self._metrics = metrics or default_metrics
        self._http = httpx.Client(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            headers=_headers(self._config),
            transport=transport,
        )

    def __enter__(self) -> SyncLlmClient[T]:
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

    def generate(
        self,
        prompt: str | Sequence[ChatMessage],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
        keep_thinking: bool = False,
    ) -> str:
        """Sync counterpart to :meth:`LlmClient.generate`."""
        messages = _normalise_prompt(prompt, system)
        body = _build_request_body(
            config=self._config,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            guided_json=None,
            json_object_mode=False,
            keep_thinking=keep_thinking,
        )
        raw_content, usage = self._call_with_retry(body)
        final = raw_content if keep_thinking else strip_think(raw_content)
        self._record_token_usage(usage, raw_content, final)
        return final

    def generate_json(
        self,
        schema: type[T],
        prompt: str | Sequence[ChatMessage],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] | None = None,
    ) -> T:
        """Sync counterpart to :meth:`LlmClient.generate_json`."""
        messages = _normalise_prompt(prompt, system)
        schema_dict = schema.model_json_schema()
        body = _build_request_body(
            config=self._config,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            guided_json=schema_dict,
            json_object_mode=False,
            keep_thinking=False,
        )
        try:
            raw_content, usage = self._call_with_retry(body)
        except LlmGuidedDecodingError as exc:
            self._metrics.schema_failures_total.labels(
                model=self._config.model,
                kind="guided_decoding_rejected",
            ).inc()
            _log.warning(
                "llm.guided_decoding.rejected",
                model=self._config.model,
                schema=schema.__name__,
                excerpt=exc.schema_excerpt,
            )
            body = _build_request_body(
                config=self._config,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
                guided_json=None,
                json_object_mode=True,
                keep_thinking=False,
            )
            raw_content, usage = self._call_with_retry(body)

        cleaned = strip_think(raw_content)
        self._record_token_usage(usage, raw_content, cleaned)

        try:
            return _validate_against_schema(cleaned, schema)
        except LlmJsonParseError:
            self._metrics.schema_failures_total.labels(
                model=self._config.model,
                kind="parse",
            ).inc()
            raise
        except LlmSchemaValidationError:
            self._metrics.schema_failures_total.labels(
                model=self._config.model,
                kind="validation",
            ).inc()
            raise

    def _call_with_retry(self, body: dict[str, Any]) -> tuple[str, dict[str, int]]:
        """Sync mirror of :meth:`LlmClient._call_with_retry`."""
        attempt_number = 0
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(
                    (LlmCallError, LlmEmptyResponseError),
                ),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.labels(
                            model=self._config.model,
                        ).inc()
                    return self._call_once(body)
        except RetryError as exc:
            # tenacity guarantees `last_attempt` is set whenever RetryError
            # is raised. The cause-chain is what makes `from cause` show
            # the underlying transport / empty-response failure in tracebacks.
            cause = exc.last_attempt.exception()
            raise LlmRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
            ) from cause
        raise LlmRetryExhaustedError(  # pragma: no cover
            "Retrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    def _call_once(self, body: dict[str, Any]) -> tuple[str, dict[str, int]]:
        """Sync mirror of :meth:`LlmClient._call_once`."""
        url = "chat/completions"
        full_url = f"{self._config.base_url}/{url}"
        start = time.perf_counter()
        status_label = "error"
        try:
            response = self._http.post(url, json=body)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise _classify_http_error(exc, full_url) from exc
            try:
                raw = cast("dict[str, Any]", response.json())
            except json.JSONDecodeError as exc:
                raise LlmCallError(
                    f"non-JSON response body: {response.text[:200]}",
                    status_code=response.status_code,
                    url=full_url,
                ) from exc
            content, usage = _parse_chat_response(raw, self._config)
            status_label = "success"
            return content, usage
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LlmCallError(
                f"transport error to {full_url}: {exc}",
                url=full_url,
            ) from exc
        except LlmEmptyResponseError:
            self._metrics.empty_responses_total.labels(model=self._config.model).inc()
            raise
        finally:
            duration = time.perf_counter() - start
            self._metrics.calls_total.labels(model=self._config.model, status=status_label).inc()
            self._metrics.request_duration_seconds.labels(model=self._config.model, status=status_label).observe(
                duration
            )
            _log.info(
                "llm.call.complete",
                model=self._config.model,
                duration_seconds=round(duration, 3),
                status=status_label,
            )

    def _record_token_usage(self, usage: dict[str, int], raw: str, final: str) -> None:
        """Mirror of the async client's token-usage recorder."""
        prompt = usage.get("prompt_tokens", 0)
        completion = usage.get("completion_tokens", 0)
        if prompt:
            self._metrics.tokens_total.labels(model=self._config.model, kind="prompt").inc(prompt)
        if completion:
            self._metrics.tokens_total.labels(model=self._config.model, kind="completion").inc(completion)
        thinking_estimate = _estimate_thinking_tokens(raw, final)
        if thinking_estimate:
            self._metrics.tokens_total.labels(model=self._config.model, kind="thinking").inc(thinking_estimate)
