"""Unit tests for :class:`lai.common.connectors.NominatimClient`.

Uses :class:`httpx.MockTransport` to stand in for the live Nominatim
service — same pattern as ``tests/unit/common/embedding/test_client.py``.
The retry tests use a tiny retry-wait config so the suite finishes in
sub-second wall-time. The throttle ``request_interval_seconds`` is set
to 0 in test configs so the suite doesn't pay 1+ seconds per geocode.
"""

from __future__ import annotations

import httpx
import pytest
from prometheus_client import CollectorRegistry

from lai.common.connectors import (
    ConnectorMetrics,
    NominatimClient,
    NominatimConfig,
    NominatimInvalidResponseError,
    NominatimRetryExhaustedError,
)

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> CollectorRegistry:
    """Isolated Prometheus registry per test — no cross-test pollution."""
    return CollectorRegistry()


@pytest.fixture
def metrics(registry: CollectorRegistry) -> ConnectorMetrics:
    return ConnectorMetrics(registry=registry)


@pytest.fixture
def fast_config() -> NominatimConfig:
    """Tiny retry waits + zero throttle so the suite runs fast."""
    return NominatimConfig(
        base_url="https://nominatim.test",
        max_retries=2,
        retry_initial_wait_seconds=0.001,
        retry_max_wait_seconds=0.001,
        request_interval_seconds=0.0,
    )


def _mock_transport(
    handler: object,
) -> httpx.MockTransport:
    """Wrap a callable in an httpx MockTransport."""
    return httpx.MockTransport(handler)


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_geocode_happy_path(
    fast_config: NominatimConfig,
    metrics: ConnectorMetrics,
    registry: CollectorRegistry,
) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"lat": "53.8688", "lon": "8.6983", "display_name": "Cuxhaven"}],
        )

    with NominatimClient(
        fast_config,
        metrics=metrics,
        transport=_mock_transport(handler),
    ) as client:
        result = client.geocode("Cuxhaven")

    assert result is not None
    lat, lng = result
    assert lat == pytest.approx(53.8688)
    assert lng == pytest.approx(8.6983)
    # Success metric incremented.
    assert (
        registry.get_sample_value(
            "lai_connector_calls_total",
            {"connector": "nominatim", "status": "success"},
        )
        == 1.0
    )


@pytest.mark.unit
def test_geocode_no_result_returns_none(fast_config: NominatimConfig) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with NominatimClient(
        fast_config,
        transport=_mock_transport(handler),
    ) as client:
        assert client.geocode("nowhere") is None


@pytest.mark.unit
def test_geocode_empty_address_short_circuits(
    fast_config: NominatimConfig,
) -> None:
    """Empty/whitespace address must return None WITHOUT hitting the
    network — verified by raising in the handler."""

    def handler(_req: httpx.Request) -> httpx.Response:
        pytest.fail("handler should not be called for empty address")
        return httpx.Response(500)  # unreachable

    with NominatimClient(
        fast_config,
        transport=_mock_transport(handler),
    ) as client:
        assert client.geocode("") is None
        assert client.geocode("   ") is None


# ─────────────────────────────────────────────────────────────────────
# bbox plausibility gate
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_geocode_bbox_gate_rejects_wrong_state(
    fast_config: NominatimConfig,
    metrics: ConnectorMetrics,
    registry: CollectorRegistry,
) -> None:
    """Cuxhaven's real coords (Niedersachsen) requested under Bremen's
    expected_bundesland → gate rejects, returns None, increments the
    bbox_rejections counter."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"lat": "53.8688", "lon": "8.6983"}],
        )

    with NominatimClient(
        fast_config,
        metrics=metrics,
        transport=_mock_transport(handler),
    ) as client:
        result = client.geocode("Cuxhaven", expected_bundesland="bremen")

    assert result is None
    assert (
        registry.get_sample_value(
            "lai_connector_nominatim_bbox_rejections_total",
            {"expected_bundesland": "bremen"},
        )
        == 1.0
    )


@pytest.mark.unit
def test_geocode_bbox_gate_accepts_correct_state(
    fast_config: NominatimConfig,
) -> None:
    """Same coords + correct expected_bundesland → accepted."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"lat": "53.8688", "lon": "8.6983"}],
        )

    with NominatimClient(
        fast_config,
        transport=_mock_transport(handler),
    ) as client:
        assert client.geocode(
            "Cuxhaven",
            expected_bundesland="niedersachsen",
        ) == (pytest.approx(53.8688), pytest.approx(8.6983))


