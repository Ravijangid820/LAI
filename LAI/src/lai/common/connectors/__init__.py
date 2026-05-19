"""Public-registry connector clients for LAI.

Two upstreams in v1:

* :class:`NominatimClient` — OpenStreetMap geocoder (free text → lat/lng).
* :class:`AlkisClient` — German cadastral INSPIRE WFS (lat/lng + Bundesland
  → real Flurstück polygons).

Both follow the same shape as the rest of ``lai.common``: sync client +
pydantic-settings config + tenacity retries + Prometheus metrics +
typed exceptions hierarchy. See :doc:`/docs/adr/0001` for the rationale
on the sync-only surface (these are low-volume calls, async buys
nothing).

Phase 2B (per ``harsh/IMPLEMENTATION_GUIDE`` §8.4) adds more registries
under this package: MaStR (Marktstammdatenregister — turbine inventory),
Handelsregister (company registry). Each lands as a new sibling client
with the same shape.
"""

from __future__ import annotations

from lai.common.connectors.alkis import AlkisClient
from lai.common.connectors.config import (
    ALKIS_WFS_ENDPOINTS,
    AlkisConfig,
    NominatimConfig,
)
from lai.common.connectors.exceptions import (
    AlkisCallError,
    AlkisError,
    AlkisInvalidResponseError,
    AlkisRetryExhaustedError,
    ConnectorError,
    NominatimCallError,
    NominatimError,
    NominatimInvalidResponseError,
    NominatimRetryExhaustedError,
)
from lai.common.connectors.metrics import (
    ConnectorMetrics,
    default_connector_metrics,
)
from lai.common.connectors.nominatim import NominatimClient

__all__ = [
    "ALKIS_WFS_ENDPOINTS",
    "AlkisCallError",
    "AlkisClient",
    "AlkisConfig",
    "AlkisError",
    "AlkisInvalidResponseError",
    "AlkisRetryExhaustedError",
    "ConnectorError",
    "ConnectorMetrics",
    "NominatimCallError",
    "NominatimClient",
    "NominatimConfig",
    "NominatimError",
    "NominatimInvalidResponseError",
    "NominatimRetryExhaustedError",
    "default_connector_metrics",
]
