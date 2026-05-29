"""Unit tests for the gesetze-im-internet.de client + TOC parser."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable

import httpx
import pytest
from prometheus_client import CollectorRegistry

from lai.common.connectors._gii_parser import parse_toc
from lai.common.connectors.config import GesetzeConfig
from lai.common.connectors.exceptions import (
    GesetzeInvalidResponseError,
    GesetzeRetryExhaustedError,
)
from lai.common.connectors.gesetze import GesetzeImInternetClient
from lai.common.connectors.metrics import ConnectorMetrics

pytestmark = pytest.mark.unit

_TOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<items>
  <item><title>Bundes-Immissionsschutzgesetz</title>
    <link>http://www.gesetze-im-internet.de/bimschg/xml.zip</link></item>
  <item><title>Baugesetzbuch</title>
    <link>http://www.gesetze-im-internet.de/bbaug/xml.zip</link></item>
  <item><title>No link here</title></item>
</items>"""

_LAW_XML = (
    b"<dokumente><norm><metadaten><jurabk>BImSchG</jurabk>"
    b"<enbez>\xc2\xa7 1</enbez></metadaten><textdaten><text>"
    b"<Content><P>Text.</P></Content></text></textdaten></norm></dokumente>"
)

Handler = Callable[[httpx.Request], httpx.Response]


def _zip_bytes(xml: bytes = _LAW_XML, *, name: str = "BJNR007210974.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(name, xml)
    return buf.getvalue()


def _fast_config() -> GesetzeConfig:
    # No throttle sleep + near-zero backoff so the suite stays sub-second.
    return GesetzeConfig(
        request_interval_seconds=0.0,
        retry_initial_wait_seconds=0.001,
        retry_max_wait_seconds=0.01,
        max_retries=1,
    )


def _client(handler: Handler, *, registry: CollectorRegistry | None = None) -> GesetzeImInternetClient:
    metrics = ConnectorMetrics(registry=registry) if registry is not None else None
    return GesetzeImInternetClient(
        _fast_config(),
        metrics=metrics,
        transport=httpx.MockTransport(handler),
    )


def test_list_laws_parses_and_normalises_toc() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gii-toc.xml"
        return httpx.Response(200, content=_TOC)

    with _client(handler) as client:
        laws = client.list_laws()

    assert [law.slug for law in laws] == ["bimschg", "bbaug"]  # no-link item dropped
    assert laws[0].title == "Bundes-Immissionsschutzgesetz"
    # http:// link normalised to https://
    assert laws[0].xml_url == "https://www.gesetze-im-internet.de/bimschg/xml.zip"


def test_fetch_law_xml_unzips_inner_document() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_zip_bytes())

    with _client(handler) as client:
        xml = client.fetch_law_xml("https://www.gesetze-im-internet.de/bimschg/xml.zip")

    assert b"<jurabk>BImSchG</jurabk>" in xml


def test_fetch_law_xml_rejects_non_zip() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"this is not a zip archive")

    with _client(handler) as client, pytest.raises(GesetzeInvalidResponseError):
        client.fetch_law_xml("https://x/y/xml.zip")


def test_fetch_law_xml_rejects_zip_without_xml() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_zip_bytes(name="readme.txt"))

    with _client(handler) as client, pytest.raises(GesetzeInvalidResponseError):
        client.fetch_law_xml("https://x/y/xml.zip")


def test_4xx_raises_invalid_response_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    with _client(handler) as client, pytest.raises(GesetzeInvalidResponseError):
        client.list_laws()


def test_malformed_toc_xml_raises_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<items><item></items>")

    with _client(handler) as client, pytest.raises(GesetzeInvalidResponseError):
        client.list_laws()


def test_5xx_retries_then_exhausts_and_counts_metrics() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="down")

    registry = CollectorRegistry()
    with _client(handler, registry=registry) as client, pytest.raises(GesetzeRetryExhaustedError):
        client.list_laws()

    assert calls["n"] == 2  # max_retries=1 → 2 attempts
    assert registry.get_sample_value("lai_connector_calls_total", {"connector": "gesetze", "status": "error"}) == 1.0
    assert registry.get_sample_value("lai_connector_retries_total", {"connector": "gesetze"}) == 1.0


def test_transport_error_retried_then_exhausts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _client(handler) as client, pytest.raises(GesetzeRetryExhaustedError):
        client.list_laws()


def test_success_records_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_TOC)

    registry = CollectorRegistry()
    with _client(handler, registry=registry) as client:
        client.list_laws()

    assert registry.get_sample_value("lai_connector_calls_total", {"connector": "gesetze", "status": "success"}) == 1.0


def test_parse_toc_directly_skips_linkless_items() -> None:
    refs = parse_toc(_TOC)
    assert [r.slug for r in refs] == ["bimschg", "bbaug"]
    assert all(r.xml_url.startswith("https://") for r in refs)
