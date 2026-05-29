"""gesetze-im-internet.de client — the German federal statute source.

Fetches the official Bundesamt für Justiz portal:

* :meth:`list_laws` downloads ``/gii-toc.xml`` (the index of every law) and
  returns the parsed :class:`LawRef` entries.
* :meth:`fetch_law_xml` downloads a single law's ``xml.zip``, unzips it
  in-memory, and returns the inner XML bytes (feed :func:`parse_law_xml`).

Production discipline matches :class:`lai.common.connectors.NominatimClient`:
sync httpx transport (mock-able via :class:`httpx.MockTransport`), tenacity
retry with exponential backoff on transport / 5xx, a typed exception
hierarchy (:class:`GesetzeError` + subclasses), Prometheus metrics on every
call, and structured logs via :mod:`structlog`. The XML parsing itself is
pure and lives in :mod:`lai.common.connectors._gii_parser`.
"""

from __future__ import annotations

import io
import time
import zipfile
from types import TracebackType

import httpx
import structlog
from defusedxml.ElementTree import ParseError
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lai.common.connectors._gii_parser import LawRef, parse_toc
from lai.common.connectors.config import GesetzeConfig
from lai.common.connectors.exceptions import (
    GesetzeCallError,
    GesetzeInvalidResponseError,
    GesetzeRetryExhaustedError,
)
from lai.common.connectors.metrics import (
    ConnectorMetrics,
    default_connector_metrics,
)

__all__ = ["GesetzeImInternetClient"]

_log = structlog.get_logger(__name__)
_CONNECTOR = "gesetze"
_TOC_PATH = "/gii-toc.xml"


class GesetzeImInternetClient:
    """Sync client for the gesetze-im-internet.de statute portal."""

    def __init__(
        self,
        config: GesetzeConfig | None = None,
        *,
        metrics: ConnectorMetrics | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config or GesetzeConfig()
        self._metrics = metrics or default_connector_metrics
        self._http = httpx.Client(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
            headers={"User-Agent": self._config.user_agent},
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> GesetzeImInternetClient:
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

    def list_laws(self) -> tuple[LawRef, ...]:
        """Return every law in the portal's table of contents.

        Raises:
            GesetzeCallError / GesetzeRetryExhaustedError: transport
                failure that persisted past all retries.
            GesetzeInvalidResponseError: the TOC body wasn't parseable XML.
        """
        content = self._fetch(_TOC_PATH)
        try:
            refs = parse_toc(content)
        except ParseError as exc:
            raise GesetzeInvalidResponseError(
                f"gii-toc.xml was not parseable XML: {exc}",
                url=self._config.base_url + _TOC_PATH,
            ) from exc
        _log.info("gesetze.list_laws.complete", laws=len(refs))
        return refs

    def fetch_law_xml(self, law: LawRef | str) -> bytes:
        """Download one law's ``xml.zip`` and return the inner XML bytes.

        ``law`` may be a :class:`LawRef` (from :meth:`list_laws`) or an
        absolute ``xml.zip`` URL.

        Raises:
            GesetzeCallError / GesetzeRetryExhaustedError: transport
                failure that persisted past all retries.
            GesetzeInvalidResponseError: the download wasn't a valid zip
                or held no ``.xml`` entry.
        """
        url = law.xml_url if isinstance(law, LawRef) else law
        content = self._fetch(url)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                xml_names = [n for n in archive.namelist() if n.lower().endswith(".xml")]
                if not xml_names:
                    raise GesetzeInvalidResponseError(f"law archive has no .xml entry: {url}", url=url)
                return archive.read(xml_names[0])
        except zipfile.BadZipFile as exc:
            raise GesetzeInvalidResponseError(f"law download was not a zip archive: {url}", url=url) from exc

    # ── Fetch / retry internals ─────────────────────────────────────

    def _fetch(self, url: str) -> bytes:
        """Fetch ``url`` with retry + metrics; throttle after success."""
        t0 = time.monotonic()
        try:
            content = self._fetch_with_retry(url)
        except (GesetzeCallError, GesetzeRetryExhaustedError):
            self._metrics.calls_total.labels(connector=_CONNECTOR, status="error").inc()
            self._metrics.request_duration_seconds.labels(connector=_CONNECTOR, status="error").observe(
                time.monotonic() - t0
            )
            raise

        duration = time.monotonic() - t0
        self._metrics.calls_total.labels(connector=_CONNECTOR, status="success").inc()
        self._metrics.request_duration_seconds.labels(connector=_CONNECTOR, status="success").observe(duration)
        if self._config.request_interval_seconds:
            time.sleep(self._config.request_interval_seconds)
        return content

    def _fetch_with_retry(self, url: str) -> bytes:
        attempt_number = 0
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._config.max_retries + 1),
                wait=wait_exponential(
                    multiplier=self._config.retry_initial_wait_seconds,
                    max=self._config.retry_max_wait_seconds,
                ),
                retry=retry_if_exception_type(GesetzeCallError),
                reraise=False,
            ):
                attempt_number = attempt.retry_state.attempt_number
                with attempt:
                    if attempt_number > 1:
                        self._metrics.retries_total.labels(connector=_CONNECTOR).inc()
                    return self._fetch_once(url)
        except RetryError as exc:
            cause = exc.last_attempt.exception()
            raise GesetzeRetryExhaustedError(
                f"all {attempt_number} attempt(s) failed for {url}",
                attempts=attempt_number,
            ) from cause
        # Unreachable — Retrying() raises or returns from inside the
        # with-block. Listed for mypy completeness.
        raise GesetzeRetryExhaustedError(  # pragma: no cover
            "Retrying loop terminated without a result",
            attempts=max(attempt_number, 1),
        )

    def _fetch_once(self, url: str) -> bytes:
        """One HTTP GET. Raises :class:`GesetzeCallError` on transport / 5xx
        (retried) and :class:`GesetzeInvalidResponseError` on 4xx (not)."""
        try:
            resp = self._http.get(url)
        except httpx.HTTPError as exc:
            raise GesetzeCallError(f"gesetze-im-internet transport failure: {exc}", url=url) from exc

        if 500 <= resp.status_code < 600:
            raise GesetzeCallError(
                f"gesetze-im-internet returned HTTP {resp.status_code}",
                status_code=resp.status_code,
                url=str(resp.request.url),
            )
        if resp.status_code != 200:
            raise GesetzeInvalidResponseError(
                f"gesetze-im-internet returned HTTP {resp.status_code}",
                url=url,
                raw_response=resp.text,
            )
        return resp.content
