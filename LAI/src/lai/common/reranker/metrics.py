"""Prometheus metrics for :class:`lai.common.reranker.client.RerankerClient`.

Same bundle-class pattern as :class:`lai.common.llm.metrics.LlmMetrics`:
the production :data:`default_reranker_metrics` registers against the
default Prometheus registry; tests pass a fresh
:class:`~prometheus_client.CollectorRegistry`.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

__all__ = ["RerankerMetrics", "default_reranker_metrics"]


# Reranker latency is bounded by TEI's ``max_input_length=512`` and
# ``max_batch_tokens=16384``: typical request finishes in 10-300ms. The
# default Prometheus buckets (0.005 … 10s) already cover that range, so
# we keep them rather than retuning. Wide tail (the ``+Inf`` bucket
# captures the >10s outliers that indicate a real problem).
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


class RerankerMetrics:
    """Bundle of Prometheus metrics for the reranker client.

    Attributes:
        calls_total: Counter of reranker calls, labelled by ``status``
            (``success`` | ``error``).
        request_duration_seconds: Histogram of end-to-end call latency.
        retries_total: Counter of retry attempts (one increment per
            retry, not per call).
        documents_total: Counter of total documents reranked, labelled by
            ``kind`` (``input`` | ``returned``). ``input`` is the size of
            the ``texts`` array; ``returned`` is the number the caller
            kept after applying ``top_n``. The difference shows how often
            the reranker is being over-fed candidates.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        effective_registry = registry if registry is not None else REGISTRY

        self.calls_total: Counter = Counter(
            "lai_reranker_calls_total",
            "Total reranker calls, by outcome.",
            labelnames=("status",),
            registry=effective_registry,
        )
        self.request_duration_seconds: Histogram = Histogram(
            "lai_reranker_request_duration_seconds",
            "End-to-end reranker call latency in seconds.",
            labelnames=("status",),
            buckets=_LATENCY_BUCKETS,
            registry=effective_registry,
        )
        self.retries_total: Counter = Counter(
            "lai_reranker_retries_total",
            "Total retry attempts (one increment per retry, not per call).",
            registry=effective_registry,
        )
        self.documents_total: Counter = Counter(
            "lai_reranker_documents_total",
            "Total documents reranked, by kind (input | returned).",
            labelnames=("kind",),
            registry=effective_registry,
        )


default_reranker_metrics: RerankerMetrics = RerankerMetrics()
"""Module-level :class:`RerankerMetrics` registered against the default
Prometheus :data:`~prometheus_client.REGISTRY`. Production
:class:`~lai.common.reranker.client.RerankerClient` uses this when the
caller does not supply a custom bundle."""