@pytest.mark.unit
def test_geocode_bbox_gate_silent_on_unknown_bundesland(
    fast_config: NominatimConfig,
) -> None:
    """Unknown Bundesland name (typo / new state) → gate is a no-op,
    no rejection. We can't verify what we can't bound."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"lat": "53.8", "lon": "9.0"}])

    with NominatimClient(
        fast_config,
        transport=_mock_transport(handler),
    ) as client:
        # "atlantis" isn't in BUNDESLAND_BBOX → gate skipped
        assert (
            client.geocode(
                "Somewhere",
                expected_bundesland="atlantis",
            )
            is not None
        )


# ─────────────────────────────────────────────────────────────────────
# Errors and retries
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_geocode_retries_on_5xx_then_succeeds(
    fast_config: NominatimConfig,
) -> None:
    """Two 503s, then a 200 — tenacity retries cover the transient."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"lat": "53.0", "lon": "9.0"}])

    with NominatimClient(
        fast_config,
        transport=_mock_transport(handler),
    ) as client:
        result = client.geocode("retry-test")

    assert result == (pytest.approx(53.0), pytest.approx(9.0))
    assert state["calls"] == 3


@pytest.mark.unit
def test_geocode_retry_exhausted(fast_config: NominatimConfig) -> None:
    """Persistent 503 → after the retry budget is spent, raises."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with (
        NominatimClient(
            fast_config,
            transport=_mock_transport(handler),
        ) as client,
        pytest.raises(NominatimRetryExhaustedError) as exc_info,
    ):
        client.geocode("retry-exhausted")

    assert exc_info.value.attempts == fast_config.max_retries + 1


@pytest.mark.unit
def test_geocode_4xx_not_retried(fast_config: NominatimConfig) -> None:
    """4xx is a hard error, not retried. Surfaces as
    NominatimInvalidResponseError (distinct from the retry-eligible
    NominatimCallError so the no-retry intent is explicit at the
    type level)."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(403, text="forbidden")

    with (
        NominatimClient(
            fast_config,
            transport=_mock_transport(handler),
        ) as client,
        pytest.raises(NominatimInvalidResponseError),
    ):
        client.geocode("forbidden")

    assert state["calls"] == 1  # no retry on 4xx


@pytest.mark.unit
def test_geocode_invalid_json_body(fast_config: NominatimConfig) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with (
        NominatimClient(
            fast_config,
            transport=_mock_transport(handler),
        ) as client,
        pytest.raises(NominatimInvalidResponseError),
    ):
        client.geocode("bad-json")


@pytest.mark.unit
def test_geocode_non_list_body(fast_config: NominatimConfig) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"oops": "object, not list"})

    with (
        NominatimClient(
            fast_config,
            transport=_mock_transport(handler),
        ) as client,
        pytest.raises(NominatimInvalidResponseError),
    ):
        client.geocode("bad-shape")


@pytest.mark.unit
def test_geocode_missing_coords_in_result(fast_config: NominatimConfig) -> None:
    """200 + list, but the first hit has no ``lat``/``lon`` keys."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"display_name": "x"}])

    with (
        NominatimClient(
            fast_config,
            transport=_mock_transport(handler),
        ) as client,
        pytest.raises(NominatimInvalidResponseError),
    ):
        client.geocode("missing-coords")


@pytest.mark.unit
def test_geocode_non_numeric_coords(fast_config: NominatimConfig) -> None:
    """200 + valid shape, but coords are unparseable."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"lat": "abc", "lon": "def"}])

    with (
        NominatimClient(
            fast_config,
            transport=_mock_transport(handler),
        ) as client,
        pytest.raises(NominatimInvalidResponseError),
    ):
        client.geocode("bad-coords")


# ─────────────────────────────────────────────────────────────────────
# Config validation
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_config_rejects_non_http_base_url() -> None:
    with pytest.raises(Exception, match="http://"):
        NominatimConfig(base_url="ftp://nominatim.test")


@pytest.mark.unit
def test_config_strips_trailing_slash() -> None:
    cfg = NominatimConfig(base_url="https://nominatim.test/")
    assert cfg.base_url == "https://nominatim.test"
