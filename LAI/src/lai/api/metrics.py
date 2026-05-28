"""Prometheus metrics for :mod:`lai.api.serve_rag`.

Mirrors the shape of :mod:`lai.common.llm.metrics` /
:mod:`lai.common.reranker.metrics` — a single :class:`RagMetrics` bundle
that owns every domain-level Counter / Histogram the chat backend
emits, plus a module-level :data:`default_metrics` registered against
the default :data:`prometheus_client.REGISTRY` (the registry the
``/metrics`` endpoint scrapes).

HTTP-level metrics (requests-per-route, p50/p95 request duration,
in-flight gauge) are emitted separately by
``prometheus-fastapi-instrumentator``, which is wired into the FastAPI
app in :func:`lai.api.serve_rag.main`. This module is only for the
*domain-level* signals a Grafana dashboard for legal-AI needs:

- Did the lawyer get a grounded answer, or did the validator strip
  citations and add ``(unbelegt)``?
- How often does the model cite a Bundesland-specific rule for the
  wrong jurisdiction (the lawyer's #2 v0 complaint)?
- How is the lawyer voting on the answers we ship?

Label cardinality
-----------------

We keep label sets small and bounded. ``mode`` takes a handful of values
(``chat`` / ``rag`` / ``contract`` / ``rag+contract``); ``language`` is
two-valued (``de`` / ``en``); ``status`` is two-valued
(``success`` / ``error``); ``rating`` is two-valued
(``thumbs_up`` / ``thumbs_down``). Per-user / per-session labels are
NOT emitted — they would explode cardinality and leak tenant identity
into the metrics path.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

__all__ = ["RagMetrics", "default_metrics"]


# Latency buckets in seconds tuned for the end-to-end /query path
# (embed + retrieve + rerank + LLM). Median lives around 4-8s for a
# grounded turn; the 30-120s tail covers thinking-mode contract
# analysis. Default Prometheus buckets top out at 10s and would
# collapse the tail into a single +Inf bucket.
_QUERY_LATENCY_BUCKETS: tuple[float, ...] = (
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    20.0,
    30.0,
    60.0,
    120.0,
    float("inf"),
)

# Chunk-count buckets — RAG turns return 0 chunks (chat-only) up to
# ``top_k`` (default 3, ceiling around 8 for power users). Beyond 8
# we don't care about resolution.
_CHUNK_COUNT_BUCKETS: tuple[float, ...] = (
    0,
    1,
    2,
    3,
    5,
    8,
    13,
    float("inf"),
)


class RagMetrics:
    """Bundle of Prometheus metrics for the chat / RAG endpoints.

    Each instance registers its collectors against the supplied
    ``registry`` (or the default :data:`prometheus_client.REGISTRY` when
    omitted). Tests should instantiate this with a fresh
    :class:`~prometheus_client.CollectorRegistry` to keep their
    assertions isolated from production metrics — mirrors the pattern in
    :class:`lai.common.llm.metrics.LlmMetrics`.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        reg = registry if registry is not None else REGISTRY

        # ── Query funnel ─────────────────────────────────────────────
        self.query_total = Counter(
            "lai_rag_query_total",
            "Total /query (and /query/stream) calls.",
            labelnames=("mode", "language", "status"),
            registry=reg,
        )
        self.query_latency_seconds = Histogram(
            "lai_rag_query_latency_seconds",
            "End-to-end /query latency in seconds (embed + retrieve + rerank + generate).",
            labelnames=("mode",),
            buckets=_QUERY_LATENCY_BUCKETS,
            registry=reg,
        )
        self.retrieval_chunks_returned = Histogram(
            "lai_rag_retrieval_chunks_returned",
            "Number of chunks returned per RAG query (post-dedup, post-rerank, capped at top_k).",
            buckets=_CHUNK_COUNT_BUCKETS,
            registry=reg,
        )

        # ── Validator alarms ────────────────────────────────────────
        # The lawyer's two v0 complaints — both have a server-side
        # validator now. These counters expose whether the validators
        # are *firing* (signal that the model is misbehaving) so we
        # can spot regressions before the lawyer does.
        self.citation_unbelegt_responses_total = Counter(
            "lai_rag_citation_unbelegt_responses_total",
            "Responses where the citation validator rewrote ≥1 sentence with (unbelegt).",
            registry=reg,
        )
        self.citation_unbelegt_sentences_total = Counter(
            "lai_rag_citation_unbelegt_sentences_total",
            "Cumulative sentences rewritten with the (unbelegt) marker.",
            registry=reg,
        )
        self.jurisdiction_warnings_responses_total = Counter(
            "lai_rag_jurisdiction_warnings_responses_total",
            "Responses where the jurisdiction validator emitted ≥1 warning.",
            registry=reg,
        )
        self.jurisdiction_warnings_total = Counter(
            "lai_rag_jurisdiction_warnings_total",
            "Cumulative jurisdiction-mismatch warnings emitted across all turns.",
            registry=reg,
        )

        # ── Lawyer feedback (POST /feedback) ────────────────────────
        # Rating is two-valued (thumbs_up / thumbs_down). The /feedback
        # route also persists a reason + free-text comment, but those
        # would explode cardinality on the metrics path; surface them
        # via the SQLite table instead.
        self.feedback_total = Counter(
            "lai_feedback_total",
            "Lawyer feedback submissions captured via POST /feedback.",
            labelnames=("rating",),
            registry=reg,
        )


# Production singleton — imported by serve_rag.py. Tests that need
# isolation construct their own :class:`RagMetrics` with a fresh
# :class:`CollectorRegistry`.
default_metrics: RagMetrics = RagMetrics()
