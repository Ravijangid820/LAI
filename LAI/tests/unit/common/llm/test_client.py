"""Tests for :class:`lai.common.llm.client.LlmClient` and
:class:`SyncLlmClient`.

Every HTTP round-trip is mocked via :class:`httpx.MockTransport`, every
metric assertion runs against an isolated :class:`CollectorRegistry`, and
every retry test uses tiny backoff so the suite stays fast.

Layout:

1. Pure-helper tests (the module-level functions).
2. Async client tests (``LlmClient``).
3. Sync client tests (``SyncLlmClient``).
4. Sync/async parity assertions where the behaviour must agree.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from prometheus_client import CollectorRegistry
from pydantic import BaseModel

from lai.common.exceptions import (
    LlmCallError,
    LlmEmptyResponseError,
    LlmGuidedDecodingError,
    LlmJsonParseError,
    LlmRetryExhaustedError,
    LlmSchemaValidationError,
)
from lai.common.llm import ChatMessage, LlmClient, SyncLlmClient
from lai.common.llm.client import (
    _build_request_body,
    _classify_http_error,
    _estimate_thinking_tokens,
    _headers,
    _normalise_prompt,
    _parse_chat_response,
    _validate_against_schema,
)
from lai.common.llm.config import LlmConfig
from lai.common.llm.metrics import LlmMetrics

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def metrics(registry: CollectorRegistry) -> LlmMetrics:
    return LlmMetrics(registry=registry)


@pytest.fixture
def config() -> LlmConfig:
    """Config with tiny retry backoff so retry tests don't hang the suite."""
    return LlmConfig(
        base_url="http://test-llm:8000/v1",
        model="qwen-test",
        max_retries=2,
        retry_initial_wait_seconds=0.001,
        retry_max_wait_seconds=0.001,
        default_max_tokens=1024,
        default_temperature=0.0,
    )


