"""Prometheus metrics for :class:`lai.common.embedding.client.EmbeddingClient`.

Same bundle-class pattern as :class:`lai.common.llm.metrics.LlmMetrics` and
:class:`lai.common.reranker.metrics.RerankerMetrics`: the production
:data:`default_embedding_metrics` registers against the default Prometheus
registry; tests pass a fresh
:class:`~prometheus_client.CollectorRegistry`.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

__all__ = ["EmbeddingMetrics", "default_embedding_metrics"]


# Embedding latency at our batch sizes:
#   * single-query: ~20-100ms
#   * batch of 32:  ~100-500ms
# Default Prometheus buckets (0.005 … 10s) cover that range, but we tune
# the lower end finer so we can spot regressions inside the typical
# operating envelope.
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    float("inf"),
)


class EmbeddingMetrics:
    """Bundle of Prometheus metrics for the embedding client.

    Attributes:
        calls_total: Counter of embedding calls, labelled by ``model`` and
            ``status`` (``success`` | ``error``).
        request_duration_seconds: Histogram of end-to-end call latency,
            same labels as ``calls_total``.
        retries_total: Counter of retry attempts (one increment per
            retry, not per call), labelled by ``model``.
        inputs_total: Counter of total inputs embedded, labelled by
            ``model``. Useful for capacity planning: divided by
            ``calls_total{status="success"}`` it gives the average
            in-flight batch size.
        dimension_mismatch_total: Counter of vectors that arrived with a
            dimension other than :attr:`EmbeddingConfig.dimension`. Should
            stay at zero in steady state; non-zero is a configuration-drift
            signal worth alerting on.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        effective_registry = registry if registry is not None else REGISTRY

        self.calls_total: Counter = Counter(
            "lai_embedding_calls_total",
            "Total embedding calls, by model and outcome.",
            labelnames=("model", "status"),
            registry=effective_registry,
        )
        self.request_duration_seconds: Histogram = Histogram(
            "lai_embedding_request_duration_seconds",
            "End-to-end embedding call latency in seconds.",
            labelnames=("model", "status"),
            buckets=_LATENCY_BUCKETS,
            registry=effective_registry,
        )
        self.retries_total: Counter = Counter(
            "lai_embedding_retries_total",
            "Total retry attempts (one increment per retry, not per call).",
            labelnames=("model",),
            registry=effective_registry,
        )
        self.inputs_total: Counter = Counter(
            "lai_embedding_inputs_total",
            "Total inputs embedded.",
            labelnames=("model",),
            registry=effective_registry,
        )
        self.dimension_mismatch_total: Counter = Counter(
            "lai_embedding_dimension_mismatch_total",
            "Vectors returned with an unexpected dimension.",
            labelnames=("model",),
            registry=effective_registry,
        )


default_embedding_metrics: EmbeddingMetrics = EmbeddingMetrics()
"""Module-level :class:`EmbeddingMetrics` registered against the default
Prometheus :data:`~prometheus_client.REGISTRY`. Production
:class:`~lai.common.embedding.client.EmbeddingClient` uses this when the
caller does not supply a custom bundle."""
