"""Prometheus metrics for :class:`lai.common.llm.client.LlmClient`.

A small bundle class (:class:`LlmMetrics`) holds every metric the client
emits. The bundle takes an optional ``registry`` so tests can construct an
isolated registry per test and assert against it without global-state
pollution.

Production code uses :data:`default_metrics`, a module-level
:class:`LlmMetrics` instance registered against
:data:`prometheus_client.REGISTRY` (the default registry the
``/metrics`` endpoint scrapes).

Metric naming follows the Prometheus convention
``<namespace>_<subsystem>_<metric>_<unit>``. Our namespace is ``lai``,
subsystem ``llm``.

Label cardinality
-----------------

We keep label sets small and bounded. ``model`` is the served model name
(a handful of values across the platform's lifetime); ``status`` is one
of ``success`` / ``error`` (not the HTTP status code, which would be
unbounded). ``reason`` and ``kind`` use closed enums documented at each
metric.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

__all__ = ["LlmMetrics", "default_metrics"]


# Histogram buckets in seconds, tuned for LLM-call latency. The default
# Prometheus buckets top out at 10s; LLM calls regularly run 5-30s and
# can reach 60s+ in thinking mode, so we extend the tail without losing
# resolution at the lower end.
_LATENCY_BUCKETS: tuple[float, ...] = (
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


class LlmMetrics:
    """Bundle of Prometheus metrics for the LLM client.

    Each instance registers its metric collectors against the supplied
    ``registry`` (or the default :data:`prometheus_client.REGISTRY` when
    omitted). Tests should instantiate this with a fresh
    :class:`~prometheus_client.CollectorRegistry` to keep their assertions
    isolated from production metrics.

    Attributes:
        calls_total: Counter of LLM calls, labelled by ``model`` and
            ``status`` (``success`` | ``error``).
        request_duration_seconds: Histogram of end-to-end call latency,
            same labels as ``calls_total``.
        retries_total: Counter of retry attempts, labelled by ``model``.
            Incremented once per retry, not once per call — a call that
            succeeded on its second attempt records ``1``.
        empty_responses_total: Counter of calls that returned empty / null
            content. A separate signal from ``calls_total{status=error}``
            because Qwen3's spurious empty completions deserve their own
            alerting threshold.
        schema_failures_total: Counter of structured-output failures,
            labelled by ``model`` and ``kind`` (``parse`` |
            ``validation`` | ``guided_decoding_rejected``).
        tokens_total: Counter of token usage, labelled by ``model`` and
            ``kind`` (``prompt`` | ``completion`` | ``thinking``).
            ``thinking`` is the count consumed by ``<think>...</think>``
            blocks, as captured by ``strip_think`` callers.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        # Allow the caller to pass ``REGISTRY`` (the default singleton) or
        # any custom :class:`CollectorRegistry`. ``None`` means "use the
        # default" — the same behaviour as the Prometheus library itself.
        effective_registry = registry if registry is not None else REGISTRY

        self.calls_total: Counter = Counter(
            "lai_llm_calls_total",
            "Total LLM calls, by model and outcome.",
            labelnames=("model", "status"),
            registry=effective_registry,
        )
        self.request_duration_seconds: Histogram = Histogram(
            "lai_llm_request_duration_seconds",
            "End-to-end LLM call latency in seconds.",
            labelnames=("model", "status"),
            buckets=_LATENCY_BUCKETS,
            registry=effective_registry,
        )
        self.retries_total: Counter = Counter(
            "lai_llm_retries_total",
            "Total retry attempts (one increment per retry, not per call).",
            labelnames=("model",),
            registry=effective_registry,
        )
        self.empty_responses_total: Counter = Counter(
            "lai_llm_empty_responses_total",
            "Calls that returned empty or null content.",
            labelnames=("model",),
            registry=effective_registry,
        )
        self.schema_failures_total: Counter = Counter(
            "lai_llm_schema_failures_total",
            "Structured-output failures by kind.",
            labelnames=("model", "kind"),
            registry=effective_registry,
        )
        self.tokens_total: Counter = Counter(
            "lai_llm_tokens_total",
            "Token usage, by kind (prompt | completion | thinking).",
            labelnames=("model", "kind"),
            registry=effective_registry,
        )


default_metrics: LlmMetrics = LlmMetrics()
"""Module-level :class:`LlmMetrics` registered against the default
Prometheus :data:`~prometheus_client.REGISTRY`. The production
:class:`~lai.common.llm.client.LlmClient` uses this when the caller does
not pass a custom bundle."""
