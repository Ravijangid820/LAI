"""Tests for the geocoding + ALKIS cache shims in ``ddiq_report``.

``geocode_address`` and ``alkis_query_parcels`` wrap the
``lai.common.connectors`` clients with a Postgres-backed cache. The
HTTP + retry + bbox-gate logic is tested in the connector's own suite
(``tests/unit/common/connectors/``); here we test the DDiQ-side
behaviour that lives in ``ddiq_report``:

* cache hit fast path
* cache miss → connector call → cache write
* the "don't cache a None / bbox-rejected result" contract
* connector errors are swallowed (None / [] rather than a crash
  mid-report)

Both the connector singleton and ``get_conn`` are monkeypatched.
"""

from __future__ import annotations

import pytest

import ddiq_report
from lai.common.connectors import AlkisError, NominatimError


# ── geocode_address ──────────────────────────────────────────────────


class TestGeocodeAddress:
    def test_empty_address_returns_none_without_db(self, monkeypatch) -> None:
        """Short-circuits before touching the pool — no get_conn call."""
        def explode():
            raise AssertionError("get_conn must not be called for empty address")
        monkeypatch.setattr(ddiq_report, "get_conn", explode)
        assert ddiq_report.geocode_address("") is None
        assert ddiq_report.geocode_address("   ") is None

    def test_cache_hit_skips_connector(self, monkeypatch, fake_db) -> None:
        fake_db(fetchone=(53.86, 8.69))  # cached (lat, lng)

        def explode(*a, **kw):
            raise AssertionError("connector must not be called on cache hit")
        monkeypatch.setattr(ddiq_report._NOMINATIM_CLIENT, "geocode", explode)

        out = ddiq_report.geocode_address("Cuxhaven, Niedersachsen")
        assert out == (53.86, 8.69)

    def test_cache_miss_calls_connector_and_writes(self, monkeypatch, fake_db) -> None:
        conn, cur = fake_db(fetchone=None)  # cache miss
        monkeypatch.setattr(
            ddiq_report._NOMINATIM_CLIENT, "geocode",
            lambda address, expected_bundesland=None: (53.86, 8.69),
        )
        out = ddiq_report.geocode_address("Cuxhaven", expected_bundesland="niedersachsen")
        assert out == (53.86, 8.69)
        # An INSERT … ON CONFLICT must have run to cache the result.
        assert conn.committed
        insert_sqls = [sql for sql, _ in cur.executed if "INSERT INTO ddiq_geocode_cache" in sql]
        assert len(insert_sqls) == 1

    def test_none_result_not_cached(self, monkeypatch, fake_db) -> None:
        """Nominatim returned nothing OR the bbox gate rejected the
        hit → connector returns None → we must NOT write a cache row
        (so a later, more-specific query gets a fresh attempt)."""
        conn, cur = fake_db(fetchone=None)
        monkeypatch.setattr(
            ddiq_report._NOMINATIM_CLIENT, "geocode",
            lambda address, expected_bundesland=None: None,
        )
        out = ddiq_report.geocode_address("Nowhere", expected_bundesland="bayern")
        assert out is None
        assert not conn.committed
        assert not any("INSERT INTO ddiq_geocode_cache" in sql for sql, _ in cur.executed)

    def test_connector_error_swallowed(self, monkeypatch, fake_db) -> None:
        """A Nominatim outage (retry budget exhausted) must not crash
        the report — geocoding is best-effort."""
        fake_db(fetchone=None)

        def boom(address, expected_bundesland=None):
            raise NominatimError("retries exhausted")
        monkeypatch.setattr(ddiq_report._NOMINATIM_CLIENT, "geocode", boom)

        assert ddiq_report.geocode_address("Cuxhaven") is None


# ── alkis_query_parcels ──────────────────────────────────────────────


