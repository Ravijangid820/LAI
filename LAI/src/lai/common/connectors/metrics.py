"""Prometheus metrics for :mod:`lai.common.connectors`.

Bounded label cardinality:
- ``connector``: one of ``"nominatim" | "alkis"`` (small fixed set).
- ``status``: ``"success" | "error" | "rejected"``.
- ``bundesland``: only for ALKIS; the 16 lowercase Bundesland keys.

The per-instance ``CollectorRegistry`` parameter lets tests use an
isolated registry to avoid cross-test pollution (same discipline as
``lai.common.llm.metrics`` / ``lai.common.embedding.metrics``).
"""

from __future__ import annotations

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

__all__ = ["ConnectorMetrics", "default_connector_metrics"]


# Default buckets tuned for external HTTP calls (Nominatim is ~100-500 ms;
# ALKIS state WFS endpoints vary wildly, occasionally seconds on cold cache).
_DEFAULT_DURATION_BUCKETS: tuple[float, ...] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    20.0,
)


class ConnectorMetrics:
    """Prometheus metric bundle for the connector subpackage.

    Construct with ``ConnectorMetrics(registry=<registry>)`` to bind to
    a specific registry. The module-level :data:`default_connector_metrics`
    binds to the global default registry — what production code uses.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        effective_registry = registry if registry is not None else REGISTRY

        self.calls_total: Counter = Counter(
            "lai_connector_calls_total",
            "Total connector HTTP calls, labelled by connector + status.",
            ["connector", "status"],
            registry=effective_registry,
        )
        self.request_duration_seconds: Histogram = Histogram(
            "lai_connector_request_duration_seconds",
            "Connector HTTP request duration in seconds.",
            ["connector", "status"],
            buckets=_DEFAULT_DURATION_BUCKETS,
            registry=effective_registry,
        )
        self.retries_total: Counter = Counter(
            "lai_connector_retries_total",
            "Total retry attempts across all connector calls.",
            ["connector"],
            registry=effective_registry,
        )
        self.bbox_rejections_total: Counter = Counter(
            "lai_connector_nominatim_bbox_rejections_total",
            (
                "Geocode results rejected by the bbox plausibility gate "
                "(``expected_bundesland`` mismatch). One of the four "
                "credibility errors the wind-lawyer caught at v0 demo; "
                "this counter is the live evidence the gate is firing."
            ),
            ["expected_bundesland"],
            registry=effective_registry,
        )
        self.alkis_results_total: Counter = Counter(
            "lai_connector_alkis_results_total",
            "Parcels returned by ALKIS WFS, labelled by Bundesland.",
            ["bundesland", "shape"],  # shape ∈ "json" | "gml"
            registry=effective_registry,
        )


default_connector_metrics = ConnectorMetrics()
