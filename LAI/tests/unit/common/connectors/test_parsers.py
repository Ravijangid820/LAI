"""Unit tests for :mod:`lai.common.connectors._parsers`.

Pure functions, no network. Cover:

* :func:`parse_alkis_feature` happy path + 9 documented edge cases
* :func:`parse_alkis_xml` happy path + malformed-XML graceful fallback
* :func:`parse_pos_list` numeric handling + non-numeric token skip
* :func:`make_synthetic_polygon` shape + size guarantees
"""

from __future__ import annotations

import pytest

from lai.common.connectors._parsers import (
    make_synthetic_polygon,
    parse_alkis_feature,
    parse_alkis_xml,
    parse_pos_list,
)

# ─────────────────────────────────────────────────────────────────────
# parse_alkis_feature
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_alkis_feature_happy_path() -> None:
    feat = {
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
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    assert out["parcelNumber"] == "12/4"
    assert out["gemarkung"] == "Lamstedt"
    assert out["flur"] == 3
    assert out["area_m2"] == pytest.approx(12345.67)
    # GeoJSON [lng, lat] flipped to [lat, lng] for Leaflet
    assert out["polygon"][0] == [53.5, 9.0]
    assert out["source"] == "ALKIS WFS"


@pytest.mark.unit
def test_parse_alkis_feature_missing_parcel_number_returns_none() -> None:
    """No label / no usable NCR → None (caller filters out)."""
    feat = {"properties": {"gemarkung": "Test"}, "geometry": {}}
    assert parse_alkis_feature(feat) is None


@pytest.mark.unit
def test_parse_alkis_feature_falls_back_to_national_cadastral_reference() -> None:
    """When no direct ``label`` / ``flurstuecksnummer`` / etc., the
    tail of ``nationalCadastralReference`` becomes the parcel number."""
    feat = {
        "properties": {
            "nationalCadastralReference": "DE-NW-12345-00056",
        },
        "geometry": {},
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    # "00056" → strip leading zeros → "56"
    assert out["parcelNumber"] == "56"


@pytest.mark.unit
def test_parse_alkis_feature_handles_bad_flur_value() -> None:
    """Track A item 6 regression: a non-numeric flur value used to pin
    the field to 0 because the legacy ``pass; break`` exited the
    candidate-key loop. Now we ``continue`` past bad values and try
    the next candidate key."""
    feat = {
        "properties": {
            "label": "P-1",
            "flurnummer": "abc",  # bad — should be skipped
            "flur": "7",  # good — should win
            "flurNr": "99",  # ignored once flur=7 was accepted
        },
        "geometry": {},
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    assert out["flur"] == 7  # not 0, not 99


@pytest.mark.unit
def test_parse_alkis_feature_handles_bad_area_value() -> None:
    """Same fix applied to ``area_m2`` — bad first key, good second."""
    feat = {
        "properties": {
            "label": "P-2",
            "areaValue": "not-a-number",
            "amtlicheFlaeche": "12345.67",
        },
        "geometry": {},
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    assert out["area_m2"] == pytest.approx(12345.67)


@pytest.mark.unit
def test_parse_alkis_feature_defaults_when_all_numeric_keys_bad() -> None:
    """Every numeric candidate present but un-parseable → keep
    safe defaults (flur=0, area=0.0)."""
    feat = {
        "properties": {
            "label": "P-3",
            "flurnummer": "xx",
            "flur": "yy",
            "flurNr": "zz",
            "areaValue": "qq",
            "amtlicheFlaeche": "ww",
        },
        "geometry": {},
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    assert out["flur"] == 0
    assert out["area_m2"] == 0.0


@pytest.mark.unit
def test_parse_alkis_feature_multi_polygon_picks_largest_ring() -> None:
    """``MultiPolygon`` → take the sub-polygon with the most vertices
    in its outer ring."""
    feat = {
        "properties": {"label": "P-4"},
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [
                # 4 vertices (smaller)
                [[[9.0, 53.0], [9.01, 53.0], [9.01, 53.01], [9.0, 53.0]]],
                # 5 vertices (larger) → expected winner
                [[[9.5, 53.5], [9.5, 53.6], [9.6, 53.6], [9.6, 53.5], [9.5, 53.5]]],
            ],
        },
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    assert len(out["polygon"]) == 5


@pytest.mark.unit
def test_parse_alkis_feature_empty_multi_polygon() -> None:
    """Degenerate MultiPolygon (empty coordinates) → empty polygon,
    no crash."""
    feat = {
        "properties": {"label": "P-5"},
        "geometry": {"type": "MultiPolygon", "coordinates": []},
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    assert out["polygon"] == []


@pytest.mark.unit
def test_parse_alkis_feature_no_geometry() -> None:
    """No geometry block → empty polygon, no crash, other fields
    populate as usual."""
    feat = {"properties": {"label": "P-6"}}
    out = parse_alkis_feature(feat)
    assert out is not None
    assert out["polygon"] == []


@pytest.mark.unit
def test_parse_alkis_feature_unsupported_geom_type() -> None:
    """Unknown geometry type (Point, LineString, …) → empty polygon."""
    feat = {
        "properties": {"label": "P-7"},
        "geometry": {"type": "Point", "coordinates": [9.0, 53.0]},
    }
    out = parse_alkis_feature(feat)
    assert out is not None
    assert out["polygon"] == []


# ─────────────────────────────────────────────────────────────────────
# parse_pos_list
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_parse_pos_list_pairs_floats() -> None:
    assert parse_pos_list("53.0 9.0 53.1 9.1 53.2 9.2") == [
        [53.0, 9.0],
        [53.1, 9.1],
        [53.2, 9.2],
    ]


@pytest.mark.unit
def test_parse_pos_list_empty_and_none() -> None:
    assert parse_pos_list("") == []
    assert parse_pos_list(None) == []
    assert parse_pos_list("   ") == []


@pytest.mark.unit
def test_parse_pos_list_skips_non_numeric_tokens() -> None:
    """Defensive against malformed feeds with garbage tokens."""
    assert parse_pos_list("53.0 9.0 oops 53.1 9.1") == [
        [53.0, 9.0],
        [53.1, 9.1],
    ]


@pytest.mark.unit
def test_parse_pos_list_odd_count_drops_trailing() -> None:
    """An odd number of floats: the trailing un-pairable float is
    dropped silently. (Real WFS responses never produce this, but the
    function is defensive.)"""
    assert parse_pos_list("53.0 9.0 53.1") == [[53.0, 9.0]]


# ─────────────────────────────────────────────────────────────────────
# parse_alkis_xml
# ─────────────────────────────────────────────────────────────────────


_SAMPLE_GML = """<?xml version='1.0' encoding='UTF-8'?>
<wfs:FeatureCollection
    xmlns:wfs="http://www.opengis.net/wfs/2.0"
    xmlns:cp="http://inspire.ec.europa.eu/schemas/cp/4.0"
    xmlns:gml="http://www.opengis.net/gml/3.2">
  <wfs:member>
    <cp:CadastralParcel gml:id="x1">
      <cp:areaValue uom="m2">12345.67</cp:areaValue>
      <cp:label>12/4</cp:label>
      <cp:nationalCadastralReference>DE-NI-LAMSTEDT-0012_0004</cp:nationalCadastralReference>
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


@pytest.mark.unit
def test_parse_alkis_xml_happy_path() -> None:
    parcels = parse_alkis_xml(_SAMPLE_GML)
    assert len(parcels) == 1
    p = parcels[0]
    assert p["parcelNumber"] == "12/4"
    assert p["area_m2"] == pytest.approx(12345.67)
    assert p["polygon"][0] == [53.5, 9.0]
    assert p["source"] == "ALKIS WFS (GML)"
    assert p["nationalCadastralReference"] == "DE-NI-LAMSTEDT-0012_0004"


@pytest.mark.unit
def test_parse_alkis_xml_empty_and_none() -> None:
    assert parse_alkis_xml("") == []
    assert parse_alkis_xml(None) == []


@pytest.mark.unit
def test_parse_alkis_xml_malformed_returns_empty() -> None:
    """A malformed XML body returns ``[]`` instead of raising — lets
    the caller fall back to ``[]`` cleanly without an exception."""
    assert parse_alkis_xml("<not<valid<xml") == []


@pytest.mark.unit
def test_parse_alkis_xml_synthetic_polygon_fallback() -> None:
    """Parcel element with no geometry but a fallback point supplied:
    use the synthetic-polygon helper to give it a placeholder shape."""
    xml = (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:cp="http://inspire.ec.europa.eu/schemas/cp/4.0">'
        "<wfs:member>"
        "<cp:CadastralParcel><cp:label>13/9</cp:label></cp:CadastralParcel>"
        "</wfs:member></wfs:FeatureCollection>"
    )
    parcels = parse_alkis_xml(xml, fallback_lat=53.0, fallback_lng=9.0)
    assert len(parcels) == 1
    assert parcels[0]["parcelNumber"] == "13/9"
    assert len(parcels[0]["polygon"]) == 4  # synthetic quad


@pytest.mark.unit
def test_parse_alkis_xml_no_fallback_leaves_polygon_empty() -> None:
    """Same as above but without fallback coords: polygon stays ``[]``."""
    xml = (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:cp="http://inspire.ec.europa.eu/schemas/cp/4.0">'
        "<wfs:member>"
        "<cp:CadastralParcel><cp:label>13/9</cp:label></cp:CadastralParcel>"
        "</wfs:member></wfs:FeatureCollection>"
    )
    parcels = parse_alkis_xml(xml)
    assert parcels[0]["polygon"] == []


@pytest.mark.unit
def test_parse_alkis_xml_label_fallback_to_ncr_tail() -> None:
    """Parcel with only NCR (no label) → parcel number derived from
    the trailing segment, leading zeros stripped."""
    xml = (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:cp="http://inspire.ec.europa.eu/schemas/cp/4.0">'
        "<wfs:member>"
        "<cp:CadastralParcel>"
        "<cp:nationalCadastralReference>DE-NW-12345-000789_</cp:nationalCadastralReference>"
        "</cp:CadastralParcel>"
        "</wfs:member></wfs:FeatureCollection>"
    )
    parcels = parse_alkis_xml(xml)
    assert parcels[0]["parcelNumber"] == "789"


@pytest.mark.unit
def test_parse_alkis_xml_bad_area_defaults_to_zero() -> None:
    """Non-numeric ``areaValue`` → 0.0, no crash."""
    xml = (
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:cp="http://inspire.ec.europa.eu/schemas/cp/4.0">'
        "<wfs:member>"
        "<cp:CadastralParcel>"
        "<cp:label>X</cp:label>"
        '<cp:areaValue uom="m2">not-a-number</cp:areaValue>'
        "</cp:CadastralParcel>"
        "</wfs:member></wfs:FeatureCollection>"
    )
    parcels = parse_alkis_xml(xml)
    assert parcels[0]["area_m2"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# make_synthetic_polygon
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_make_synthetic_polygon_shape() -> None:
    """4 vertices, centred on the given point."""
    pts = make_synthetic_polygon(53.0, 9.0, area_ha=2.5)
    assert len(pts) == 4
    # The centroid of the 4 corners should be (lat, lng).
    cx_lat = sum(p[0] for p in pts) / 4
    cx_lng = sum(p[1] for p in pts) / 4
    assert cx_lat == pytest.approx(53.0, rel=1e-6)
    assert cx_lng == pytest.approx(9.0, rel=1e-6)


@pytest.mark.unit
def test_make_synthetic_polygon_scales_with_area() -> None:
    """A 10 ha polygon's edge is sqrt(4) ≈ 2× a 2.5 ha polygon's edge."""
    small = make_synthetic_polygon(53.0, 9.0, area_ha=2.5)
    big = make_synthetic_polygon(53.0, 9.0, area_ha=10.0)
    small_edge_lat = small[0][0] - small[2][0]
    big_edge_lat = big[0][0] - big[2][0]
    assert big_edge_lat == pytest.approx(small_edge_lat * 2.0, rel=0.01)
