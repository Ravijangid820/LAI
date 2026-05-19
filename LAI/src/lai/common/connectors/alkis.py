"""ALKIS INSPIRE WFS client.

The 12 German state cadastral services that publish parcels via WFS
(see :data:`ALKIS_WFS_ENDPOINTS`). Half speak GeoJSON, half speak GML
3.2 — :class:`AlkisClient` tries JSON first and falls back to GML on
HTTP 400 or a non-``application/json`` content-type, mirroring the
legacy DDiQ behaviour we're replacing.

Same production discipline as :class:`~lai.common.connectors.nominatim.NominatimClient`:
httpx transport, tenacity retries, typed exceptions, Prometheus metrics,
structured logs. Sync-only; if a future async surface is justified it
lands as a sibling.

Notable retry behaviour:

* **HTTP 530** (Cloudflare "origin unreachable") is retried —
  occasionally surfaces on the NRW and Bayern WFS endpoints during
  upstream maintenance windows.
* **HTTP 400 with GML fallback** is NOT a retry; it's a documented
  shape-detection path. Some state WFS (notably NRW INSPIRE-CP) return
  400 when asked for ``OUTPUTFORMAT=application/json`` and only speak
  GML. The fallback re-issues the request without the ``OUTPUTFORMAT``
  parameter, which yields GML.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Final

import httpx
import structlog
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lai.common.connectors._parsers import (
    ParcelDict,
    parse_alkis_feature,
    parse_alkis_xml,
)
from lai.common.connectors.config import (
    ALKIS_WFS_ENDPOINTS,
    AlkisConfig,
)
from lai.common.connectors.exceptions import (
    AlkisCallError,
    AlkisRetryExhaustedError,
)
from lai.common.connectors.metrics import (
    ConnectorMetrics,
    default_connector_metrics,
)

__all__ = ["AlkisClient"]

_log = structlog.get_logger(__name__)

# 1° latitude ≈ 111 km — used to convert ``radius_m`` to a bbox buffer
# in degrees. Approximation: at German latitudes the longitude scale
# is ~67 km/deg, so this slightly over-buffers in the lng axis. Over-
# buffering is the safe direction (extra features filtered downstream)
# vs. under-buffering (missing the parcel under the WEA mast).
_METERS_PER_DEGREE_LAT: Final[float] = 111_000.0


class AlkisClient:
    """Sync ALKIS INSPIRE WFS client.

    Single-method client: :meth:`query_parcels` takes a ``(lat, lng,
    bundesland)`` triple and returns a list of parcel dicts. Returns
    ``[]`` for unsupported Bundeslaender (no WFS endpoint), no parcels
    in the bbox, or a 4xx response after the GML fallback.
    """

    def __init__(
        self,
        config: AlkisConfig | None = None,
        *,
        metrics: ConnectorMetrics | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config or AlkisConfig()
        self._metrics = metrics or default_connector_metrics
        # No base_url — each Bundesland has its own URL from
        # ALKIS_WFS_ENDPOINTS; we pass the absolute URL per request.
        self._http = httpx.Client(
            timeout=self._config.timeout_seconds,
            headers={"User-Agent": self._config.user_agent},
            transport=transport,
        )

    def __enter__(self) -> AlkisClient:
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

    def query_parcels(
        self,
        lat: float,
        lng: float,
        bundesland: str,
        *,
        radius_m: float = 150.0,
    ) -> list[ParcelDict]:
        """Fetch ALKIS parcels in a bbox around ``(lat, lng)``.

        Args:
            lat / lng: WGS-84 query centre.
            bundesland: Lowercase state key (e.g. ``"niedersachsen"``).
                Must be in :data:`ALKIS_WFS_ENDPOINTS` — unknown keys
                return ``[]`` cheaply (no network call). City-states
                like Berlin / Bremen / Hamburg have no separate WFS
                and are returned ``[]``.
            radius_m: Buffer around the centre point in metres,
                converted to a square bbox via 1° lat ≈ 111 km.

        Returns:
            List of parcel dicts. Each dict has at minimum:
            ``parcelNumber``, ``gemarkung``, ``flur``, ``polygon``,
            ``area_m2``, ``source``. GML-path results add
            ``nationalCadastralReference``.

        Raises:
            AlkisRetryExhaustedError: all retry attempts (incl. HTTP
                530 retries) failed.
            AlkisInvalidResponseError: 2xx response that couldn't be
                parsed as either JSON features or GML.

        Operational note:
            Empty result lists are a normal outcome (the bbox may not
            cover any parcels for very rural sites). The caller (DDiQ)
            distinguishes "no parcels here" from "ALKIS unavailable"
            by checking for an exception vs. an empty list.
        """
        config = ALKIS_WFS_ENDPOINTS.get(bundesland)
        if not config:
            return []

        url = config["url"]
        typename = config["typename"]
        label = config["label"]

        # Convert radius_m to a degree buffer (square bbox).
        buf = radius_m / _METERS_PER_DEGREE_LAT
        bbox = f"{lat - buf},{lng - buf},{lat + buf},{lng + buf},EPSG:4326"
        params_json = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": typename,
            "SRSNAME": "EPSG:4326",
            "BBOX": bbox,
            "COUNT": str(self._config.feature_count),
            "OUTPUTFORMAT": "application/json",
        }

        t0 = time.monotonic()
        try:
            parcels = self._call_with_retry(
                url=url,
                params_json=params_json,
                lat=lat,
                lng=lng,
                bundesland=bundesland,
            )
        except (AlkisCallError, AlkisRetryExhaustedError):
            self._metrics.calls_total.labels(connector="alkis", status="error").inc()
            self._metrics.request_duration_seconds.labels(connector="alkis", status="error").observe(
                time.monotonic() - t0
            )
            raise

        duration = time.monotonic() - t0
        self._metrics.calls_total.labels(connector="alkis", status="success").inc()
        self._metrics.request_duration_seconds.labels(connector="alkis", status="success").observe(duration)
        _log.info(
            "alkis.query.complete",
            bundesland=bundesland,
            label=label,
            lat=round(lat, 5),
            lng=round(lng, 5),
            n_parcels=len(parcels),
            duration_seconds=round(duration, 3),
        )
        return parcels

    # ── Retry / call internals ──────────────────────────────────────

    def _call_with_retry(
        self,
        *,
        url: str,
        params_json: dict[str, str],
        lat: float,
        lng: float,
        bundesland: str,
    ) -> list[ParcelDict]:
        """Wrap :meth:`_call_once` with tenacity. Retries on
        :class:`AlkisCallError` only — invalid-response is not retried
        because the response body is unlikely to change between
        attempts."""
        attempt_number = 0
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(AlkisCallError),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.labels(connector="alkis").inc()
                    return self._call_once(
                        url=url,
                        params_json=params_json,
                        lat=lat,
                        lng=lng,
                        bundesland=bundesland,
                    )
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise AlkisRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed",
                attempts=attempt_number,
                bundesland=bundesland,
            ) from cause
        raise AlkisRetryExhaustedError(  # pragma: no cover
            "Retrying loop terminated without a result",
            attempts=max(attempt_number, 1),
            bundesland=bundesland,
        )

    def _call_once(
        self,
        *,
        url: str,
        params_json: dict[str, str],
        lat: float,
        lng: float,
        bundesland: str,
    ) -> list[ParcelDict]:
        """One HTTP call. Handles the JSON-or-GML shape detection
        internally — a 400 or non-JSON content-type triggers the GML
        retry (NOT counted as a tenacity retry; this is shape
        detection, not transient failure)."""
        # Try JSON first.
        try:
            resp = self._http.get(url, params=params_json)
        except httpx.HTTPError as exc:
            raise AlkisCallError(
                f"ALKIS transport failure ({bundesland}): {exc}",
                url=url,
                bundesland=bundesland,
            ) from exc

        if resp.status_code == 530 or 500 <= resp.status_code < 600:
            # 530 = Cloudflare origin unreachable. Retry-eligible.
            raise AlkisCallError(
                f"ALKIS returned HTTP {resp.status_code} ({bundesland})",
                status_code=resp.status_code,
                url=url,
                bundesland=bundesland,
            )

        content_type = (resp.headers.get("content-type") or "").lower()
        wants_gml = resp.status_code == 400 or "application/json" not in content_type

        parsed: list[ParcelDict]
        shape: str
        if wants_gml:
            # Drop OUTPUTFORMAT and re-request as GML. The state WFS
            # serves GML by default for INSPIRE CP.
            params_gml = {k: v for k, v in params_json.items() if k != "OUTPUTFORMAT"}
            try:
                resp_gml = self._http.get(url, params=params_gml)
            except httpx.HTTPError as exc:
                raise AlkisCallError(
                    f"ALKIS GML retry transport failure ({bundesland}): {exc}",
                    url=url,
                    bundesland=bundesland,
                ) from exc
            if 500 <= resp_gml.status_code < 600 or resp_gml.status_code == 530:
                raise AlkisCallError(
                    f"ALKIS GML retry HTTP {resp_gml.status_code} ({bundesland})",
                    status_code=resp_gml.status_code,
                    url=url,
                    bundesland=bundesland,
                )
            if resp_gml.status_code != 200:
                # 4xx on the GML path too — give up, return [].
                _log.warning(
                    "alkis.gml_retry.unexpected_status",
                    bundesland=bundesland,
                    status_code=resp_gml.status_code,
                )
                return []
            parsed = parse_alkis_xml(
                resp_gml.text,
                fallback_lat=lat,
                fallback_lng=lng,
            )
            shape = "gml"
        else:
            if resp.status_code != 200:
                _log.warning(
                    "alkis.json.unexpected_status",
                    bundesland=bundesland,
                    status_code=resp.status_code,
                )
                return []
            try:
                body = resp.json()
            except ValueError:
                # Header said JSON but body isn't — try the GML parser
                # on it before giving up. Some misconfigured proxies do
                # this.
                parsed = parse_alkis_xml(
                    resp.text,
                    fallback_lat=lat,
                    fallback_lng=lng,
                )
                shape = "gml"
            else:
                features = body.get("features", []) if isinstance(body, dict) else []
                parsed = [p for p in (parse_alkis_feature(f) for f in features) if p]
                shape = "json"

        if parsed:
            self._metrics.alkis_results_total.labels(
                bundesland=bundesland,
                shape=shape,
            ).inc(len(parsed))
        return parsed
