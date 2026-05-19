"""Nominatim (OSM geocoder) client.

Sync-only — the volume is low (a few dozen requests per DDiQ report;
zero outside report runs) and the hosted OSM policy throttles us to
1 req/s anyway, so the latency wins from an async surface don't
materialise. If the throttle ever moves to a self-hosted instance,
:class:`AsyncNominatimClient` can land as a sibling without touching
this one.

Production discipline matches :class:`lai.common.embedding.SyncEmbeddingClient`:

* httpx transport (mock-able via :class:`httpx.MockTransport` for tests)
* tenacity retry with exponential backoff on transport / 5xx
* typed exception hierarchy (:class:`NominatimError` and subclasses)
* Prometheus metrics on every call (:class:`ConnectorMetrics`)
* structured logs via :mod:`structlog`
* explicit Bundesland plausibility gate — the gate is part of the
  client's contract, not an after-the-fact wrapper, because the gate
  exists *to filter Nominatim's first-match-wins quirk* and only
  makes sense at this layer.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lai.common.connectors.config import NominatimConfig
from lai.common.connectors.exceptions import (
    NominatimCallError,
    NominatimInvalidResponseError,
    NominatimRetryExhaustedError,
)
from lai.common.connectors.metrics import (
    ConnectorMetrics,
    default_connector_metrics,
)
from lai.common.jurisdiction import BUNDESLAND_BBOX, point_in_bbox

if TYPE_CHECKING:
    pass

__all__ = ["NominatimClient"]

_log = structlog.get_logger(__name__)


class NominatimClient:
    """Sync Nominatim geocoder client.

    Single-method client: :meth:`geocode` takes an address string and
    returns ``(lat, lng) | None``. Optional ``expected_bundesland``
    triggers the bbox plausibility gate (see :data:`BUNDESLAND_BBOX`)
    — rejecting Nominatim's first-match-wins false positives
    (Cuxhaven→Bremen and similar).
    """

    def __init__(
        self,
        config: NominatimConfig | None = None,
        *,
        metrics: ConnectorMetrics | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config or NominatimConfig()
        self._metrics = metrics or default_connector_metrics
        self._http = httpx.Client(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            headers={"User-Agent": self._config.user_agent},
            transport=transport,
        )

    def __enter__(self) -> NominatimClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # ── Public API ──────────────────────────────────────────────────

    def geocode(
        self,
        address: str,
        *,
        country_codes: str = "de",
        expected_bundesland: str | None = None,
    ) -> tuple[float, float] | None:
        """Geocode ``address`` to ``(lat, lng)``.

        Args:
            address: Free-text address. Whitespace-only / empty returns
                ``None`` cheaply.
            country_codes: Comma-separated ISO 3166-1 alpha-2 codes
                Nominatim should restrict matches to. Default ``"de"``
                (German DDiQ scope).
            expected_bundesland: Lowercase Bundesland name (e.g.
                ``"niedersachsen"``). When set AND the bbox table
                covers that state (see :func:`has_bbox`), the returned
                coordinates are checked against the state's bbox; a
                miss is rejected (returns ``None``) and the
                :data:`ConnectorMetrics.bbox_rejections_total` counter
                is incremented.

        Returns:
            ``(lat, lng)`` on success, ``None`` if the address is
            empty, Nominatim returned no result, or the bbox gate
            rejected the result.

        Raises:
            NominatimCallError: HTTP transport failure (5xx, timeout,
                connect-refused) that persisted past all retries.
            NominatimRetryExhaustedError: tenacity retry budget
                exhausted.
            NominatimInvalidResponseError: 2xx body wasn't valid JSON
                or didn't carry the expected fields.

        Notes:
            * Sleeps :attr:`NominatimConfig.request_interval_seconds`
              AFTER a successful call, in keeping with OSM's
              1 req/sec policy. Failed calls do NOT sleep (you've
              already paid the latency cost of the failure).
            * The address is sent in the query string; we don't URL-
              encode manually because httpx does that.
        """
        if not address or not address.strip():
            return None

        params = {
            "q": address,
            "format": "json",
            "limit": "1",
            "countrycodes": country_codes,
        }

        t0 = time.monotonic()
        try:
            results = self._call_with_retry(params)
        except (NominatimCallError, NominatimRetryExhaustedError):
            self._metrics.calls_total.labels(connector="nominatim", status="error").inc()
            self._metrics.request_duration_seconds.labels(connector="nominatim", status="error").observe(
                time.monotonic() - t0
            )
            raise

        duration = time.monotonic() - t0

        if not results:
            self._metrics.calls_total.labels(connector="nominatim", status="success").inc()
            self._metrics.request_duration_seconds.labels(connector="nominatim", status="success").observe(duration)
            _log.info(
                "nominatim.geocode.no_result",
                address=address[:120],
                duration_seconds=round(duration, 4),
            )
            return None

        # Parse the first hit. Surface a typed error if the body
        # doesn't carry numeric lat/lon — better than the legacy
        # ``KeyError: 'lat'`` that the upstream caller had to swallow.
        first = results[0]
        try:
            lat = float(first["lat"])
            lng = float(first["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            self._metrics.calls_total.labels(connector="nominatim", status="error").inc()
            raise NominatimInvalidResponseError(
                f"Nominatim result missing numeric lat/lon: {exc}",
                raw_response=str(first),
            ) from exc

        # bbox plausibility gate — rejects Cuxhaven→Bremen style mis-
        # resolutions. Unknown Bundeslaender (not in the bbox table)
        # pass silently — we can't gate what we can't verify.
        bbox = BUNDESLAND_BBOX.get(expected_bundesland) if expected_bundesland else None
        if bbox is not None and not point_in_bbox(lat, lng, bbox):
            self._metrics.bbox_rejections_total.labels(expected_bundesland=expected_bundesland).inc()
            self._metrics.calls_total.labels(connector="nominatim", status="rejected").inc()
            self._metrics.request_duration_seconds.labels(connector="nominatim", status="rejected").observe(duration)
            _log.warning(
                "nominatim.geocode.bbox_rejected",
                address=address[:120],
                returned_lat=round(lat, 4),
                returned_lng=round(lng, 4),
                expected_bundesland=expected_bundesland,
                duration_seconds=round(duration, 4),
            )
            # Still respect the throttle — we consumed a request.
            if self._config.request_interval_seconds:
                time.sleep(self._config.request_interval_seconds)
            return None

        self._metrics.calls_total.labels(connector="nominatim", status="success").inc()
        self._metrics.request_duration_seconds.labels(connector="nominatim", status="success").observe(duration)
        _log.info(
            "nominatim.geocode.complete",
            address=address[:120],
            lat=round(lat, 4),
            lng=round(lng, 4),
            duration_seconds=round(duration, 4),
        )
        if self._config.request_interval_seconds:
            time.sleep(self._config.request_interval_seconds)
        return (lat, lng)

    # ── Retry / call internals ──────────────────────────────────────

    def _call_with_retry(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """Wrap :meth:`_call_once` with tenacity. Returns the parsed
        JSON list. Raises on persistent failure."""
        attempt_number = 0
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(NominatimCallError),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.labels(connector="nominatim").inc()
                    return self._call_once(params)
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise NominatimRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
            ) from cause
        # Unreachable — Retrying() raises or returns from inside the
        # with-block. Listed for mypy completeness.
        raise NominatimRetryExhaustedError(  # pragma: no cover
            "Retrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    def _call_once(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """One HTTP call to ``GET /search``. Raises
        :class:`NominatimCallError` on transport / 5xx so tenacity
        retries; raises :class:`NominatimInvalidResponseError` on
        2xx-but-not-JSON (NOT retried — body is unlikely to change).
        """
        try:
            resp = self._http.get("/search", params=params)
        except httpx.HTTPError as exc:
            raise NominatimCallError(
                f"Nominatim transport failure: {exc}",
                url=str(getattr(exc.request, "url", None)),
            ) from exc

        if 500 <= resp.status_code < 600:
            # 5xx + transport errors retry. NominatimCallError IS the
            # retry-eligible exception class (see _call_with_retry's
            # retry_if_exception_type filter).
            raise NominatimCallError(
                f"Nominatim returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                url=str(resp.request.url),
            )
        if resp.status_code != 200:
            # 4xx — hard error, NOT retried. Wrap as
            # NominatimInvalidResponseError because tenacity's
            # retry filter only matches NominatimCallError; using a
            # different class makes the no-retry path explicit at the
            # type level rather than depending on a deny-list.
            raise NominatimInvalidResponseError(
                f"Nominatim returned HTTP {resp.status_code}: {resp.text[:200]}",
                raw_response=resp.text,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise NominatimInvalidResponseError(
                f"Nominatim response was not JSON: {exc}",
                raw_response=resp.text,
            ) from exc

        if not isinstance(body, list):
            raise NominatimInvalidResponseError(
                f"Nominatim returned non-list body: {type(body).__name__}",
                raw_response=str(body),
            )
        return body
