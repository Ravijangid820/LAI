"""Live integration tests for the reranker client.

Hits the **real** ``lai-test-reranker`` HuggingFace TEI endpoint
(``cross-encoder/ms-marco-MiniLM-L-12-v2`` on port 8004 host-side) to
verify that the unit-test mocks match production reality:

- TEI accepts the exact request body shape we send.
- The response is the ``[{"index": int, "score": float}, ...]`` array
  the parser expects, sorted descending by score.
- A wind-relevant document scores higher than unrelated ones (sanity
  check on the model itself, not the client).
- Batching against the live server works end-to-end across the 32-doc
  per-request limit.

Same skip-if-unreachable pattern as the LLM integration test. Run with
``make integration`` or ``pytest -m integration``.
"""

from __future__ import annotations

import os

import httpx
import pytest
from prometheus_client import CollectorRegistry

from lai.common.reranker import (
    RerankerClient,
    RerankerConfig,
    RerankerMetrics,
    SyncRerankerClient,
)

LIVE_BASE_URL = os.environ.get("LAI_RERANKER_TEST_BASE_URL", "http://localhost:8004")


def _live_endpoint_available() -> bool:
    """Probe ``/info`` to determine whether the reranker is reachable."""
    try:
        response = httpx.get(f"{LIVE_BASE_URL}/info", timeout=3.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not _live_endpoint_available(),
        reason=(
            f"live TEI reranker not reachable at {LIVE_BASE_URL}; "
            "set LAI_RERANKER_TEST_BASE_URL or start lai-test-reranker."
        ),
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def live_config() -> RerankerConfig:
    """Config pointed at the live reranker, conservative retry policy."""
    return RerankerConfig(
        base_url=LIVE_BASE_URL,
        max_retries=1,
        retry_initial_wait_seconds=0.5,
        retry_max_wait_seconds=2.0,
        timeout_seconds=30.0,
    )


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def metrics(registry: CollectorRegistry) -> RerankerMetrics:
    return RerankerMetrics(registry=registry)


# ─────────────────────────────────────────────────────────────────────────────
# Async client — happy paths against the real reranker
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_relevant_doc_scores_highest(live_config: RerankerConfig, metrics: RerankerMetrics) -> None:
    """The wind-relevant document outranks the unrelated ones.

    The cross-encoder model knows enough to score the right document
    higher; this asserts both that the call works *and* that the model
    behaves sensibly enough to be useful in the retrieval pipeline.
    """
    async with RerankerClient(live_config, metrics=metrics) as client:
        results = await client.rerank(
            query="what is wind energy",
            texts=[
                "the cat sat on the mat",
                "wind turbines generate electricity from wind",
                "Berlin is the capital of Germany",
            ],
        )

    assert len(results) == 3
    assert results[0].index == 1  # the wind-turbine sentence
    # Top score is meaningfully above the others (ms-marco's scores
    # range roughly 0..1; the wind doc here was ~0.8, others ~1e-5).
    assert results[0].score > results[1].score
    assert results[0].score > 0.1


@pytest.mark.asyncio
async def test_top_n_filters_to_requested_count(live_config: RerankerConfig, metrics: RerankerMetrics) -> None:
    """``top_n`` returns at most N items, regardless of input count."""
    async with RerankerClient(live_config, metrics=metrics) as client:
        results = await client.rerank(
            query="electric car",
            texts=[
                "Tesla makes electric cars",
                "the cat sat on the mat",
                "Berlin is the capital of Germany",
                "electric vehicles use batteries",
                "cooking with butter and onions",
            ],
            top_n=2,
        )

    assert len(results) == 2


@pytest.mark.asyncio
async def test_batching_against_live_endpoint(
    live_config: RerankerConfig,
    metrics: RerankerMetrics,
    registry: CollectorRegistry,
) -> None:
    """Sending > max_batch_size docs splits into multiple requests and merges.

    Uses a small ``max_batch_size`` (8) to force batching against the
    live endpoint without sending a giant payload.
    """
    config = live_config.model_copy(update={"max_batch_size": 8})
    texts = [f"document number {i} about an arbitrary topic" for i in range(20)]
    async with RerankerClient(config, metrics=metrics) as client:
        results = await client.rerank(query="topic", texts=texts)

    # All 20 documents are returned, with global indices in [0, 20).
    assert len(results) == 20
    assert sorted(r.index for r in results) == list(range(20))
    # Sorted descending by score (the contract :func:`_merge_batches` guarantees).
    assert results == sorted(results, key=lambda r: r.score, reverse=True)

    # Three batches (8 + 8 + 4) ⇒ three success-status calls.
    assert registry.get_sample_value("lai_reranker_calls_total", {"status": "success"}) == 3.0


@pytest.mark.asyncio
async def test_metrics_increment_on_live_call(
    live_config: RerankerConfig,
    metrics: RerankerMetrics,
    registry: CollectorRegistry,
) -> None:
    """End-to-end metrics: success counter, latency observation, doc counters."""
    async with RerankerClient(live_config, metrics=metrics) as client:
        await client.rerank(query="hello", texts=["a", "b", "c"])

    assert registry.get_sample_value("lai_reranker_calls_total", {"status": "success"}) == 1.0
    assert registry.get_sample_value("lai_reranker_request_duration_seconds_count", {"status": "success"}) == 1.0
    assert registry.get_sample_value("lai_reranker_documents_total", {"kind": "input"}) == 3.0
    assert registry.get_sample_value("lai_reranker_documents_total", {"kind": "returned"}) == 3.0


# ─────────────────────────────────────────────────────────────────────────────
# Sync client — parity
# ─────────────────────────────────────────────────────────────────────────────


def test_sync_client_relevant_doc_scores_highest(live_config: RerankerConfig, metrics: RerankerMetrics) -> None:
    """``SyncRerankerClient`` works end-to-end against the same endpoint."""
    with SyncRerankerClient(live_config, metrics=metrics) as client:
        results = client.rerank(
            query="what is wind energy",
            texts=[
                "the cat sat on the mat",
                "wind turbines generate electricity from wind",
                "Berlin is the capital of Germany",
            ],
        )

    assert results[0].index == 1
    assert results[0].score > results[1].score