def _chat_response(
    content: str,
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
) -> dict[str, Any]:
    """Build a vLLM-shaped OpenAI-compatible chat completion."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "qwen-test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _mock_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    """Wrap a request handler in a :class:`httpx.MockTransport`."""
    return httpx.MockTransport(handler)


class _ExtractionSchema(BaseModel):
    """Sample Pydantic schema for guided-decoding tests."""

    label: str
    score: float


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalisePrompt:
    @pytest.mark.unit
    def test_bare_string_becomes_user_message(self) -> None:
        assert _normalise_prompt("hello", system=None) == [
            {"role": "user", "content": "hello"},
        ]

    @pytest.mark.unit
    def test_message_list_is_passed_through(self) -> None:
        messages = [
            ChatMessage(role="user", content="q1"),
            ChatMessage(role="assistant", content="a1"),
            ChatMessage(role="user", content="q2"),
        ]
        assert _normalise_prompt(messages, system=None) == [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]

    @pytest.mark.unit
    def test_system_string_is_prepended_to_bare_string(self) -> None:
        assert _normalise_prompt("hello", system="be terse") == [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hello"},
        ]

    @pytest.mark.unit
    def test_system_string_is_prepended_to_list(self) -> None:
        messages = [ChatMessage(role="user", content="q")]
        result = _normalise_prompt(messages, system="be terse")
        assert result[0] == {"role": "system", "content": "be terse"}
        assert result[1] == {"role": "user", "content": "q"}

    @pytest.mark.unit
    def test_double_system_raises(self) -> None:
        messages = [
            ChatMessage(role="system", content="from list"),
            ChatMessage(role="user", content="q"),
        ]
        with pytest.raises(ValueError, match="system message"):
            _normalise_prompt(messages, system="from kwarg")


class TestBuildRequestBody:
    @pytest.mark.unit
    def test_defaults_from_config(self, config: LlmConfig) -> None:
        body = _build_request_body(
            config=config,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            temperature=None,
            stop=None,
            guided_json=None,
            json_object_mode=False,
            keep_thinking=False,
        )
        assert body["model"] == "qwen-test"
        assert body["max_tokens"] == 1024
        assert body["temperature"] == 0.0
        assert body["stream"] is False
        assert "stop" not in body
        assert "response_format" not in body
        # thinking_mode_enabled defaults to True so no chat_template_kwargs
        assert "extra_body" not in body

    @pytest.mark.unit
    def test_overrides_take_effect(self, config: LlmConfig) -> None:
        body = _build_request_body(
            config=config,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=42,
            temperature=0.7,
            stop=["END", "STOP"],
            guided_json=None,
            json_object_mode=False,
            keep_thinking=False,
        )
        assert body["max_tokens"] == 42
        assert body["temperature"] == pytest.approx(0.7)
        assert body["stop"] == ["END", "STOP"]

    @pytest.mark.unit
    def test_guided_json_attaches_schema_and_backend(self, config: LlmConfig) -> None:
        schema: dict[str, Any] = {"type": "object", "properties": {"a": {"type": "integer"}}}
        body = _build_request_body(
            config=config,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            temperature=None,
            stop=None,
            guided_json=schema,
            json_object_mode=False,
            keep_thinking=False,
        )
        assert body["extra_body"]["guided_json"] == schema
        assert body["extra_body"]["guided_decoding_backend"] == "xgrammar"

    @pytest.mark.unit
    def test_json_object_mode_sets_response_format(self, config: LlmConfig) -> None:
        body = _build_request_body(
            config=config,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            temperature=None,
            stop=None,
            guided_json=None,
            json_object_mode=True,
            keep_thinking=False,
        )
        assert body["response_format"] == {"type": "json_object"}

    @pytest.mark.unit
    def test_thinking_mode_disabled_forwards_chat_template_kwargs(self) -> None:
        cfg = LlmConfig(thinking_mode_enabled=False)
        body = _build_request_body(
            config=cfg,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            temperature=None,
            stop=None,
            guided_json=None,
            json_object_mode=False,
            keep_thinking=False,
        )
        assert body["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False

    @pytest.mark.unit
    def test_keep_thinking_is_response_side_only(self, config: LlmConfig) -> None:
        """``keep_thinking`` does not change the model-side request body."""
        body_a = _build_request_body(
            config=config,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            temperature=None,
            stop=None,
            guided_json=None,
            json_object_mode=False,
            keep_thinking=True,
        )
        body_b = _build_request_body(
            config=config,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=None,
            temperature=None,
            stop=None,
            guided_json=None,
            json_object_mode=False,
            keep_thinking=False,
        )
        assert body_a == body_b


class TestHeaders:
    @pytest.mark.unit
    def test_default_headers_have_content_type_only(self) -> None:
        headers = _headers(LlmConfig())
        assert headers == {"Content-Type": "application/json"}

    @pytest.mark.unit
    def test_api_key_adds_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LAI_LLM_API_KEY", "sk-secret")
        headers = _headers(LlmConfig())
        assert headers["Authorization"] == "Bearer sk-secret"


class TestParseChatResponse:
    @pytest.mark.unit
    def test_success(self, config: LlmConfig) -> None:
        raw = _chat_response("hello", prompt_tokens=12, completion_tokens=3)
        content, usage = _parse_chat_response(raw, config)
        assert content == "hello"
        assert usage == {"prompt_tokens": 12, "completion_tokens": 3}

    @pytest.mark.unit
    def test_empty_content_raises(self, config: LlmConfig) -> None:
        raw = _chat_response("")
        with pytest.raises(LlmEmptyResponseError, match="empty content"):
            _parse_chat_response(raw, config)

    @pytest.mark.unit
    def test_null_content_raises(self, config: LlmConfig) -> None:
        raw = _chat_response("placeholder")  # we'll mutate
        raw["choices"][0]["message"]["content"] = None
        with pytest.raises(LlmEmptyResponseError):
            _parse_chat_response(raw, config)

    @pytest.mark.unit
    def test_missing_choices_raises(self, config: LlmConfig) -> None:
        raw: dict[str, Any] = {"object": "chat.completion"}
        with pytest.raises(LlmEmptyResponseError, match="no choices"):
            _parse_chat_response(raw, config)

    @pytest.mark.unit
    def test_empty_choices_list_raises(self, config: LlmConfig) -> None:
        raw: dict[str, Any] = {"choices": []}
        with pytest.raises(LlmEmptyResponseError, match="no choices"):
            _parse_chat_response(raw, config)

    @pytest.mark.unit
    def test_missing_usage_returns_zero_counts(self, config: LlmConfig) -> None:
        raw = _chat_response("hello")
        del raw["usage"]
        _, usage = _parse_chat_response(raw, config)
        assert usage == {"prompt_tokens": 0, "completion_tokens": 0}

    @pytest.mark.unit
    def test_string_token_counts_are_cast_to_int(self, config: LlmConfig) -> None:
        """Older endpoints occasionally emit usage values as strings."""
        raw = _chat_response("hello")
        raw["usage"] = {"prompt_tokens": "12", "completion_tokens": "3"}
        _, usage = _parse_chat_response(raw, config)
        assert usage == {"prompt_tokens": 12, "completion_tokens": 3}


class TestClassifyHttpError:
    @pytest.mark.unit
    def test_400_becomes_guided_decoding_error(self) -> None:
        response = httpx.Response(400, text="schema rejected")
        request = httpx.Request("POST", "http://x/v1/chat/completions")
        response.request = request
        exc = httpx.HTTPStatusError("bad", request=request, response=response)
        out = _classify_http_error(exc, "http://x/v1/chat/completions")
        assert isinstance(out, LlmGuidedDecodingError)
        assert out.schema_excerpt == "schema rejected"

    @pytest.mark.unit
    @pytest.mark.parametrize("status", [401, 403, 500, 502, 503])
    def test_other_statuses_become_call_error(self, status: int) -> None:
        response = httpx.Response(status, text=f"error {status}")
        request = httpx.Request("POST", "http://x/v1/chat/completions")
        response.request = request
        exc = httpx.HTTPStatusError("bad", request=request, response=response)
        out = _classify_http_error(exc, "http://x/v1/chat/completions")
        assert isinstance(out, LlmCallError)
        assert out.status_code == status
        assert out.url == "http://x/v1/chat/completions"


class TestValidateAgainstSchema:
    @pytest.mark.unit
    def test_happy_path(self) -> None:
        result = _validate_against_schema(
            '{"label": "red", "score": 0.9}',
            _ExtractionSchema,
        )
        assert result == _ExtractionSchema(label="red", score=0.9)

    @pytest.mark.unit
    def test_parse_failure_raises_json_parse_error(self) -> None:
        with pytest.raises(LlmJsonParseError):
            _validate_against_schema("not json @@@", _ExtractionSchema)

    @pytest.mark.unit
    def test_schema_failure_raises_schema_validation_error(self) -> None:
        with pytest.raises(LlmSchemaValidationError) as exc_info:
            _validate_against_schema(
                '{"label": "red"}',  # missing 'score'
                _ExtractionSchema,
            )
        assert exc_info.value.raw_response is not None
        assert any("score" in str(err.get("loc", "")) for err in exc_info.value.validation_errors)


class TestEstimateThinkingTokens:
    @pytest.mark.unit
    def test_no_delta_returns_zero(self) -> None:
        assert _estimate_thinking_tokens("hello", "hello") == 0

    @pytest.mark.unit
    def test_positive_delta_divides_by_four(self) -> None:
        # 40 char delta → 10 tokens
        assert _estimate_thinking_tokens("a" * 50, "a" * 10) == 10

    @pytest.mark.unit
    def test_negative_delta_clamped_to_zero(self) -> None:
        """Defensive: if stripped is longer than raw (shouldn't happen)."""
        assert _estimate_thinking_tokens("short", "way longer") == 0


# ─────────────────────────────────────────────────────────────────────────────
# Async client — generate()
# ─────────────────────────────────────────────────────────────────────────────


class TestAsyncGenerate:
    @pytest.mark.unit
    async def test_basic_success(self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_response("Hello, world."))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = await client.generate("Hi there")

        assert result == "Hello, world."
        # Request shape sanity
        assert captured["body"]["model"] == "qwen-test"
        assert captured["body"]["messages"] == [{"role": "user", "content": "Hi there"}]
        # Metrics
        assert (
            registry.get_sample_value(
                "lai_llm_calls_total",
                {"model": "qwen-test", "status": "success"},
            )
            == 1.0
        )
        assert (
            registry.get_sample_value(
                "lai_llm_tokens_total",
                {"model": "qwen-test", "kind": "prompt"},
            )
            == 100.0
        )

    @pytest.mark.unit
    async def test_strips_think_block_by_default(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_chat_response("<think>step 1</think>The answer is 42."),
            )

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = await client.generate("q")

        assert result == "The answer is 42."

    @pytest.mark.unit
    async def test_keep_thinking_preserves_trace(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_chat_response("<think>step 1</think>The answer is 42."),
            )

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = await client.generate("q", keep_thinking=True)

        assert result == "<think>step 1</think>The answer is 42."

    @pytest.mark.unit
    async def test_overrides_propagate_to_request(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_response("ok"))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            await client.generate(
                "q",
                system="be precise",
                max_tokens=42,
                temperature=0.5,
                stop=["END"],
            )

        body = captured["body"]
        assert body["messages"][0] == {"role": "system", "content": "be precise"}
        assert body["max_tokens"] == 42
        assert body["temperature"] == pytest.approx(0.5)
        assert body["stop"] == ["END"]


# ─────────────────────────────────────────────────────────────────────────────
# Async client — generate_json()
# ─────────────────────────────────────────────────────────────────────────────


class TestAsyncGenerateJson:
    @pytest.mark.unit
    async def test_guided_decoding_happy_path(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_chat_response('{"label": "red", "score": 0.9}'))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = await client.generate_json(_ExtractionSchema, "extract")

        assert result == _ExtractionSchema(label="red", score=0.9)
        # The request carried the JSON schema
        assert "guided_json" in captured["body"]["extra_body"]
        assert captured["body"]["extra_body"]["guided_decoding_backend"] == "xgrammar"

    @pytest.mark.unit
    async def test_falls_back_when_guided_decoding_is_rejected(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        request_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            request_count["n"] += 1
            body = json.loads(request.content)
            if "guided_json" in body.get("extra_body", {}):
                return httpx.Response(400, text="schema feature unsupported")
            # Fallback path: response_format=json_object should be present
            assert body["response_format"] == {"type": "json_object"}
            return httpx.Response(200, json=_chat_response('{"label": "blue", "score": 0.1}'))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = await client.generate_json(_ExtractionSchema, "extract")

        assert result == _ExtractionSchema(label="blue", score=0.1)
        assert request_count["n"] == 2
        # Guided decoding rejection counter incremented
        assert (
            registry.get_sample_value(
                "lai_llm_schema_failures_total",
                {"model": "qwen-test", "kind": "guided_decoding_rejected"},
            )
            == 1.0
        )

    @pytest.mark.unit
    async def test_schema_validation_failure(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        """LLM returned valid JSON of the wrong shape."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_chat_response('{"label": "red"}'),  # missing score
            )

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            with pytest.raises(LlmSchemaValidationError):
                await client.generate_json(_ExtractionSchema, "extract")

        assert (
            registry.get_sample_value(
                "lai_llm_schema_failures_total",
                {"model": "qwen-test", "kind": "validation"},
            )
            == 1.0
        )

    @pytest.mark.unit
    async def test_json_parse_failure(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        """LLM returned non-JSON in JSON mode (salvage exhausted)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("just prose, no JSON"))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            with pytest.raises(LlmJsonParseError):
                await client.generate_json(_ExtractionSchema, "extract")

        assert (
            registry.get_sample_value(
                "lai_llm_schema_failures_total",
                {"model": "qwen-test", "kind": "parse"},
            )
            == 1.0
        )


# ─────────────────────────────────────────────────────────────────────────────
# Async client — retry behaviour
# ─────────────────────────────────────────────────────────────────────────────


class TestAsyncRetries:
    @pytest.mark.unit
    async def test_retries_on_5xx_then_succeeds(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 3:
                return httpx.Response(503, text="overloaded")
            return httpx.Response(200, json=_chat_response("recovered"))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = await client.generate("q")

        assert result == "recovered"
        assert len(attempts) == 3
        # retries_total counts the retry *attempts* (i.e., the 2nd and 3rd).
        assert registry.get_sample_value("lai_llm_retries_total", {"model": "qwen-test"}) == 2.0

    @pytest.mark.unit
    async def test_retry_exhausted_raises(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="always down")

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            with pytest.raises(LlmRetryExhaustedError) as exc_info:
                await client.generate("q")

        # max_retries=2 in fixture → total attempts = 3
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.__cause__, LlmCallError)

    @pytest.mark.unit
    async def test_retries_on_empty_response(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                return httpx.Response(200, json=_chat_response(""))
            return httpx.Response(200, json=_chat_response("ok"))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = await client.generate("q")

        assert result == "ok"
        # empty_responses_total incremented on the failed attempt
        assert registry.get_sample_value("lai_llm_empty_responses_total", {"model": "qwen-test"}) == 1.0

    @pytest.mark.unit
    async def test_timeout_becomes_call_error(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("upstream timeout", request=request)

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            with pytest.raises(LlmRetryExhaustedError) as exc_info:
                await client.generate("q")

        # Underlying cause is a LlmCallError (transport mapped to typed exc)
        assert isinstance(exc_info.value.__cause__, LlmCallError)

    @pytest.mark.unit
    async def test_non_json_response_body_becomes_call_error(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            with pytest.raises(LlmRetryExhaustedError) as exc_info:
                await client.generate("q")

        assert isinstance(exc_info.value.__cause__, LlmCallError)


# ─────────────────────────────────────────────────────────────────────────────
# Async client — lifecycle
# ─────────────────────────────────────────────────────────────────────────────


class TestAsyncTokenAccounting:
    @pytest.mark.unit
    async def test_zero_token_counts_skip_metric_increments(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        """Zero token counts must not register any series on tokens_total.

        Covers the false branches of the ``if prompt:`` / ``if completion:``
        guards in ``_record_token_usage`` — incrementing a counter by 0
        would still create the series in Prometheus, which we want to
        avoid so dashboards don't show meaningless flat-zero lines.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_chat_response("ok", prompt_tokens=0, completion_tokens=0),
            )

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            await client.generate("q")

        assert registry.get_sample_value("lai_llm_tokens_total", {"model": "qwen-test", "kind": "prompt"}) is None
        assert registry.get_sample_value("lai_llm_tokens_total", {"model": "qwen-test", "kind": "completion"}) is None


class TestAsyncLifecycle:
    @pytest.mark.unit
    async def test_aclose_closes_underlying_transport(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("ok"))

        client = LlmClient(config, metrics=metrics, transport=_mock_transport(handler))
        await client.aclose()
        assert client._http.is_closed

    @pytest.mark.unit
    async def test_context_manager_closes_on_exit(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("ok"))

        async with LlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            pass
        assert client._http.is_closed


# ─────────────────────────────────────────────────────────────────────────────
# Sync client — mirrors a subset of the async tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSyncClient:
    @pytest.mark.unit
    def test_generate_basic(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("hello sync"))

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = client.generate("hi")

        assert result == "hello sync"

    @pytest.mark.unit
    def test_generate_strips_think(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("<think>x</think>real answer"))

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            assert client.generate("q") == "real answer"

    @pytest.mark.unit
    def test_generate_json_happy_path(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response('{"label": "x", "score": 0.5}'))

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = client.generate_json(_ExtractionSchema, "extract")

        assert result == _ExtractionSchema(label="x", score=0.5)

    @pytest.mark.unit
    def test_generate_json_fallback(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "guided_json" in body.get("extra_body", {}):
                return httpx.Response(400, text="rejected")
            return httpx.Response(200, json=_chat_response('{"label": "x", "score": 0.5}'))

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            result = client.generate_json(_ExtractionSchema, "extract")

        assert result == _ExtractionSchema(label="x", score=0.5)

    @pytest.mark.unit
    def test_retry_then_succeed(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, json=_chat_response("ok"))

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            assert client.generate("q") == "ok"

    @pytest.mark.unit
    def test_retry_exhausted(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="always down")

        with (
            SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client,
            pytest.raises(LlmRetryExhaustedError) as exc_info,
        ):
            client.generate("q")
        assert exc_info.value.attempts == 3

    @pytest.mark.unit
    def test_empty_response_retried(self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                return httpx.Response(200, json=_chat_response(""))
            return httpx.Response(200, json=_chat_response("recovered"))

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            assert client.generate("q") == "recovered"

        assert registry.get_sample_value("lai_llm_empty_responses_total", {"model": "qwen-test"}) == 1.0

    @pytest.mark.unit
    def test_timeout_becomes_call_error(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timeout", request=request)

        with (
            SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client,
            pytest.raises(LlmRetryExhaustedError) as exc_info,
        ):
            client.generate("q")
        assert isinstance(exc_info.value.__cause__, LlmCallError)

    @pytest.mark.unit
    def test_non_json_response_body(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        with (
            SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client,
            pytest.raises(LlmRetryExhaustedError),
        ):
            client.generate("q")

    @pytest.mark.unit
    def test_context_manager_closes(self, config: LlmConfig, metrics: LlmMetrics) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("ok"))

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            pass
        assert client._http.is_closed

    @pytest.mark.unit
    def test_generate_json_validation_failure(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response('{"label": "x"}'))

        with (
            SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client,
            pytest.raises(LlmSchemaValidationError),
        ):
            client.generate_json(_ExtractionSchema, "extract")

        assert (
            registry.get_sample_value(
                "lai_llm_schema_failures_total",
                {"model": "qwen-test", "kind": "validation"},
            )
            == 1.0
        )

    @pytest.mark.unit
    def test_generate_json_parse_failure(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_chat_response("not json at all"))

        with (
            SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client,
            pytest.raises(LlmJsonParseError),
        ):
            client.generate_json(_ExtractionSchema, "extract")

        assert (
            registry.get_sample_value(
                "lai_llm_schema_failures_total",
                {"model": "qwen-test", "kind": "parse"},
            )
            == 1.0
        )

    @pytest.mark.unit
    def test_zero_token_counts_skip_metric_increments(
        self, config: LlmConfig, metrics: LlmMetrics, registry: CollectorRegistry
    ) -> None:
        """Sync mirror of the async zero-token-accounting test."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_chat_response("ok", prompt_tokens=0, completion_tokens=0),
            )

        with SyncLlmClient(config, metrics=metrics, transport=_mock_transport(handler)) as client:
            client.generate("q")

        assert registry.get_sample_value("lai_llm_tokens_total", {"model": "qwen-test", "kind": "prompt"}) is None
        assert registry.get_sample_value("lai_llm_tokens_total", {"model": "qwen-test", "kind": "completion"}) is None
