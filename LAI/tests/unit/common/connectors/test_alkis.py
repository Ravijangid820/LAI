"""Unit tests for :class:`lai.common.connectors.AlkisClient`.

Pattern matches ``test_nominatim.py``: :class:`httpx.MockTransport` for
the HTTP layer; tiny retry waits in the fixture config; isolated
Prometheus registry per test.

The state WFS endpoints' two response shapes (JSON + GML) are both
exercised — the GML fallback path is the one Track A item 6 fixed, so
it gets explicit coverage here.
"""

from __future__ import annotations

import httpx
import pytest
from prometheus_client import CollectorRegistry

from lai.common.connectors import (
    AlkisClient,
    AlkisConfig,
    AlkisRetryExhaustedError,
    ConnectorMetrics,
)
from lai.common.connectors.config import ALKIS_WFS_ENDPOINTS

_GML_BODY = """<?xml version='1.0' encoding='UTF-8'?>
<wfs:FeatureCollection
    xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:cp="http://inspire.ec.europa.eu/schemas/cp/4.0"
    xmlns:gml="http://www.opengis.net/gml/3.2">
  <wfs:member>
    <cp:CadastralParcel gml:id="x1">
      <cp:areaValue uom="m2">5000.0</cp:areaValue>
      <cp:label>9/3</cp:label>
      <cp:geometry>
        <gml:Polygon srsName="EPSG:4326">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>53.5 9.0 53.5 9.01 53.51 9.01 53.51 9.0 53.5 9.0</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </cp:geometry>
    </cp:CadastralParcel>
  </wfs:member>
</wfs:FeatureCollection>
"""

_JSON_BODY = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "label": "12/4",
                "gemarkungsname": "Lamstedt",
                "flurnummer": "3",
                "areaValue": "12345.67",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [9.0, 53.5],
                        [9.01, 53.5],
                        [9.01, 53.51],
                        [9.0, 53.51],
                        [9.0, 53.5],
                    ]
                ],
            },
        },
    ],
}


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def metrics(registry: CollectorRegistry) -> ConnectorMetrics:
    return ConnectorMetrics(registry=registry)


@pytest.fixture
def fast_config() -> AlkisConfig:
    """Tiny retry waits so the retry tests are sub-second."""
    return AlkisConfig(
        max_retries=2,
        retry_initial_wait_seconds=0.001,
        retry_max_wait_seconds=0.001,
    )


def _mt(handler: object) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ─────────────────────────────────────────────────────────────────────
# Happy paths
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_query_parcels_json_happy_path(
    fast_config: AlkisConfig,
    metrics: ConnectorMetrics,
    registry: CollectorRegistry,
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_JSON_BODY,
            headers={"content-type": "application/json"},
        )

    with AlkisClient(
        fast_config,
        metrics=metrics,
        transport=_mt(handler),
    ) as client:
        parcels = client.query_parcels(
            lat=53.505,
            lng=9.005,
            bundesland="niedersachsen",
        )

    assert len(parcels) == 1
    assert parcels[0]["parcelNumber"] == "12/4"
    assert parcels[0]["source"] == "ALKIS WFS"
    # JSON-shape metric incremented.
    assert (
        registry.get_sample_value(
            "lai_connector_alkis_results_total",
            {"bundesland": "niedersachsen", "shape": "json"},
        )
        == 1.0
    )


@pytest.mark.unit
def test_query_parcels_gml_fallback_on_400(
    fast_config: AlkisConfig,
    metrics: ConnectorMetrics,
    registry: CollectorRegistry,
) -> None:
    """First request (JSON) returns 400 — shape detection retries
    without ``OUTPUTFORMAT``, gets GML, parser handles it."""
    state = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if "OUTPUTFORMAT" in req.url.params:
            return httpx.Response(400, text="json not accepted")
        return httpx.Response(
            200,
            text=_GML_BODY,
            headers={"content-type": "application/xml"},
        )

    with AlkisClient(
        fast_config,
        metrics=metrics,
        transport=_mt(handler),
    ) as client:
        parcels = client.query_parcels(
            lat=53.505,
            lng=9.005,
            bundesland="nordrhein-westfalen",
        )

    assert state["calls"] == 2
    assert len(parcels) == 1
    assert parcels[0]["parcelNumber"] == "9/3"
    assert parcels[0]["source"] == "ALKIS WFS (GML)"
    # GML-shape metric incremented.
    assert (
        registry.get_sample_value(
            "lai_connector_alkis_results_total",
            {"bundesland": "nordrhein-westfalen", "shape": "gml"},
        )
        == 1.0
    )


