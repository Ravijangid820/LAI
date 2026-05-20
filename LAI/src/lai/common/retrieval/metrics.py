"""Prometheus metrics for :class:`lai.common.retrieval.client.RetrievalClient`.

Same bundle-class pattern as :class:`lai.common.embedding.metrics.EmbeddingMetrics`:
the production :data:`default_retrieval_metrics` registers against the
default Prometheus registry; tests pass a fresh
:class:`~prometheus_client.CollectorRegistry`.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

__all__ = ["RetrievalMetrics", "default_retrieval_metrics"]


# pgvector HNSW ANN query latency on the corpus_child_chunks index:
#   * warm cache, ef_search=100:   ~30-150 ms
#   * cold / large candidate set:  up to ~1 s
# The in-RAM matrix this replaces ran 0.66-6.65 s, so the histogram's
# upper buckets exist mostly to catch regressions / cache-cold spikes.
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    float("inf"),
)

# Rows-returned histogram — a dense search asks for top_k (default 30);
# this catches under-recall (index returning fewer than requested) which
# would silently degrade answer grounding.
_ROWS_BUCKETS: tuple[float, ...] = (
    0,
    1,
    5,
    10,
    20,
    30,
    50,
    100,
    float("inf"),
)


class RetrievalMetrics:
    """Bundle of Prometheus metrics for the retrieval client.

    Attributes:
        queries_total: Counter of dense searches, labelled by ``status``
            (``success`` | ``error``).
        query_duration_seconds: Histogram of end-to-end query latency,
            labelled by ``status``.
        rows_returned: Histogram of the number of child chunks each
            search returned. Under-recall (fewer than ``top_k``) shows up
            in the low buckets.
        retries_total: Counter of retry attempts (one increment per
            retry, not per query).
        pool_exhausted_total: Counter of times the connection pool had no
            free connection to hand out. Non-zero is a capacity-planning
            signal worth alerting on.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        effective_registry = registry if registry is not None else REGISTRY

        self.queries_total: Counter = Counter(
            "lai_retrieval_queries_total",
            "Total pgvector dense searches, by outcome.",
            labelnames=("status",),
            registry=effective_registry,
        )
        self.query_duration_seconds: Histogram = Histogram(
            "lai_retrieval_query_duration_seconds",
            "End-to-end pgvector dense-search latency in seconds.",
            labelnames=("status",),
            buckets=_LATENCY_BUCKETS,
            registry=effective_registry,
        )
        self.rows_returned: Histogram = Histogram(
            "lai_retrieval_rows_returned",
            "Number of child chunks returned per dense search.",
            buckets=_ROWS_BUCKETS,
            registry=effective_registry,
        )
        self.retries_total: Counter = Counter(
            "lai_retrieval_retries_total",
            "Total retry attempts (one increment per retry, not per query).",
            registry=effective_registry,
        )
        self.pool_exhausted_total: Counter = Counter(
            "lai_retrieval_pool_exhausted_total",
            "Times the connection pool had no free connection to hand out.",
            registry=effective_registry,
        )


default_retrieval_metrics: RetrievalMetrics = RetrievalMetrics()
"""Module-level :class:`RetrievalMetrics` registered against the default
Prometheus :data:`~prometheus_client.REGISTRY`. Production
:class:`~lai.common.retrieval.client.RetrievalClient` uses this when the
caller does not supply a custom bundle."""
