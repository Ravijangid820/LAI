"""Pure parsers for ALKIS INSPIRE WFS responses.

The state WFS endpoints split into two response-shape camps:

* **GeoJSON** (Niedersachsen, Schleswig-Holstein, Brandenburg, …): a
  ``FeatureCollection`` with ``features[*].properties`` +
  ``features[*].geometry``. :func:`parse_alkis_feature` handles one
  feature.
* **GML 3.2 / INSPIRE Cadastral Parcels v4.0** (NRW, Bayern, Hessen,
  …): an XML document. :func:`parse_alkis_xml` walks it.

Both produce the same flat dict shape downstream code expects:

    {
        "parcelNumber": str,
        "gemarkung": str,
        "flur": int,
        "polygon": list[[lat, lng], ...],
        "area_m2": float,
        "source": "ALKIS WFS" | "ALKIS WFS (GML)",
        # optional, present only on GML path:
        "nationalCadastralReference": str,
    }

The functions are pure — no I/O, no globals, no side effects — so they
unit-test cheaply. The HTTP fetch sits in
:class:`lai.common.connectors.alkis.AlkisClient`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 — Element type + ParseError only; all parsing uses defusedxml below
from typing import Any

from defusedxml.ElementTree import fromstring as _safe_fromstring

__all__ = [
    "ParcelDict",
    "make_synthetic_polygon",
    "parse_alkis_feature",
    "parse_alkis_xml",
    "parse_pos_list",
]

# Type alias for the flat parcel dict the rest of the system expects.
# Kept as ``dict[str, Any]`` (rather than a TypedDict) because the
# downstream consumers in DDiQ already lean on .get() / .copy() / etc.
# A future tightening can promote this to a Pydantic model without
# breaking the wire shape.
ParcelDict = dict[str, Any]


def make_synthetic_polygon(
    lat: float,
    lng: float,
    *,
    area_ha: float = 2.5,
) -> list[list[float]]:
    """Build a square placeholder polygon centred at ``(lat, lng)``.

    Used as a last-resort fallback when a GML CadastralParcel element
    contains no geometry — we'd rather show a square in the right place
    than drop the parcel silently. ``polygon_source`` upstream tags
    such polygons as ``"estimated"`` so the renderer can draw them with
    distinct styling.

    Args:
        lat: WGS-84 centre latitude.
        lng: WGS-84 centre longitude.
        area_ha: Target area in hectares. Default 2.5 ha = 25 000 m²
            ≈ a typical Flurstück.

    Returns:
        ``[[lat, lng], ...]`` closed-ring quadrilateral in WGS-84.
    """
    side_m = (area_ha * 10000) ** 0.5
    dlat = (side_m / 2) / 111000  # 1° latitude ≈ 111 km
    # Longitude scaling at ~53°N (typical German wind site) ≈ 67 km per degree.
    # Using 67 here keeps parity with the legacy DDiQ implementation.
    dlng = (side_m / 2) / 67000
    return [
        [lat + dlat, lng - dlng],
        [lat + dlat, lng + dlng],
        [lat - dlat, lng + dlng],
        [lat - dlat, lng - dlng],
    ]


def parse_pos_list(text: str | None) -> list[list[float]]:
    """Parse a GML ``<gml:posList>`` text payload to ``[[lat, lng], ...]``.

    INSPIRE Cadastral Parcels requests ``EPSG:4326`` axis order = lat,
    lng (per OGC GML 3.2 conventions for that CRS). The text is
    whitespace-separated floats; pair them up. Non-numeric tokens are
    skipped silently (very rare in practice but a defensive choice
    against malformed feeds).
    """
    if not text:
        return []
    nums: list[float] = []
    for tok in text.split():
        try:
            nums.append(float(tok))
        except ValueError:
            continue
    return [[nums[i], nums[i + 1]] for i in range(0, len(nums) - 1, 2)]


def parse_alkis_feature(feature: dict[str, Any]) -> ParcelDict | None:
    """Parse one GeoJSON feature from ALKIS INSPIRE WFS.

    Returns ``None`` when the feature has no usable parcel number —
    callers ``.append`` only non-None results.

    Robustness notes:

    * The ``flur`` and ``area_m2`` lookups try multiple candidate keys
      because state schemas disagree slightly (``flurnummer`` vs.
      ``flur`` vs. ``flurNr``; ``areaValue`` vs. ``amtlicheFlaeche`` vs.
      ``flaeche``). We accept the first key whose value *parses*, not
      the first that's merely present — a non-numeric value in the
      first candidate doesn't pin the field to 0 (the legacy
      ``pass; break`` bug from Track A item 6).
    * Geometry handling: ``Polygon`` and ``MultiPolygon`` only.
      ``MultiPolygon`` collapses to the ring with the most vertices
      (largest ring of the largest sub-polygon).
    * GeoJSON axis order is ``[lng, lat]``; we flip to ``[lat, lng]``
      for Leaflet consumption.
    """
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}

    # ── Parcel number ── first non-empty key wins (string read, no
    # parsing required).
    pnum: str | None = None
    for key in ("label", "flurstuecksnummer", "flstnrzae", "bezeichnung", "flstNr"):
        v = props.get(key)
        if v:
            pnum = str(v).strip()
            break
    # Fallback: tail of ``nationalCadastralReference`` (NRW, Bayern).
    if not pnum:
        ncr = str(props.get("nationalCadastralReference", ""))
        if ncr:
            parts = ncr.split("-")
            if len(parts) >= 3:
                pnum = re.sub(r"^0+", "", parts[-1])
                pnum = re.sub(r"/0+", "/", pnum)
    if not pnum:
        return None

    # ── Gemarkung ── first non-empty wins.
    gemarkung = ""
    for key in ("gemarkungsname", "gemarkung", "gemeinde", "municipality"):
        v = props.get(key)
        if v:
            gemarkung = str(v).strip()
            break

    # ── Flur ── break only on successful parse.
    flur = 0
    for key in ("flurnummer", "flur", "flurNr"):
        raw = props.get(key)
        if raw is None:
            continue
        try:
            flur = int(raw)
            break
        except (ValueError, TypeError):
            continue

    # ── Area ── same shape as flur.
    area_m2: float = 0.0
    for key in ("areaValue", "amtlicheFlaeche", "flaeche", "area"):
        raw = props.get(key)
        if raw is None:
            continue
        try:
            area_m2 = float(raw)
            break
        except (ValueError, TypeError):
            continue

    # ── Polygon ── GeoJSON ``[lng, lat]`` → Leaflet ``[lat, lng]``.
    polygon: list[list[float]] = []
    coords = geom.get("coordinates")
    if geom.get("type") == "Polygon" and coords:
        polygon = [[pt[1], pt[0]] for pt in coords[0]]
    elif geom.get("type") == "MultiPolygon" and coords:
        # Pick the sub-polygon with the most vertices in its outer ring.
        largest = max(coords, key=lambda p: len(p[0]) if p else 0)
        if largest:
            polygon = [[pt[1], pt[0]] for pt in largest[0]]

    return {
        "parcelNumber": pnum,
        "gemarkung": gemarkung,
        "flur": flur,
        "polygon": polygon,
        "area_m2": area_m2,
        "source": "ALKIS WFS",
    }


# GML namespaces — kept as module-level so the parser doesn't recompile
# them every call. The XML walk uses ``_local_tag`` to strip namespaces
# so this set is mostly informational.
_GML_NS = "{http://www.opengis.net/gml/3.2}"
_CP_NS = "{http://inspire.ec.europa.eu/schemas/cp/4.0}"


def _local_tag(tag: str) -> str:
    """Strip the namespace from an ElementTree tag.

    ``{http://…}CadastralParcel`` → ``CadastralParcel``. Tolerant to
    elements without a namespace.
    """
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text_of_child(parent: ET.Element, local_name: str) -> str | None:
    """First child whose local-name equals ``local_name``, returning
    its text (stripped). ``None`` when no match or text is whitespace-
    only."""
    for c in parent:
        text = c.text
        if _local_tag(c.tag) == local_name and text and text.strip():
            return text.strip()
    return None


def parse_alkis_xml(
    xml_text: str | None,
    *,
    fallback_lat: float | None = None,
    fallback_lng: float | None = None,
) -> list[ParcelDict]:
    """Parse an INSPIRE Cadastral Parcels GML/XML response.

    Walks every ``CadastralParcel`` element regardless of namespace
    prefix. For each parcel we pull:

    * ``cp:label`` → parcel number (preferred)
    * ``cp:nationalCadastralReference`` → parcel number fallback
    * ``cp:areaValue`` → area in m²
    * First descendant ``gml:posList`` → exterior ring (``EPSG:4326``,
      lat-lng axis order per INSPIRE conventions)

    Args:
        xml_text: Raw response body. Empty / unparseable → ``[]``.
        fallback_lat / fallback_lng: When provided AND a parcel
            element contains no geometry, emit a small synthetic
            polygon centred here (via :func:`make_synthetic_polygon`).
            Without these, geometry-less parcels still surface but
            with an empty polygon. The legacy DDiQ code passed the
            query point in here; we keep that contract.

    Returns:
        Same shape as :func:`parse_alkis_feature` per parcel, plus a
        ``"nationalCadastralReference"`` field copied verbatim (the
        GML feeds tend to carry rich NCRs that the renderer surfaces).

    Security note:
        ``xml.etree.ElementTree`` is used despite the documented
        XXE risks because INSPIRE WFS endpoints are government-
        operated trusted sources, the XML is fetched over HTTPS,
        and the parser doesn't resolve external DTDs by default
        (only ``defusedxml`` would add belt-and-braces protection;
        the dependency cost isn't justified for this trust model).
    """
    if not xml_text:
        return []
    parcels: list[ParcelDict] = []
    try:
        root = _safe_fromstring(xml_text)
    except ET.ParseError:
        # Malformed XML — return empty so the caller can fall back
        # cleanly. The caller logs the underlying bytes.
        return []

    for parcel_el in root.iter():
        if _local_tag(parcel_el.tag) != "CadastralParcel":
            continue

        label = _text_of_child(parcel_el, "label") or ""
        ncr = _text_of_child(parcel_el, "nationalCadastralReference") or ""
        area_text = _text_of_child(parcel_el, "areaValue")
        try:
            area_m2 = float(area_text) if area_text else 0.0
        except ValueError:
            area_m2 = 0.0

        # First descendant gml:posList is the exterior ring.
        polygon: list[list[float]] = []
        for pos_list in parcel_el.iter(_GML_NS + "posList"):
            polygon = parse_pos_list(pos_list.text or "")
            if polygon:
                break
        if not polygon and fallback_lat is not None and fallback_lng is not None:
            polygon = make_synthetic_polygon(fallback_lat, fallback_lng)

        # Parcel number — label first, otherwise tail of NCR.
        pnum = label
        if not pnum and ncr:
            tail = re.sub(r"_+$", "", ncr).split("-")[-1]
            pnum = re.sub(r"^0+", "", tail) or tail

        parcels.append(
            {
                "parcelNumber": pnum,
                "gemarkung": "",
                "flur": 0,
                "polygon": polygon,
                "area_m2": area_m2,
                "source": "ALKIS WFS (GML)",
                "nationalCadastralReference": ncr,
            }
        )

    return parcels