@pytest.mark.unit
def test_query_parcels_gml_fallback_on_non_json_content_type(
    fast_config: AlkisConfig,
) -> None:
    """200 + content-type ``application/xml`` (not JSON) → switch to
    GML fallback even though status was 200."""
    state = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if "OUTPUTFORMAT" in req.url.params:
            return httpx.Response(
                200,
                text=_GML_BODY,
                headers={"content-type": "application/xml"},
            )
        return httpx.Response(
            200,
            text=_GML_BODY,
            headers={"content-type": "application/xml"},
        )

    with AlkisClient(fast_config, transport=_mt(handler)) as client:
        parcels = client.query_parcels(
            lat=53.5,
            lng=9.0,
            bundesland="bayern",
        )

    assert state["calls"] == 2
    assert len(parcels) == 1


@pytest.mark.unit
def test_query_parcels_unknown_bundesland_returns_empty_no_network(
    fast_config: AlkisConfig,
) -> None:
    """City-states (Berlin/Bremen/Hamburg) and unknown keys have no
    WFS endpoint — return [] cheaply without an HTTP call."""

    def handler(_req: httpx.Request) -> httpx.Response:
        pytest.fail("handler should not be called for unknown bundesland")
        return httpx.Response(500)

    with AlkisClient(fast_config, transport=_mt(handler)) as client:
        assert (
            client.query_parcels(
                lat=53.0,
                lng=9.0,
                bundesland="atlantis",
            )
            == []
        )


@pytest.mark.unit
def test_query_parcels_empty_features_list(
    fast_config: AlkisConfig,
) -> None:
    """A 200 JSON response with ``features: []`` returns an empty list
    cleanly (not a crash)."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": []},
            headers={"content-type": "application/json"},
        )

    with AlkisClient(fast_config, transport=_mt(handler)) as client:
        assert (
            client.query_parcels(
                lat=53.0,
                lng=9.0,
                bundesland="niedersachsen",
            )
            == []
        )


# ─────────────────────────────────────────────────────────────────────
# Retries
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_query_parcels_retries_on_530(fast_config: AlkisConfig) -> None:
    """HTTP 530 (Cloudflare origin-unreachable) is retry-eligible.
    Two 530s then 200 → tenacity covers the transient."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] < 3:
            return httpx.Response(530)
        return httpx.Response(
            200,
            json=_JSON_BODY,
            headers={"content-type": "application/json"},
        )

    with AlkisClient(fast_config, transport=_mt(handler)) as client:
        parcels = client.query_parcels(
            lat=53.5,
            lng=9.0,
            bundesland="bayern",
        )

    assert state["calls"] == 3
    assert len(parcels) == 1


@pytest.mark.unit
def test_query_parcels_retry_exhausted(fast_config: AlkisConfig) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with (
        AlkisClient(fast_config, transport=_mt(handler)) as client,
        pytest.raises(
            AlkisRetryExhaustedError,
        ) as exc_info,
    ):
        client.query_parcels(lat=53.0, lng=9.0, bundesland="niedersachsen")

    assert exc_info.value.attempts == fast_config.max_retries + 1
    assert exc_info.value.bundesland == "niedersachsen"


@pytest.mark.unit
def test_query_parcels_transport_error_propagates(
    fast_config: AlkisConfig,
) -> None:
    """httpx.ConnectError under the hood → wrapped as AlkisCallError →
    retried → exhausted."""

    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated TCP failure")

    with (
        AlkisClient(fast_config, transport=_mt(handler)) as client,
        pytest.raises(
            AlkisRetryExhaustedError,
        ),
    ):
        client.query_parcels(lat=53.0, lng=9.0, bundesland="niedersachsen")


@pytest.mark.unit
def test_query_parcels_4xx_on_gml_path_returns_empty(
    fast_config: AlkisConfig,
) -> None:
    """JSON path 400 (triggers GML fallback) → GML path also 4xx → []."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    with AlkisClient(fast_config, transport=_mt(handler)) as client:
        assert (
            client.query_parcels(
                lat=53.0,
                lng=9.0,
                bundesland="niedersachsen",
            )
            == []
        )


# ─────────────────────────────────────────────────────────────────────
# Endpoint coverage
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_alkis_wfs_endpoints_covers_12_states() -> None:
    """The 12 states with INSPIRE WFS endpoints. The 4 unrepresented
    states (Berlin, Bremen, Hamburg, Saarland) deliberately have no
    entry — city-states have no separate cadaster; Saarland's portal
    doesn't publish INSPIRE CP."""
    assert len(ALKIS_WFS_ENDPOINTS) == 12
    for key, conf in ALKIS_WFS_ENDPOINTS.items():
        assert key.islower(), f"bundesland keys must be lowercase: {key!r}"
        assert conf["url"].startswith(("http://", "https://"))
        assert conf["typename"] == "cp:CadastralParcel"
        assert conf["label"]
