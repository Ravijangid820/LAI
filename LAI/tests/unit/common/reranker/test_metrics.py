"""Tests for :mod:`lai.common.reranker.metrics`."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY, CollectorRegistry

from lai.common.reranker.metrics import RerankerMetrics, default_reranker_metrics


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def m(registry: CollectorRegistry) -> RerankerMetrics:
    return RerankerMetrics(registry=registry)


@pytest.mark.unit
def test_default_metrics_uses_default_registry() -> None:
    """``default_reranker_metrics`` is a module-level singleton on REGISTRY."""
    # Untouched series returns None; the lookup *succeeding* (vs. raising
    # KeyError) proves the collector is registered.
    assert REGISTRY.get_sample_value("lai_reranker_calls_total", {"status": "success"}) is None
    assert isinstance(default_reranker_metrics, RerankerMetrics)


@pytest.mark.unit
def test_calls_total_increments_per_status(m: RerankerMetrics, registry: CollectorRegistry) -> None:
    m.calls_total.labels(status="success").inc()
    m.calls_total.labels(status="success").inc()
    m.calls_total.labels(status="error").inc()

    assert registry.get_sample_value("lai_reranker_calls_total", {"status": "success"}) == 2.0
    assert registry.get_sample_value("lai_reranker_calls_total", {"status": "error"}) == 1.0


@pytest.mark.unit
def test_request_duration_histogram_records_observations(m: RerankerMetrics, registry: CollectorRegistry) -> None:
    h = m.request_duration_seconds.labels(status="success")
    h.observe(0.05)
    h.observe(0.4)

    labels = {"status": "success"}
    assert registry.get_sample_value("lai_reranker_request_duration_seconds_count", labels) == 2.0
    assert registry.get_sample_value("lai_reranker_request_duration_seconds_sum", labels) == pytest.approx(0.45)


@pytest.mark.unit
def test_request_duration_uses_reranker_appropriate_buckets(m: RerankerMetrics, registry: CollectorRegistry) -> None:
    labels = {"status": "success"}
    m.request_duration_seconds.labels(**labels).observe(0.02)  # below 0.025
    m.request_duration_seconds.labels(**labels).observe(0.04)  # in [0.025, 0.05)
    m.request_duration_seconds.labels(**labels).observe(0.3)  # in [0.25, 0.5)
    m.request_duration_seconds.labels(**labels).observe(15.0)  # in [10, +Inf)

    def bucket(le: str) -> float | None:
        return registry.get_sample_value(
            "lai_reranker_request_duration_seconds_bucket",
            {**labels, "le": le},
        )

    assert bucket("0.025") == 1.0
    assert bucket("0.05") == 2.0
    assert bucket("0.5") == 3.0
    assert bucket("10.0") == 3.0
    assert bucket("+Inf") == 4.0


@pytest.mark.unit
def test_retries_total_no_labels(m: RerankerMetrics, registry: CollectorRegistry) -> None:
    """``retries_total`` has no labels — it's a single global counter."""
    m.retries_total.inc()
    m.retries_total.inc(3)
    assert registry.get_sample_value("lai_reranker_retries_total", {}) == 4.0


@pytest.mark.unit
def test_documents_total_distinguishes_input_vs_returned(m: RerankerMetrics, registry: CollectorRegistry) -> None:
    m.documents_total.labels(kind="input").inc(50)
    m.documents_total.labels(kind="returned").inc(10)

    assert registry.get_sample_value("lai_reranker_documents_total", {"kind": "input"}) == 50.0
    assert registry.get_sample_value("lai_reranker_documents_total", {"kind": "returned"}) == 10.0
