"""Tests for :mod:`lai.common.retrieval.metrics`."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY, CollectorRegistry

from lai.common.retrieval.metrics import RetrievalMetrics, default_retrieval_metrics


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def m(registry: CollectorRegistry) -> RetrievalMetrics:
    return RetrievalMetrics(registry=registry)


@pytest.mark.unit
def test_default_metrics_registered_against_default_registry() -> None:
    assert isinstance(default_retrieval_metrics, RetrievalMetrics)
    default_retrieval_metrics.queries_total.labels(status="success").inc()
    assert REGISTRY.get_sample_value("lai_retrieval_queries_total", {"status": "success"}) is not None


@pytest.mark.unit
def test_query_counters(m: RetrievalMetrics, registry: CollectorRegistry) -> None:
    m.queries_total.labels(status="success").inc()
    m.queries_total.labels(status="error").inc()
    m.queries_total.labels(status="success").inc()
    assert registry.get_sample_value("lai_retrieval_queries_total", {"status": "success"}) == pytest.approx(2.0)
    assert registry.get_sample_value("lai_retrieval_queries_total", {"status": "error"}) == pytest.approx(1.0)


@pytest.mark.unit
def test_latency_histogram_buckets(m: RetrievalMetrics, registry: CollectorRegistry) -> None:
    m.query_duration_seconds.labels(status="success").observe(0.05)
    assert registry.get_sample_value(
        "lai_retrieval_query_duration_seconds_bucket",
        {"status": "success", "le": "0.05"},
    ) == pytest.approx(1.0)


@pytest.mark.unit
def test_rows_returned_and_pool_exhausted(m: RetrievalMetrics, registry: CollectorRegistry) -> None:
    m.rows_returned.observe(30)
    m.pool_exhausted_total.inc()
    assert registry.get_sample_value("lai_retrieval_rows_returned_count") == pytest.approx(1.0)
    assert registry.get_sample_value("lai_retrieval_pool_exhausted_total") == pytest.approx(1.0)


@pytest.mark.unit
def test_isolated_registry_independence(registry: CollectorRegistry) -> None:
    a = RetrievalMetrics(registry=registry)
    b = RetrievalMetrics(registry=CollectorRegistry())
    assert a.queries_total is not b.queries_total