class TestAlkisQueryParcels:
    def test_cache_hit_returns_parsed_list(self, monkeypatch, fake_db) -> None:
        cached = [{"parcelNumber": "12/4", "gemarkung": "Test"}]
        fake_db(fetchone=(cached,))  # parcel_data column already a list

        def explode(*a, **kw):
            raise AssertionError("connector must not be called on cache hit")
        monkeypatch.setattr(ddiq_report._ALKIS_CLIENT, "query_parcels", explode)

        out = ddiq_report.alkis_query_parcels(53.86, 8.69, "niedersachsen")
        assert out == cached

    def test_cache_hit_json_string(self, monkeypatch, fake_db) -> None:
        """The cache column round-trips as JSON text in some rows; the
        shim json.loads it when it isn't already a list."""
        import json
        cached = [{"parcelNumber": "7/2"}]
        fake_db(fetchone=(json.dumps(cached),))
        out = ddiq_report.alkis_query_parcels(53.0, 8.0, "niedersachsen")
        assert out == cached

    def test_cache_miss_calls_connector_and_writes(self, monkeypatch, fake_db) -> None:
        conn, cur = fake_db(fetchone=None)
        parcels = [{"parcelNumber": "99/1", "gemarkung": "Lamstedt"}]
        monkeypatch.setattr(
            ddiq_report._ALKIS_CLIENT, "query_parcels",
            lambda lat, lng, bundesland, radius_m=150: parcels,
        )
        out = ddiq_report.alkis_query_parcels(53.0, 8.0, "niedersachsen")
        assert out == parcels
        assert any("INSERT INTO ddiq_parcel_cache" in sql for sql, _ in cur.executed)

    def test_connector_error_returns_empty(self, monkeypatch, fake_db) -> None:
        fake_db(fetchone=None)

        def boom(lat, lng, bundesland, radius_m=150):
            raise AlkisError("WFS down")
        monkeypatch.setattr(ddiq_report._ALKIS_CLIENT, "query_parcels", boom)

        assert ddiq_report.alkis_query_parcels(53.0, 8.0, "nrw") == []


# ── geocode_project_location (wiring over geocode_address) ───────────


class TestGeocodeProjectLocation:
    def _sections(self, location="", name=""):
        from ddiq.models import AusgabeblattRow, AusgabeblattSection
        rows = []
        if location:
            rows.append(AusgabeblattRow(label="Location", value=location))
        if name:
            rows.append(AusgabeblattRow(label="Project Name", value=name))
        return [AusgabeblattSection(id="overview", title="O", rows=rows)]

    def test_uses_location_first(self, monkeypatch) -> None:
        calls: list[tuple[str, str | None]] = []

        def fake_geocode(address, expected_bundesland=None):
            calls.append((address, expected_bundesland))
            return (53.86, 8.69)

        monkeypatch.setattr(ddiq_report, "geocode_address", fake_geocode)
        out = ddiq_report.geocode_project_location(
            self._sections(location="Cuxhaven, Niedersachsen"),
        )
        assert out == (53.86, 8.69)
        # First attempt is the full location string; detect_bundesland
        # should have resolved niedersachsen for the gate.
        assert calls[0][0] == "Cuxhaven, Niedersachsen"
        assert calls[0][1] == "niedersachsen"

    def test_falls_back_to_tokens(self, monkeypatch) -> None:
        """When the full location string doesn't geocode, the shim
        retries with individual tokens (+ ', Germany')."""
        attempts: list[str] = []

        def fake_geocode(address, expected_bundesland=None):
            attempts.append(address)
            # Only succeed on a token-level query.
            return (53.0, 8.0) if address.endswith(", Germany") else None

        monkeypatch.setattr(ddiq_report, "geocode_address", fake_geocode)
        out = ddiq_report.geocode_project_location(
            self._sections(location="Obscure Hamlet, Niedersachsen"),
        )
        assert out == (53.0, 8.0)
        assert len(attempts) > 1
        assert any(a.endswith(", Germany") for a in attempts)

    def test_falls_back_to_project_name(self, monkeypatch) -> None:
        attempts: list[str] = []

        def fake_geocode(address, expected_bundesland=None):
            attempts.append(address)
            return (52.0, 9.0) if "Lamstedt" in address else None

        monkeypatch.setattr(ddiq_report, "geocode_address", fake_geocode)
        out = ddiq_report.geocode_project_location(
            self._sections(location="", name="Windpark Lamstedt"),
        )
        assert out == (52.0, 9.0)
        # "Windpark" prefix is stripped before geocoding the name.
        assert any("Lamstedt" in a and "Windpark" not in a for a in attempts)

    def test_returns_none_when_nothing_resolves(self, monkeypatch) -> None:
        monkeypatch.setattr(
            ddiq_report, "geocode_address",
            lambda address, expected_bundesland=None: None,
        )
        out = ddiq_report.geocode_project_location(
            self._sections(location="X, Y", name="Windpark Z"),
        )
        assert out is None

    def test_empty_sections_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(
            ddiq_report, "geocode_address",
            lambda address, expected_bundesland=None: (1.0, 1.0),
        )
        # No Location and no Project Name → no geocode attempt → None.
        assert ddiq_report.geocode_project_location(self._sections()) is None
