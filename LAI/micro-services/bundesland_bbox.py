"""Bounding boxes for the 16 German Bundeslaender — used by the geocoding
plausibility gate in :func:`ddiq_report.geocode_address`.

The gate exists because Nominatim's ``limit=1`` first-match strategy can
return a same-named place in the wrong Bundesland. The wind-lawyer
smoke-test flagged a Cuxhaven address resolving to the city-state of
Bremen ~70 km south-west of the actual project site; the resulting
turbine markers landed in the wrong jurisdiction and the report's
cadastral pipeline silently used the wrong ALKIS WFS. Validating the
returned ``(lat, lng)`` against the bbox of the named Bundesland turns
that into a 'no match — request a more specific address' instead of a
plausible-looking-but-wrong report.

Data source
-----------

Values were fetched from Nominatim itself on 2026-05-17 by querying
``GET /search?q=<name>&format=json&limit=1&countrycodes=de&featuretype=state``
and reading the ``boundingbox`` field. They are the same numbers
Nominatim returns for the Bundesland's own outline polygon, so any
``(lat, lng)`` Nominatim resolves *inside* a Bundesland is by
construction inside the corresponding bbox; the gate's only job is to
reject results that fall outside.

Each tuple is ``(lat_min, lat_max, lng_min, lng_max)`` in WGS-84.
Keys match the lowercase strings returned by
:func:`ddiq_report.detect_bundesland` so callers can chain the two
without an extra normalisation table.

Note on island coverage: Niedersachsen's lat_max (54.14) includes its
North Sea islands; Schleswig-Holstein's lat_max (55.10) includes Sylt
and Föhr. These are correct — wind projects do exist on the islands.

The bbox is a *necessary* condition, not a sufficient one. A point in
Bremen's bbox might still be in Niedersachsen (Bremen is fully
enclaved). For wind-due-diligence purposes the gate is calibrated to
catch the order-of-magnitude wrong-state mistake; finer-grained
validation comes from the per-Bundesland ALKIS query downstream.
"""

from __future__ import annotations

# ``(lat_min, lat_max, lng_min, lng_max)``
BUNDESLAND_BBOX: dict[str, tuple[float, float, float, float]] = {
    "niedersachsen":          (51.2951, 54.1378,  6.3459, 11.5981),
    "nordrhein-westfalen":    (50.3227, 52.5315,  5.8663,  9.4617),
    "schleswig-holstein":     (53.3598, 55.0992,  7.5212, 11.6724),
    "brandenburg":            (51.3591, 53.5591, 11.2658, 14.7658),
    "mecklenburg-vorpommern": (53.1104, 54.8850, 10.5939, 14.4122),
    "sachsen-anhalt":         (50.9379, 53.0418, 10.5608, 13.1868),
    "bayern":                 (47.2701, 50.5647,  8.9764, 13.8396),
    "hessen":                 (49.3953, 51.6578,  7.7725, 10.2364),
    "thüringen":              (50.2043, 51.6493,  9.8770, 12.6539),
    "sachsen":                (50.1713, 51.6851, 11.8723, 15.0419),
    "rheinland-pfalz":        (48.9664, 50.9423,  6.1123,  8.5083),
    "baden-württemberg":      (47.5325, 49.7913,  7.5117, 10.4956),
    "saarland":               (49.1120, 49.6394,  6.3558,  7.4048),
    "bremen":                 (53.0112, 53.5984,  8.4816,  8.9908),
    "hamburg":                (53.3951, 54.0277,  8.1045, 10.3253),
    "berlin":                 (52.3382, 52.6755, 13.0883, 13.7612),
}


def is_in_bundesland(lat: float, lng: float, bundesland: str) -> bool:
    """Return True if ``(lat, lng)`` falls inside ``bundesland``'s bbox.

    Args:
        lat: Latitude in WGS-84 degrees.
        lng: Longitude in WGS-84 degrees.
        bundesland: Lowercase Bundesland name as returned by
            :func:`ddiq_report.detect_bundesland`. Unknown names are
            treated as "no check" — returns True so the gate doesn't
            silently reject valid points when the keyword detector
            evolves and emits a new spelling. The caller can detect
            this case via :func:`has_bbox`.

    Returns:
        ``True`` if inside (or if the Bundesland is unknown — see above);
        ``False`` if outside the bbox.
    """
    bbox = BUNDESLAND_BBOX.get(bundesland)
    if bbox is None:
        return True  # unknown Bundesland — see docstring
    lat_min, lat_max, lng_min, lng_max = bbox
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def has_bbox(bundesland: str) -> bool:
    """Return True if we have a bbox for the given Bundesland.

    Useful for callers that want to log "skipped bbox check because the
    Bundesland is unknown" rather than treat the no-bbox case as a
    silent pass.
    """
    return bundesland in BUNDESLAND_BBOX


def bundesland_from_coords(lat: float, lng: float) -> str | None:
    """Reverse-derive a Bundesland from a ``(lat, lng)`` point.

    Returns the first key in :data:`BUNDESLAND_BBOX` whose bbox contains
    the point, or ``None`` if no bbox matches.

    Caveat — bbox-based reverse derivation is approximate:

    - **Bremen** is fully enclaved within Niedersachsen; Bremen's own
      bbox is a strict subset of Niedersachsen's. Depending on iteration
      order, a point in Bremen may be reported as ``niedersachsen``.
    - **Hamburg / Berlin** are city-states whose bboxes overlap with the
      surrounding Bundesland (Schleswig-Holstein around Hamburg;
      Brandenburg around Berlin).
    - Points near a state border may technically fall inside two
      neighboring bboxes (bboxes are axis-aligned; real borders are
      polygonal).

    For wind-DD plausibility-gating these ambiguities don't matter —
    we use the result only to bbox-check Nominatim's geocode output,
    so a slightly-over-broad bbox accepts a few extra points that a
    strict polygon check would reject. That's the right failure mode:
    the gate exists to catch order-of-magnitude wrong-state results
    (Cuxhaven→Bremen, 70 km off), not to be a cadastral validator.
    """
    for key, (lat_min, lat_max, lng_min, lng_max) in BUNDESLAND_BBOX.items():
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            return key
    return None
