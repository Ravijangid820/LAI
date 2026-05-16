"""Tests for :class:`lai.common.embedding.metrics.EmbeddingMetrics`."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY, CollectorRegistry

from lai.common.embedding.metrics import EmbeddingMetrics, default_embedding_metrics


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


class TestConstruction:
    @pytest.mark.unit
    def test_isolated_registry(self, registry: CollectorRegistry) -> None:
        metrics = EmbeddingMetrics(registry=registry)
        # Registering twice on the same isolated registry must collide,
        # proving the bundle actually attached its collectors.
        with pytest.raises(ValueError, match="Duplicated"):
            EmbeddingMetrics(registry=registry)
        # Avoid linter "unused variable" warning.
        assert metrics.calls_total is not None

    @pytest.mark.unit
    def test_default_singleton_uses_default_registry(self) -> None:
        # ``default_embedding_metrics`` is module-level; it registered
        # against the global ``REGISTRY`` at import time. We can locate
        # at least one of its families there.
        names = {m.name for m in REGISTRY.collect()}
        assert "lai_embedding_calls" in names or "lai_embedding_calls_total" in names

    @pytest.mark.unit
    def test_default_singleton_exposes_attributes(self) -> None:
        assert default_embedding_metrics.calls_total is not None
        assert default_embedding_metrics.request_duration_seconds is not None
        assert default_embedding_metrics.retries_total is not None
        assert default_embedding_metrics.inputs_total is not None
        assert default_embedding_metrics.dimension_mismatch_total is not None


class TestLabelShapes:
    @pytest.mark.unit
    def test_calls_total_has_model_and_status(self, registry: CollectorRegistry) -> None:
        metrics = EmbeddingMetrics(registry=registry)
        metrics.calls_total.labels(model="m", status="success").inc()
        # Repeating with the same labels must not raise.
        metrics.calls_total.labels(model="m", status="success").inc()

    @pytest.mark.unit
    def test_retries_labelled_by_model(self, registry: CollectorRegistry) -> None:
        metrics = EmbeddingMetrics(registry=registry)
        metrics.retries_total.labels(model="m").inc()

    @pytest.mark.unit
    def test_inputs_labelled_by_model(self, registry: CollectorRegistry) -> None:
        metrics = EmbeddingMetrics(registry=registry)
        metrics.inputs_total.labels(model="m").inc(5)

    @pytest.mark.unit
    def test_dimension_mismatch_labelled_by_model(self, registry: CollectorRegistry) -> None:
        metrics = EmbeddingMetrics(registry=registry)
        metrics.dimension_mismatch_total.labels(model="m").inc()


class TestHistogramObservation:
    @pytest.mark.unit
    def test_request_duration_observes(self, registry: CollectorRegistry) -> None:
        metrics = EmbeddingMetrics(registry=registry)
        metrics.request_duration_seconds.labels(model="m", status="success").observe(0.1)
        # If buckets are wired correctly, the +Inf bucket sees the sample.
        for metric in registry.collect():
            if metric.name == "lai_embedding_request_duration_seconds":
                samples = [s for s in metric.samples if s.name.endswith("_count")]
                assert any(s.value == 1.0 for s in samples)
                return
        pytest.fail("histogram metric not found in registry")
