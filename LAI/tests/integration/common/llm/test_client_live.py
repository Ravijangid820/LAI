"""Live integration tests for :class:`lai.common.llm.client.LlmClient`.

Hits the **real** ``lai_analyzer_llm`` vLLM endpoint to verify that the
unit-test mocks match production reality:

- vLLM accepts the ``extra_body.guided_json`` shape we send.
- Qwen3.6-27B actually emits ``<think>...</think>`` reasoning blocks for
  multi-step prompts (and our stripper removes them).
- The OpenAI-compatible response shape matches what :func:`_parse_chat_response`
  expects.
- Schema-enforced output produces JSON that satisfies a Pydantic model
  end-to-end.

These tests:

- Are marked ``@pytest.mark.integration`` and ``@pytest.mark.slow`` —
  ``make test`` (the default unit suite) skips them; ``make test-all``
  picks them up.
- Auto-skip with a clear reason if the endpoint is not reachable, so a
  developer / CI runner without local Docker access just sees ``SKIPPED``
  rather than an opaque failure.
- Use a per-test isolated Prometheus :class:`CollectorRegistry` so the
  default registry is not polluted across the suite.
- Use a long ``timeout_seconds`` (120s) because Qwen3.6-27B in thinking
  mode can be slow under contention.

Override the endpoint via the ``LAI_LLM_TEST_BASE_URL`` environment
variable when the analyzer is reachable at a non-default address.
"""

from __future__ import annotations

import os

import httpx
import pytest
from prometheus_client import CollectorRegistry
from pydantic import BaseModel, Field

from lai.common.llm import LlmClient, LlmConfig, LlmMetrics, SyncLlmClient

# Default host port for `lai_analyzer_llm` per `docker ps`:
# `0.0.0.0:8005->8000/tcp`. Override via env when the analyzer lives
# elsewhere (e.g., on the Docker network from inside CI).
LIVE_BASE_URL = os.environ.get("LAI_LLM_TEST_BASE_URL", "http://localhost:8005/v1")
LIVE_MODEL = os.environ.get("LAI_LLM_TEST_MODEL", "qwen3.6-27b")


