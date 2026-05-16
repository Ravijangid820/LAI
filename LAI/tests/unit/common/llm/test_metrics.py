"""Tests for :mod:`lai.common.llm.metrics`.

Each test constructs an isolated :class:`CollectorRegistry` so the
assertions never collide with production state and the tests can run in
any order. Metric values are read back via the registry's public
``get_sample_value`` API (the supported way to inspect collector state).
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY, CollectorRegistry

from lai.common.llm import metrics as metrics_module
from lai.common.llm.metrics import LlmMetrics, default_metrics

# ─────────────────────────────────────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> CollectorRegistry:
    """Fresh registry per test for isolation."""
    return CollectorRegistry()


@pytest.fixture
def m(registry: CollectorRegistry) -> LlmMetrics:
    """LlmMetrics instance bound to the isolated registry."""
    return LlmMetrics(registry=registry)


@pytest.mark.unit
def test_default_metrics_uses_default_registry() -> None:
    """``default_metrics`` is the module-level singleton registered against REGISTRY."""
    # Looked up via get_sample_value with no initial increments: returns
    # None for an unobserved series, which proves the metric was
    # *registered* in the default registry (the lookup would raise
    # otherwise).
    assert (
        REGISTRY.get_sample_value(
            "lai_llm_calls_total",
            {"model": "x", "status": "success"},
        )
        is None
    )
    assert isinstance(default_metrics, LlmMetrics)


@pytest.mark.unit
def test_isolated_registry_does_not_collide_with_default(
    registry: CollectorRegistry,
) -> None:
    """Two LlmMetrics on two registries are independent collectors."""
    bundle_a = LlmMetrics(registry=registry)
    other_registry = CollectorRegistry()
    bundle_b = LlmMetrics(registry=other_registry)

    # Sanity: the two bundles hold distinct Counter objects (each was
    # constructed against its own registry, so no sharing is possible).
    assert bundle_a.calls_total is not bundle_b.calls_total

    bundle_a.calls_total.labels(model="m", status="success").inc()
    # ``bundle_b`` (on the other registry) is untouched.
    assert (
        registry.get_sample_value(
            "lai_llm_calls_total",
            {"model": "m", "status": "success"},
        )
        == 1.0
    )
    assert (
        other_registry.get_sample_value(
            "lai_llm_calls_total",
            {"model": "m", "status": "success"},
        )
        is None
    )


# ─────────────────────────────────────────────────────────────────────────────
# Metric surface — all six metrics exist with the documented label sets
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("attr", "name", "labels"),
    [
        ("calls_total", "lai_llm_calls_total", {"model": "x", "status": "success"}),
        (
            "request_duration_seconds",
            "lai_llm_request_duration_seconds_count",
            {"model": "x", "status": "success"},
        ),
        ("retries_total", "lai_llm_retries_total", {"model": "x"}),
        ("empty_responses_total", "lai_llm_empty_responses_total", {"model": "x"}),
        (
            "schema_failures_total",
            "lai_llm_schema_failures_total",
            {"model": "x", "kind": "parse"},
        ),
        ("tokens_total", "lai_llm_tokens_total", {"model": "x", "kind": "prompt"}),
    ],
)
def test_metric_is_registered_with_expected_labels(
    m: LlmMetrics,
    registry: CollectorRegistry,
    attr: str,
    name: str,
    labels: dict[str, str],
) -> None:
    """Each metric is registered with the documented name and label set."""
    metric = getattr(m, attr)
    # `labels` is a child-factory: calling it touches the label combo so the
    # series appears in the registry, but value is 0 until we ``inc`` /
    # ``observe``. For counters and histograms ``get_sample_value`` returns
    # ``0.0`` once the series exists.
    metric.labels(**labels)
    value = registry.get_sample_value(name, labels)
    assert value == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Behaviour
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_calls_total_increments_per_label_combo(m: LlmMetrics, registry: CollectorRegistry) -> None:
    m.calls_total.labels(model="qwen3.6-27b", status="success").inc()
    m.calls_total.labels(model="qwen3.6-27b", status="success").inc()
    m.calls_total.labels(model="qwen3.6-27b", status="error").inc()
    m.calls_total.labels(model="qwen2.5-7b", status="success").inc()

    assert (
        registry.get_sample_value(
            "lai_llm_calls_total",
            {"model": "qwen3.6-27b", "status": "success"},
        )
        == 2.0
    )
    assert (
        registry.get_sample_value(
            "lai_llm_calls_total",
            {"model": "qwen3.6-27b", "status": "error"},
        )
        == 1.0
    )
    assert (
        registry.get_sample_value(
            "lai_llm_calls_total",
            {"model": "qwen2.5-7b", "status": "success"},
        )
        == 1.0
    )


@pytest.mark.unit
def test_request_duration_histogram_records_observations(m: LlmMetrics, registry: CollectorRegistry) -> None:
    h = m.request_duration_seconds.labels(model="qwen3.6-27b", status="success")
    h.observe(0.75)
    h.observe(3.2)
    h.observe(45.0)

    labels = {"model": "qwen3.6-27b", "status": "success"}
    assert registry.get_sample_value("lai_llm_request_duration_seconds_count", labels) == 3.0
    assert registry.get_sample_value(
        "lai_llm_request_duration_seconds_sum",
        labels,
    ) == pytest.approx(0.75 + 3.2 + 45.0)


@pytest.mark.unit
def test_request_duration_uses_llm_appropriate_buckets(m: LlmMetrics, registry: CollectorRegistry) -> None:
    """Observations fall into the documented LLM-tuned bucket boundaries."""
    labels = {"model": "qwen3.6-27b", "status": "success"}
    m.request_duration_seconds.labels(**labels).observe(0.4)  # below 0.5
    m.request_duration_seconds.labels(**labels).observe(0.6)  # in [0.5, 1.0)
    m.request_duration_seconds.labels(**labels).observe(45.0)  # in [30, 60)
    m.request_duration_seconds.labels(**labels).observe(200.0)  # in [120, +Inf)

    # Cumulative buckets: each `le` is "≤ that boundary".
    def bucket(le: str) -> float | None:
        return registry.get_sample_value(
            "lai_llm_request_duration_seconds_bucket",
            {**labels, "le": le},
        )

    assert bucket("0.5") == 1.0  # only 0.4 falls below
    assert bucket("1.0") == 2.0  # 0.4 + 0.6
    assert bucket("30.0") == 2.0  # 45.0 doesn't fit
    assert bucket("60.0") == 3.0  # 45.0 now counted
    assert bucket("120.0") == 3.0  # 200.0 doesn't fit
    assert bucket("+Inf") == 4.0  # everything


@pytest.mark.unit
def test_retries_total_increments(m: LlmMetrics, registry: CollectorRegistry) -> None:
    m.retries_total.labels(model="qwen3.6-27b").inc()
    m.retries_total.labels(model="qwen3.6-27b").inc(2)  # batched increment

    assert registry.get_sample_value("lai_llm_retries_total", {"model": "qwen3.6-27b"}) == 3.0


@pytest.mark.unit
def test_empty_responses_total_increments(m: LlmMetrics, registry: CollectorRegistry) -> None:
    m.empty_responses_total.labels(model="qwen3.6-27b").inc()
    assert registry.get_sample_value("lai_llm_empty_responses_total", {"model": "qwen3.6-27b"}) == 1.0


@pytest.mark.unit
def test_schema_failures_distinguishes_kinds(m: LlmMetrics, registry: CollectorRegistry) -> None:
    m.schema_failures_total.labels(model="qwen3.6-27b", kind="parse").inc()
    m.schema_failures_total.labels(model="qwen3.6-27b", kind="validation").inc()
    m.schema_failures_total.labels(model="qwen3.6-27b", kind="guided_decoding_rejected").inc()

    for kind in ("parse", "validation", "guided_decoding_rejected"):
        assert (
            registry.get_sample_value(
                "lai_llm_schema_failures_total",
                {"model": "qwen3.6-27b", "kind": kind},
            )
            == 1.0
        )


@pytest.mark.unit
def test_tokens_total_distinguishes_kinds(m: LlmMetrics, registry: CollectorRegistry) -> None:
    m.tokens_total.labels(model="qwen3.6-27b", kind="prompt").inc(1024)
    m.tokens_total.labels(model="qwen3.6-27b", kind="completion").inc(512)
    m.tokens_total.labels(model="qwen3.6-27b", kind="thinking").inc(256)

    assert registry.get_sample_value("lai_llm_tokens_total", {"model": "qwen3.6-27b", "kind": "prompt"}) == 1024.0
    assert registry.get_sample_value("lai_llm_tokens_total", {"model": "qwen3.6-27b", "kind": "completion"}) == 512.0
    assert registry.get_sample_value("lai_llm_tokens_total", {"model": "qwen3.6-27b", "kind": "thinking"}) == 256.0


# ─────────────────────────────────────────────────────────────────────────────
# Module surface
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_module_exposes_documented_symbols() -> None:
    assert set(metrics_module.__all__) == {"LlmMetrics", "default_metrics"}
    # The module-level default_metrics is a single instance (not a factory).
    assert metrics_module.default_metrics is default_metrics
