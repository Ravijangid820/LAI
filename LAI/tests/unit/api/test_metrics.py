"""Tests for :mod:`lai.api.metrics`.

Mirrors the shape of ``tests/unit/common/llm/test_metrics.py``: each
test uses an isolated :class:`CollectorRegistry` so assertions never
collide with the module-level :data:`default_metrics` singleton (which
is registered against the default Prometheus REGISTRY at import time).
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY, CollectorRegistry

from lai.api.metrics import RagMetrics, default_metrics


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def m(registry: CollectorRegistry) -> RagMetrics:
    return RagMetrics(registry=registry)


@pytest.mark.unit
def test_default_metrics_registered_against_default_registry() -> None:
    """:data:`default_metrics` lives on the default Prometheus REGISTRY.

    Looked up via ``get_sample_value`` with a label set that has not
    been observed: returns None on a registered-but-unobserved series.
    A *missing* collector would silently return None too, so the
    isinstance check below is what actually proves registration —
    the get_sample_value call is the cheap sanity round-trip.
    """
    assert isinstance(default_metrics, RagMetrics)
    # Touch one label set so the series exists at scrape time.
    default_metrics.query_total.labels(
        mode="__test__",
        language="de",
        status="success",
    ).inc()
    assert REGISTRY.get_sample_value(
        "lai_rag_query_total",
        {"mode": "__test__", "language": "de", "status": "success"},
    ) == pytest.approx(1.0)


@pytest.mark.unit
def test_query_total_label_combinations(
    m: RagMetrics,
    registry: CollectorRegistry,
) -> None:
    m.query_total.labels(mode="rag", language="de", status="success").inc()
    m.query_total.labels(mode="rag", language="de", status="success").inc(2)
    m.query_total.labels(mode="chat", language="en", status="success").inc()

    assert registry.get_sample_value(
        "lai_rag_query_total",
        {"mode": "rag", "language": "de", "status": "success"},
    ) == pytest.approx(3.0)
    assert registry.get_sample_value(
        "lai_rag_query_total",
        {"mode": "chat", "language": "en", "status": "success"},
    ) == pytest.approx(1.0)


@pytest.mark.unit
def test_query_latency_histogram_buckets_extend_to_two_minutes(
    m: RagMetrics,
    registry: CollectorRegistry,
) -> None:
    """A 95-second observation must fall in the 120s bucket, not +Inf alone."""
    m.query_latency_seconds.labels(mode="rag").observe(95.0)
    bucket_120 = registry.get_sample_value(
        "lai_rag_query_latency_seconds_bucket",
        {"mode": "rag", "le": "120.0"},
    )
    bucket_60 = registry.get_sample_value(
        "lai_rag_query_latency_seconds_bucket",
        {"mode": "rag", "le": "60.0"},
    )
    assert bucket_120 == pytest.approx(1.0)
    assert bucket_60 == pytest.approx(0.0)


@pytest.mark.unit
def test_retrieval_chunks_histogram_observes_zero_for_chat_only(
    m: RagMetrics,
    registry: CollectorRegistry,
) -> None:
    """Chat-only turns return 0 chunks — the zero bucket must accept that."""
    m.retrieval_chunks_returned.observe(0)
    m.retrieval_chunks_returned.observe(3)
    assert registry.get_sample_value(
        "lai_rag_retrieval_chunks_returned_count",
    ) == pytest.approx(2.0)


@pytest.mark.unit
def test_validator_counters_increment_independently(
    m: RagMetrics,
    registry: CollectorRegistry,
) -> None:
    """``responses_total`` counts *turns with ≥1 flag* while ``sentences_total``
    is a true cumulative — they must not be folded into one collector."""
    m.citation_unbelegt_responses_total.inc()
    m.citation_unbelegt_sentences_total.inc(3)
    m.jurisdiction_warnings_responses_total.inc()
    m.jurisdiction_warnings_total.inc(2)
    assert registry.get_sample_value(
        "lai_rag_citation_unbelegt_responses_total",
    ) == pytest.approx(1.0)
    assert registry.get_sample_value(
        "lai_rag_citation_unbelegt_sentences_total",
    ) == pytest.approx(3.0)
    assert registry.get_sample_value(
        "lai_rag_jurisdiction_warnings_responses_total",
    ) == pytest.approx(1.0)
    assert registry.get_sample_value(
        "lai_rag_jurisdiction_warnings_total",
    ) == pytest.approx(2.0)


@pytest.mark.unit
def test_feedback_total_rating_label_is_string_enum(
    m: RagMetrics,
    registry: CollectorRegistry,
) -> None:
    """Rating label is two-valued: thumbs_up / thumbs_down."""
    m.feedback_total.labels(rating="thumbs_up").inc()
    m.feedback_total.labels(rating="thumbs_up").inc()
    m.feedback_total.labels(rating="thumbs_down").inc()
    assert registry.get_sample_value(
        "lai_feedback_total",
        {"rating": "thumbs_up"},
    ) == pytest.approx(2.0)
    assert registry.get_sample_value(
        "lai_feedback_total",
        {"rating": "thumbs_down"},
    ) == pytest.approx(1.0)


@pytest.mark.unit
def test_isolated_registry_does_not_collide_with_default(
    registry: CollectorRegistry,
) -> None:
    bundle_a = RagMetrics(registry=registry)
    other = CollectorRegistry()
    bundle_b = RagMetrics(registry=other)

    assert bundle_a.feedback_total is not bundle_b.feedback_total
    bundle_a.feedback_total.labels(rating="thumbs_up").inc()
    assert registry.get_sample_value(
        "lai_feedback_total",
        {"rating": "thumbs_up"},
    ) == pytest.approx(1.0)
    assert (
        other.get_sample_value(
            "lai_feedback_total",
            {"rating": "thumbs_up"},
        )
        is None
    )