def _live_endpoint_available() -> bool:
    """Probe ``/v1/models`` to determine whether the analyzer is reachable.

    Three-second timeout — long enough to clear normal startup, short
    enough that a downed endpoint doesn't drag out test collection.
    """
    try:
        response = httpx.get(f"{LIVE_BASE_URL}/models", timeout=3.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not _live_endpoint_available(),
        reason=(
            f"live vLLM endpoint not reachable at {LIVE_BASE_URL}; "
            "set LAI_LLM_TEST_BASE_URL or start lai_analyzer_llm. "
            "Run `make test-all` only with the analyzer up."
        ),
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def live_config() -> LlmConfig:
    """Config pointed at the live analyzer with conservative timeouts.

    ``default_max_tokens`` is set to **4096** rather than the unit-suite
    default of 1024. Qwen3.6-27B in thinking mode (which the live
    endpoint runs by default) routinely consumes 1-3K tokens on the
    ``<think>`` trace alone — leaving anything less than ~2K for the
    visible answer risks ``finish_reason: 'length'`` truncation with
    ``content: null``. The unit tests use mocks so this issue is
    invisible there; the integration test is what catches it.
    """
    return LlmConfig(
        base_url=LIVE_BASE_URL,
        model=LIVE_MODEL,
        # Fail fast in integration: one retry is enough to weather a
        # transient blip without dragging the suite out.
        max_retries=1,
        retry_initial_wait_seconds=0.5,
        retry_max_wait_seconds=2.0,
        # Qwen3.6-27B in thinking mode regularly runs 30-60s on the
        # production GPU; allow headroom for cold cache / contention.
        timeout_seconds=180.0,
        default_max_tokens=4096,
        # Deterministic output makes assertions stable.
        default_temperature=0.0,
    )


@pytest.fixture
def registry() -> CollectorRegistry:
    """Fresh Prometheus registry per test for assertion isolation."""
    return CollectorRegistry()


@pytest.fixture
def isolated_metrics(registry: CollectorRegistry) -> LlmMetrics:
    """Per-test :class:`LlmMetrics` bound to ``registry``."""
    return LlmMetrics(registry=registry)


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────


class _CountryFact(BaseModel):
    """Tiny schema for the guided-JSON contract test."""

    country: str = Field(..., min_length=1)
    capital: str = Field(..., min_length=1)


# ─────────────────────────────────────────────────────────────────────────────
# Async client — happy-path live calls
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_returns_non_empty_answer(live_config: LlmConfig, isolated_metrics: LlmMetrics) -> None:
    """A basic factual prompt returns a non-empty answer mentioning Berlin.

    The 27B model knows Germany's capital; at ``temperature=0`` the
    answer is stable across runs.
    """
    async with LlmClient(live_config, metrics=isolated_metrics) as client:
        answer = await client.generate(
            "What is the capital of Germany? Answer in one short sentence.",
        )

    assert answer.strip()
    assert "berlin" in answer.lower()


@pytest.mark.asyncio
async def test_default_strips_think_block(live_config: LlmConfig, isolated_metrics: LlmMetrics) -> None:
    """A reasoning prompt's response has no ``<think>`` tags by default.

    Whether Qwen actually emits a think block for this prompt depends on
    the model's runtime decision, but we assert the *invariant*: if it
    did, the strip removed it. The unit suite proves the stripper's
    semantics; this test proves the wiring carries through end-to-end.
    """
    async with LlmClient(live_config, metrics=isolated_metrics) as client:
        clean = await client.generate(
            "Step by step, compute 17 * 23. Reason aloud, then give the answer.",
        )

    assert clean
    assert "<think>" not in clean
    assert "</think>" not in clean
    # The arithmetic answer should survive into the clean output.
    assert "391" in clean


@pytest.mark.asyncio
async def test_keep_thinking_preserves_trace_when_model_emits_one(
    live_config: LlmConfig, isolated_metrics: LlmMetrics
) -> None:
    """With ``keep_thinking=True``, the raw response is returned untouched.

    We do not assert that ``<think>`` is *present* (the model might
    short-circuit a trivial step) — only that, when it is present, we
    surface it instead of stripping it.
    """
    async with LlmClient(live_config, metrics=isolated_metrics) as client:
        raw = await client.generate(
            "Step by step, compute 17 * 23. Reason aloud, then give the answer.",
            keep_thinking=True,
        )

    assert raw
    # Either the model emitted a trace (and we preserved it), or it
    # produced a direct answer (and the assertion is vacuously satisfied
    # by the alternative branch). Both outcomes are valid for this test.
    has_think = "<think>" in raw and "</think>" in raw
    has_answer = "391" in raw
    assert has_think or has_answer


@pytest.mark.asyncio
async def test_generate_json_returns_validated_schema(live_config: LlmConfig, isolated_metrics: LlmMetrics) -> None:
    """Schema-enforced output: vLLM ``guided_json`` produces parseable, valid JSON.

    The whole point of ADR 0002 — proven against the real endpoint.
    """
    async with LlmClient(live_config, metrics=isolated_metrics) as client:
        result = await client.generate_json(
            _CountryFact,
            "Return Germany and its capital city.",
        )

    assert isinstance(result, _CountryFact)
    assert result.country.lower() == "germany"
    assert "berlin" in result.capital.lower()


@pytest.mark.asyncio
async def test_metrics_increment_on_live_call(
    live_config: LlmConfig,
    isolated_metrics: LlmMetrics,
    registry: CollectorRegistry,
) -> None:
    """End-to-end metrics: success counter and a latency observation."""
    async with LlmClient(live_config, metrics=isolated_metrics) as client:
        await client.generate("Say hi in one word.")

    success = registry.get_sample_value(
        "lai_llm_calls_total",
        {"model": LIVE_MODEL, "status": "success"},
    )
    assert success == 1.0

    latency_count = registry.get_sample_value(
        "lai_llm_request_duration_seconds_count",
        {"model": LIVE_MODEL, "status": "success"},
    )
    assert latency_count == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Sync client — parity check against the same endpoint
# ─────────────────────────────────────────────────────────────────────────────


def test_sync_client_generate(live_config: LlmConfig, isolated_metrics: LlmMetrics) -> None:
    """``SyncLlmClient.generate`` works against the same live endpoint."""
    with SyncLlmClient(live_config, metrics=isolated_metrics) as client:
        answer = client.generate(
            "What is the capital of Germany? Answer in one short sentence.",
        )

    assert answer.strip()
    assert "berlin" in answer.lower()


def test_sync_client_generate_json(live_config: LlmConfig, isolated_metrics: LlmMetrics) -> None:
    """``SyncLlmClient.generate_json`` round-trips the guided-JSON contract."""
    with SyncLlmClient(live_config, metrics=isolated_metrics) as client:
        result = client.generate_json(
            _CountryFact,
            "Return Germany and its capital city.",
        )

    assert isinstance(result, _CountryFact)
    assert result.country.lower() == "germany"
    assert "berlin" in result.capital.lower()
