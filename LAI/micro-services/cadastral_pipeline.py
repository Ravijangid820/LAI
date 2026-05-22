"""
Cadastral Pipeline Module — LAI v1
══════════════════════════════════════════════════════════════════
Implements the 13-step Output Map process for parcel classification:

  1. Define project area           →  define_project_area()
  2. Collect cadastral parcels     →  collect_cadastral_parcels()
  3. Extract relevant parcels      →  filter_relevant_parcels()
  4. Collect contract data         →  extract_contracts()
  5. Build contract dataset        →  build_contract_dataset()
  6. Match contracts to parcels    →  match_contracts_to_parcels()
  7. Identify unsecured parcels    →  identify_unsecured()
  8. Handle ambiguity              →  handle_ambiguity()
  9. Assign classifications        →  classify_all()
 10. Prepare spatial output        →  prepare_spatial_output()
 11. Define visualization rules    →  (color mapping in classify_all)
 12. Generate map dataset          →  generate_geojson()
 13. Validate results              →  validate_results()

Usage:
    from cadastral_pipeline import CadastralPipeline
    pipeline = CadastralPipeline(alkis_query_fn=..., rag_context_fn=..., ...)
    result = pipeline.run(doc_ids, full_text, wea_statuses=...)
"""

from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
import re, json, logging, math, time, uuid
from difflib import SequenceMatcher

logger = logging.getLogger("cadastral_pipeline")

# Conditional Shapely import — graceful fallback
try:
    from shapely.geometry import Polygon as ShapelyPolygon, Point as ShapelyPoint, MultiPolygon
    from shapely.ops import unary_union
    from shapely.validation import make_valid
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False
    logger.warning("Shapely not installed — spatial intersection will use bounding-box fallback")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ParcelClassification(str, Enum):
    SECURED = "secured"
    NOT_SECURED = "not_secured"
    UNCERTAIN = "uncertain"


class ProjectArea(BaseModel):
    """Step 1: The wind park boundary polygon."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    polygon: list[list[float]] = []          # [[lat, lng], ...]
    centroid_lat: float = 0.0
    centroid_lng: float = 0.0
    area_km2: float = 0.0
    source: str = ""                         # "user_drawn", "document_extracted", "wea_convex_hull"


class RawParcel(BaseModel):
    """A cadastral parcel before classification."""
    parcel_number: str
    gemarkung: str = ""
    flur: int = 0
    polygon: list[list[float]] = []          # [[lat, lng], ...]
    area_m2: float = 0.0
    source: str = ""                         # "alkis_wfs", "alkis_xml", "document_regex", "llm"
    normalized_id: str = ""                  # Canonical ID: "gemarkung:flur:nr"


class ContractRecord(BaseModel):
    """Steps 4-5: A usage contract extracted from documents."""
    contract_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contract_ref: str = ""                   # e.g., "NV-2024-001"
    contract_type: str = ""                  # Nutzungsvertrag, Pachtvertrag, etc.
    contracting_entity: str = ""
    referenced_parcels: list[str] = []       # Normalized parcel IDs
    raw_parcel_refs: list[str] = []          # Original text references
    doc_id: str = ""
    text_excerpt: str = ""


class ClassifiedParcel(BaseModel):
    """Steps 6-9: A parcel with its classification result."""
    id: str = Field(default_factory=lambda: f"cp-{uuid.uuid4().hex[:8]}")
    parcel_number: str
    gemarkung: str = ""
    flur: int = 0
    polygon: list[list[float]] = []
    polygon_source: str = "estimated"        # "alkis_wfs", "document", "estimated"
    area_m2: float = 0.0
    area_ha: float = 0.0
    normalized_id: str = ""

    # Classification
    classification: ParcelClassification = ParcelClassification.NOT_SECURED
    color: str = "red"                       # green / red / yellow
    confidence: float = 0.0                  # 0.0 - 1.0
    classification_reason: str = ""

    # Links
    matched_contract_id: Optional[str] = None
    matched_contract_ref: Optional[str] = None
    linked_wea: Optional[str] = None
    owner: str = ""
    notes: str = ""


class ClearanceZone(BaseModel):
    """Setback/clearance zone around a WEA."""
    wea_name: str
    center_lat: float
    center_lng: float
    radius_m: float = 1000.0
    polygon: list[list[float]] = []          # Circle approximation


class ValidationReport(BaseModel):
    """Step 13: Quality assurance results."""
    total_parcels_in_area: int = 0
    classified_count: int = 0
    unclassified_count: int = 0
    secured_count: int = 0
    not_secured_count: int = 0
    uncertain_count: int = 0
    coverage_ratio: float = 0.0              # secured / total
    duplicates_found: int = 0
    conflicts_found: int = 0
    geometry_errors: int = 0
    sample_checks: list[dict] = []
    passed: bool = False
    issues: list[str] = []


class PipelineResult(BaseModel):
    """Complete output of the 13-step pipeline."""
    project_area: ProjectArea
    classified_parcels: list[ClassifiedParcel] = []
    contracts: list[ContractRecord] = []
    clearance_zones: list[ClearanceZone] = []
    validation: ValidationReport = ValidationReport()
    geojson: dict = {}
    timings: dict = {}


# ═══════════════════════════════════════════════════════════════════════════════
# PARCEL ID NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_parcel_number(raw: str) -> str:
    """
    Normalize a Flurstuck number to canonical form.
    '0012/0004' -> '12/4'
    '12-4'      -> '12/4'
    '12_4'      -> '12/4'
    '120004'    -> '12/4'  (ALKIS concatenated, heuristic split)
    '12'        -> '12'    (no sub-number)
    """
    s = str(raw).strip()
    if not s:
        return ""

    # Replace common separators with /
    s = re.sub(r"[-_\\]", "/", s)

    # Handle concatenated ALKIS format (6+ digits, no separator)
    if re.match(r"^\d{5,}$", s) and "/" not in s:
        mid = len(s) // 2
        zaehler = s[:mid].lstrip("0") or "0"
        nenner = s[mid:].lstrip("0") or "0"
        if nenner != "0":
            s = f"{zaehler}/{nenner}"
        else:
            s = zaehler

    # Strip leading zeros from each part
    if "/" in s:
        parts = s.split("/", 1)
        zaehler = parts[0].lstrip("0") or "0"
        nenner = parts[1].lstrip("0") or "0"
        s = f"{zaehler}/{nenner}"
    else:
        s = s.lstrip("0") or "0"

    return s


def normalize_parcel_id(parcel_number: str, gemarkung: str = "", flur: int = 0) -> str:
    """
    Create a canonical parcel ID for matching.
    Format: "{gemarkung_lower}:{flur}:{normalized_number}"
    e.g., "hude:3:12/4"
    """
    norm_num = normalize_parcel_number(parcel_number)
    gem = gemarkung.strip().lower() if gemarkung else ""
    gem = re.sub(r"^(gemeinde|stadt|markt|gemarkung)\s+", "", gem)
    return f"{gem}:{flur}:{norm_num}" if gem else f":{flur}:{norm_num}"


def fuzzy_match_parcel_id(id1: str, id2: str) -> float:
    """
    Fuzzy match two normalized parcel IDs.
    Returns similarity score 0.0-1.0.
    """
    if id1 == id2:
        return 1.0

    parts1 = id1.split(":")
    parts2 = id2.split(":")
    if len(parts1) == 3 and len(parts2) == 3:
        num1, num2 = parts1[2], parts2[2]
        flur1, flur2 = parts1[1], parts2[1]

        # Same number, same Flur, different Gemarkung -> likely match
        if num1 == num2 and flur1 == flur2:
            if not parts1[0] or not parts2[0]:
                return 0.9  # One side missing Gemarkung
            gem_sim = SequenceMatcher(None, parts1[0], parts2[0]).ratio()
            return 0.7 + 0.3 * gem_sim

        # Same number, different Flur -> lower confidence
        if num1 == num2:
            return 0.5

    return SequenceMatcher(None, id1, id2).ratio()


# ═══════════════════════════════════════════════════════════════════════════════
# SPATIAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def polygon_to_shapely(coords: list[list[float]]):
    """Convert [[lat,lng], ...] to Shapely Polygon."""
    if not SHAPELY_AVAILABLE or not coords or len(coords) < 3:
        return None
    try:
        ring = [(pt[1], pt[0]) for pt in coords]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        poly = ShapelyPolygon(ring)
        if not poly.is_valid:
            poly = make_valid(poly)
            if isinstance(poly, MultiPolygon):
                poly = max(poly.geoms, key=lambda g: g.area)
        return poly
    except Exception as e:
        logger.warning(f"Shapely polygon conversion failed: {e}")
        return None


def compute_centroid(polygon: list[list[float]]) -> tuple[float, float]:
    """Compute centroid of a [[lat,lng], ...] polygon."""
    if not polygon:
        return (0.0, 0.0)
    lats = [p[0] for p in polygon]
    lngs = [p[1] for p in polygon]
    return (sum(lats) / len(lats), sum(lngs) / len(lngs))


def polygon_area_km2(polygon: list[list[float]]) -> float:
    """Approximate area in km2 using Shapely or Shoelace formula."""
    if not polygon or len(polygon) < 3:
        return 0.0
    sp = polygon_to_shapely(polygon)
    if sp:
        centroid = sp.centroid
        lat_scale = 111.0
        lng_scale = 111.0 * math.cos(math.radians(centroid.y))
        scaled = [(x * lng_scale, y * lat_scale) for x, y in sp.exterior.coords]
        scaled_poly = ShapelyPolygon(scaled)
        return round(scaled_poly.area, 4)
    return 0.0


def convex_hull_from_points(points: list[tuple[float, float]], buffer_deg: float = 0.005) -> list[list[float]]:
    """
    Create a convex hull polygon from (lat, lng) points with buffer.
    buffer_deg ~ 0.005 deg ~ 500m
    """
    if not points:
        return []

    if SHAPELY_AVAILABLE and len(points) >= 3:
        shapely_points = [ShapelyPoint(lng, lat) for lat, lng in points]
        hull = unary_union(shapely_points).convex_hull
        if buffer_deg > 0:
            hull = hull.buffer(buffer_deg)
        coords = list(hull.exterior.coords)
        return [[pt[1], pt[0]] for pt in coords]

    # Fallback: bounding box
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    min_lat, max_lat = min(lats) - buffer_deg, max(lats) + buffer_deg
    min_lng, max_lng = min(lngs) - buffer_deg, max(lngs) + buffer_deg
    return [
        [max_lat, min_lng], [max_lat, max_lng],
        [min_lat, max_lng], [min_lat, min_lng],
    ]


# A real wind park spans a few km; a turbine that geocodes tens of km from the
# cluster is a geocode error (e.g. the applicant's HQ address resolving to a
# different town), not a real site. Left unchecked, one such outlier inflates
# the convex-hull project area to hundreds of km² → a 300 m ALKIS sampling grid
# explodes to tens of thousands of points (the "35,627 points / 1321 km²" jam).
_MAX_WEA_SPREAD_KM = 25.0
# Hard ceiling on ALKIS sampling points regardless of area — a safety net so a
# bad area can never explode the grid.
_MAX_ALKIS_GRID_POINTS = 400
# Stop hammering ALKIS once it's clearly down (e.g. HTTP 530) rather than
# grinding every grid point × its internal retries. ``CONSECUTIVE`` catches a
# hard outage fast; ``TOTAL`` is a defence-in-depth budget for a FLAKY WFS that
# alternates success/failure (which would keep resetting the consecutive
# counter). Either tripping aborts the lookup → estimated parcels downstream.
_MAX_ALKIS_CONSECUTIVE_FAILURES = 8
_MAX_ALKIS_TOTAL_FAILURES = 20


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two (lat, lng) points."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def filter_outlier_points(
    points: list[tuple[float, float]], max_km: float = _MAX_WEA_SPREAD_KM
) -> list[tuple[float, float]]:
    """Drop points more than ``max_km`` from the cluster centre (the median
    lat/lng, which is robust to outliers). Keeps a single mis-geocoded turbine
    from inflating the project area. With ≤2 points there's no cluster to judge
    against, so they're returned unchanged."""
    if len(points) <= 2:
        return points
    med_lat = sorted(p[0] for p in points)[len(points) // 2]
    med_lng = sorted(p[1] for p in points)[len(points) // 2]
    kept = [p for p in points if _haversine_km(p[0], p[1], med_lat, med_lng) <= max_km]
    return kept if kept else points


def make_circle_polygon(lat: float, lng: float, radius_m: float, num_points: int = 64) -> list[list[float]]:
    """Create a circle approximation as a polygon."""
    points = []
    for i in range(num_points):
        angle = (2 * math.pi * i) / num_points
        dlat = (radius_m * math.cos(angle)) / 111000
        dlng = (radius_m * math.sin(angle)) / (111000 * math.cos(math.radians(lat)))
        points.append([lat + dlat, lng + dlng])
    points.append(points[0])
    return points


def polygons_intersect(poly1: list[list[float]], poly2: list[list[float]]) -> bool:
    """Check if two polygons intersect."""
    if SHAPELY_AVAILABLE:
        sp1 = polygon_to_shapely(poly1)
        sp2 = polygon_to_shapely(poly2)
        if sp1 and sp2:
            return sp1.intersects(sp2)
    # Fallback: bounding box overlap
    if not poly1 or not poly2:
        return False
    lats1, lngs1 = [p[0] for p in poly1], [p[1] for p in poly1]
    lats2, lngs2 = [p[0] for p in poly2], [p[1] for p in poly2]
    return not (max(lats1) < min(lats2) or max(lats2) < min(lats1) or
                max(lngs1) < min(lngs2) or max(lngs2) < min(lngs1))


def polygon_contains_point(polygon: list[list[float]], lat: float, lng: float) -> bool:
    """Check if a point is inside a polygon."""
    if SHAPELY_AVAILABLE:
        sp = polygon_to_shapely(polygon)
        if sp:
            return sp.contains(ShapelyPoint(lng, lat))
    # Fallback: bounding box
    if not polygon:
        return False
    lats = [p[0] for p in polygon]
    lngs = [p[1] for p in polygon]
    return min(lats) <= lat <= max(lats) and min(lngs) <= lng <= max(lngs)


# ═══════════════════════════════════════════════════════════════════════════════
# ENHANCED REGEX EXTRACTORS
# ═══════════════════════════════════════════════════════════════════════════════

PARCEL_PATTERNS = [
    # "Flurstuck 12/4 ... Gemarkung Hude ... Flur 3" (same paragraph only)
    re.compile(
        "(?:Flurst[uü]ck|Grundst[uü]ck|Parzelle)[s]?\\s*(?:Nr\\.?\\s*)?(\\d+[/\\-]\\d+)"
        "(?:[^\\n]{0,100}?Gemarkung\\s+([A-ZÄÖÜa-zäöüß\\-]+))?"
        "(?:[^\\n]{0,100}?Flur\\s+(\\d+))?",
        re.IGNORECASE
    ),
    # "Flur 3 Nr. 12/4"
    re.compile(
        "Flur\\s+(\\d+)\\s*(?:,\\s*)?(?:Nr\\.?\\s*|Flurst[uü]ck\\s*)(\\d+[/\\-]\\d+)"
        "(?:[^\\n]{0,100}?Gemarkung\\s+([A-ZÄÖÜa-zäöüß\\-]+))?",
        re.IGNORECASE
    ),
    # "Gemarkung Hude, Flur 3, Flurstuck 12/4"
    re.compile(
        "Gemarkung\\s+([A-ZÄÖÜa-zäöüß\\-]+)\\s*,?\\s*Flur\\s+(\\d+)\\s*,?\\s*"
        "(?:Flurst[uü]ck|Nr\\.?)\\s*(\\d+[/\\-]\\d+)",
        re.IGNORECASE
    ),
]

CONTRACT_PATTERNS = [
    re.compile(
        "(?:Nutzungsvertrag|Pachtvertrag|Gestattungsvertrag|Dienstbarkeitsvertrag|Vertrag)"
        "[s\\-]*\\s*(?:Nr\\.?\\s*|nummer\\s*)?([A-Z0-9][\\w\\-]*\\d+)",
        re.IGNORECASE
    ),
    re.compile(
        "(?:Vertrag|Vereinbarung)\\s+(?:vom|dated)\\s+(\\d{1,2}[./]\\d{1,2}[./]\\d{2,4})",
        re.IGNORECASE
    ),
]

CONTRACT_TYPE_PATTERNS = {
    "Nutzungsvertrag": re.compile("Nutzungsvertrag|Nutzungsvereinbarung|usage\\s+agreement", re.IGNORECASE),
    "Pachtvertrag": re.compile("Pachtvertrag|Pachtvereinbarung|lease\\s+agreement", re.IGNORECASE),
    "Gestattungsvertrag": re.compile("Gestattungsvertrag|Gestattung|easement", re.IGNORECASE),
    "Dienstbarkeit": re.compile("Dienstbarkeit|Grunddienstbarkeit|servitude", re.IGNORECASE),
    "Kaufvertrag": re.compile("Kaufvertrag|purchase\\s+agreement", re.IGNORECASE),
}


def extract_parcel_refs_enhanced(text: str) -> list[dict]:
    """Extract parcel references from text using multiple patterns."""
    found = []
    seen = set()

    for pattern_idx, pattern in enumerate(PARCEL_PATTERNS):
        for m in pattern.finditer(text):
            if pattern_idx == 0:
                num = normalize_parcel_number(m.group(1))
                gem = m.group(2) or ""
                flur = int(m.group(3)) if m.group(3) else 0
            elif pattern_idx == 1:
                num = normalize_parcel_number(m.group(2))
                gem = m.group(3) or ""
                flur = int(m.group(1))
            else:
                num = normalize_parcel_number(m.group(3))
                gem = m.group(1) or ""
                flur = int(m.group(2))

            if num in seen:
                continue
            seen.add(num)
            found.append({
                "parcel_number": num,
                "gemarkung": gem,
                "flur": flur,
                "raw": m.group(0)[:100],
                "position": m.start(),
            })

    return found


def extract_contract_refs(text: str) -> list[dict]:
    """Extract contract references and their nearby parcel mentions."""
    contracts = []
    for pattern in CONTRACT_PATTERNS:
        for m in pattern.finditer(text):
            context_start = max(0, m.start() - 200)
            context_end = min(len(text), m.end() + 200)
            context = text[context_start:context_end]

            contract_type = "Unknown"
            for ctype, cpattern in CONTRACT_TYPE_PATTERNS.items():
                if cpattern.search(context):
                    contract_type = ctype
                    break

            nearby_parcels = []
            search_window = text[max(0, m.start() - 2000):min(len(text), m.end() + 2000)]
            for pref in extract_parcel_refs_enhanced(search_window):
                nearby_parcels.append(pref["parcel_number"])

            contracts.append({
                "contract_ref": m.group(1) if m.lastindex else m.group(0),
                "contract_type": contract_type,
                "position": m.start(),
                "raw": m.group(0)[:100],
                "nearby_parcels": nearby_parcels,
                "context": context,
            })

    return contracts


# ═══════════════════════════════════════════════════════════════════════════════
# CLEARANCE ZONES
# ═══════════════════════════════════════════════════════════════════════════════

# Default fallback radii used when neither hub-height nor 10H apply.
# These are coarse Bundesland defaults — only meaningful when the LLM
# couldn't extract a hub height for the WEA.
CLEARANCE_DEFAULTS = {
    "bayern": 2000,
    "nordrhein-westfalen": 1000,
    "niedersachsen": 1000,
    "schleswig-holstein": 1000,
    "brandenburg": 1000,
    "mecklenburg-vorpommern": 1000,
    "sachsen-anhalt": 1000,
    "hessen": 1000,
    "thueringen": 1000,
    "sachsen": 1000,
    "rheinland-pfalz": 1000,
    "baden-wuerttemberg": 1000,
    "saarland": 800,
    "bremen": 500,
    "hamburg": 500,
    "berlin": 500,
}

# Bundesländer where the 10H rule (10× total height to the nearest residential
# building) is the binding minimum distance to Wohnbebauung. Bayern (BayBO Art. 82)
# is the canonical case; Hessen has a soft 10H equivalent under §249 Abs. 6 BauGB
# in some districts. Other states use absolute setbacks per Regional/Landesplanung
# (no fixed federal rule).
TEN_H_BUNDESLAENDER = {"bayern", "hessen"}


def clearance_radius_for_wea(wea, bundesland: str = "") -> tuple[float, str]:
    """Return (radius_m, source) for a WEA's clearance zone.

    Picks the binding radius by priority:
      1. 10H × (hub_height + ½·rotor) for Bayern/Hessen if hub data is present
      2. CLEARANCE_DEFAULTS[bundesland] otherwise
      3. 1000m flat fallback

    The lawyer-relevant case is #1 — without it a 200m turbine in Bayern
    would show 2000m clearance regardless of actual height, which can
    misrepresent compliance with BayBO Art. 82."""
    hub = getattr(wea, "hub_height_m", None)
    rotor = getattr(wea, "rotor_diameter_m", None)
    bl = (bundesland or "").lower()

    if hub and bl in TEN_H_BUNDESLAENDER:
        # Total-height per BayBO Art. 82 = Nabenhöhe + Rotorradius. If rotor
        # is unknown fall back to hub-only — still tighter than the flat
        # 2000m default and at least directionally correct.
        total_h = float(hub) + (float(rotor) / 2.0 if rotor else 0.0)
        radius = max(10.0 * total_h, 500.0)  # 500m floor for tiny test data
        return (round(radius, 1), f"10H rule (BayBO Art. 82) · hub {hub}m + rotor/2 {round((rotor or 0)/2,1)}m")

    if bl in CLEARANCE_DEFAULTS:
        return (float(CLEARANCE_DEFAULTS[bl]), f"Bundesland default ({bl})")

    return (1000.0, "fallback (no Bundesland match)")


def build_clearance_zones(wea_statuses: list, bundesland: str = "") -> list[ClearanceZone]:
    """Build clearance zone circles for each WEA. Each zone uses the binding
    radius for that turbine — hub-height-aware 10H for Bayern/Hessen,
    Bundesland default otherwise. The radius rationale is propagated so
    the frontend can display 'why this circle is this big'."""
    zones = []
    for wea in wea_statuses:
        if wea.lat == 0 and wea.lng == 0:
            continue
        radius, source = clearance_radius_for_wea(wea, bundesland)
        circle = make_circle_polygon(wea.lat, wea.lng, radius)
        # Annotate the WEA itself so generate_findings/UI can show the
        # binding rule per turbine.
        try:
            wea.clearance_radius_m = radius
        except Exception:
            pass
        zones.append(ClearanceZone(
            wea_name=wea.name,
            center_lat=wea.lat,
            center_lng=wea.lng,
            radius_m=radius,
            polygon=circle,
        ))
        # Stash on the zone object for downstream display via dict access.
        try:
            zones[-1].__dict__["_radius_source"] = source
        except Exception:
            pass
    return zones


# ═══════════════════════════════════════════════════════════════════════════════
# GEOJSON OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def _to_geojson_coords(latlng_polygon: list[list[float]]) -> list[list[list[float]]]:
    """Convert [[lat,lng], ...] to GeoJSON [[[lng,lat], ...]] format."""
    if not latlng_polygon:
        return []
    ring = [[pt[1], pt[0]] for pt in latlng_polygon]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return [ring]


def generate_geojson(
    classified_parcels: list[ClassifiedParcel],
    project_area: ProjectArea = None,
    wea_statuses: list = None,
    clearance_zones: list[ClearanceZone] = None,
) -> dict:
    """
    Generate a standard GeoJSON FeatureCollection (Step 12).
    Layers: parcels, project_area, wea_points, clearance_zones.
    CRS: EPSG:4326 (WGS84).
    """
    features = []

    # Layer 1: Classified Parcels
    for parcel in classified_parcels:
        if not parcel.polygon:
            continue
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": _to_geojson_coords(parcel.polygon),
            },
            "properties": {
                "layer": "parcels",
                "id": parcel.id,
                "parcelNumber": parcel.parcel_number,
                "gemarkung": parcel.gemarkung,
                "flur": parcel.flur,
                "normalizedId": parcel.normalized_id,
                "classification": parcel.classification.value,
                "color": parcel.color,
                "confidence": parcel.confidence,
                "reason": parcel.classification_reason,
                "area_ha": parcel.area_ha,
                "polygonSource": parcel.polygon_source,
                "matchedContract": parcel.matched_contract_ref,
                "linkedWEA": parcel.linked_wea,
                "owner": parcel.owner,
                "notes": parcel.notes,
            },
        })

    # Layer 2: Project Area Boundary
    if project_area and project_area.polygon:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": _to_geojson_coords(project_area.polygon),
            },
            "properties": {
                "layer": "project_area",
                "name": project_area.name,
                "area_km2": project_area.area_km2,
                "source": project_area.source,
                "color": "#2E75B6",
                "fillOpacity": 0.05,
                "strokeWidth": 3,
                "strokeDash": "10,5",
            },
        })

    # Layer 3: WEA Points
    if wea_statuses:
        for wea in wea_statuses:
            if wea.lat == 0 and wea.lng == 0:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [wea.lng, wea.lat],
                },
                "properties": {
                    "layer": "wea",
                    "name": wea.name,
                    "owner": wea.owner,
                    "ampel": wea.ampel,
                    "parcel": wea.parcel,
                    "contract": wea.contract,
                    "address": wea.address,
                    "icon": "wind_turbine",
                },
            })

    # Layer 4: Clearance Zones
    if clearance_zones:
        for zone in clearance_zones:
            if not zone.polygon:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": _to_geojson_coords(zone.polygon),
                },
                "properties": {
                    "layer": "clearance_zones",
                    "wea_name": zone.wea_name,
                    "radius_m": zone.radius_m,
                    "color": "#FF6B6B",
                    "fillOpacity": 0.08,
                    "strokeWidth": 1,
                    "strokeDash": "5,3",
                },
            })

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "crs": "EPSG:4326",
            "generated_by": "LAI Cadastral Pipeline v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_features": len(features),
            "layers": {
                "parcels": sum(1 for f in features if f["properties"].get("layer") == "parcels"),
                "wea": sum(1 for f in features if f["properties"].get("layer") == "wea"),
                "clearance_zones": sum(1 for f in features if f["properties"].get("layer") == "clearance_zones"),
                "project_area": 1 if project_area and project_area.polygon else 0,
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION (Step 13)
# ═══════════════════════════════════════════════════════════════════════════════

GERMANY_BBOX = (47.0, 5.5, 55.1, 15.1)


def validate_results(
    classified: list[ClassifiedParcel],
    contracts: list[ContractRecord],
    project_area: ProjectArea = None,
    full_text: str = "",
) -> ValidationReport:
    """Step 13: Validate the pipeline output."""
    issues = []
    report = ValidationReport()

    report.total_parcels_in_area = len(classified)
    report.classified_count = len(classified)

    report.secured_count = sum(1 for p in classified if p.classification == ParcelClassification.SECURED)
    report.not_secured_count = sum(1 for p in classified if p.classification == ParcelClassification.NOT_SECURED)
    report.uncertain_count = sum(1 for p in classified if p.classification == ParcelClassification.UNCERTAIN)
    report.unclassified_count = report.total_parcels_in_area - (
        report.secured_count + report.not_secured_count + report.uncertain_count
    )

    if report.total_parcels_in_area > 0:
        report.coverage_ratio = round(report.secured_count / report.total_parcels_in_area, 4)

    # Check 1: Completeness
    if report.unclassified_count > 0:
        issues.append(f"{report.unclassified_count} parcels have no classification assigned")

    # Check 2: Uniqueness
    ids = [p.normalized_id for p in classified if p.normalized_id]
    unique_ids = set(ids)
    report.duplicates_found = len(ids) - len(unique_ids)
    if report.duplicates_found > 0:
        dupes = [nid for nid in unique_ids if ids.count(nid) > 1]
        issues.append(f"{report.duplicates_found} duplicate parcel(s): {', '.join(dupes[:5])}")

    # Check 3: No conflicts
    id_to_cls = {}
    for p in classified:
        if p.normalized_id:
            id_to_cls.setdefault(p.normalized_id, set()).add(p.classification)
    conflicts = {nid: cls for nid, cls in id_to_cls.items() if len(cls) > 1}
    report.conflicts_found = len(conflicts)
    if conflicts:
        issues.append(f"{len(conflicts)} parcel(s) have conflicting classifications")

    # Check 4: Geometry validation
    geo_errors = 0
    for p in classified:
        if not p.polygon:
            geo_errors += 1
            continue
        for pt in p.polygon:
            if len(pt) >= 2:
                lat, lng = pt[0], pt[1]
                if not (GERMANY_BBOX[0] <= lat <= GERMANY_BBOX[2] and
                        GERMANY_BBOX[1] <= lng <= GERMANY_BBOX[3]):
                    geo_errors += 1
                    break
    report.geometry_errors = geo_errors
    if geo_errors > 0:
        issues.append(f"{geo_errors} parcel(s) have geometry issues")

    # Check 5: Random sample verification
    secured_parcels = [
        p for p in classified
        if p.classification == ParcelClassification.SECURED and p.matched_contract_ref
    ]
    sample_size = min(len(secured_parcels), 10)
    if sample_size > 0 and full_text:
        import random
        sample = random.sample(secured_parcels, sample_size)
        for sp in sample:
            p_pos = full_text.lower().find(sp.parcel_number.lower())
            c_pos = full_text.lower().find(sp.matched_contract_ref.lower()) if sp.matched_contract_ref else -1
            verified = p_pos >= 0 and c_pos >= 0 and abs(p_pos - c_pos) < 3000
            report.sample_checks.append({
                "parcel_id": sp.parcel_number,
                "contract_ref": sp.matched_contract_ref,
                "verified": verified,
            })
        failed = sum(1 for sc in report.sample_checks if not sc["verified"])
        if failed > 0:
            issues.append(f"{failed}/{sample_size} sample checks failed verification")

    # Check 6: Coverage warning
    if report.coverage_ratio < 0.3 and report.total_parcels_in_area > 5:
        issues.append(f"Low coverage ratio ({report.coverage_ratio:.0%}): most parcels are unsecured")

    report.issues = issues
    report.passed = len(issues) == 0
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class CadastralPipeline:
    """
    Orchestrates the 13-step cadastral classification pipeline.

    Dependencies are injected for testability:
      - alkis_query_fn:       (lat, lng, bundesland, radius) -> list[dict]
      - rag_context_fn:       (doc_ids, question, top_k) -> str
      - llm_json_fn:          (system, user) -> dict/list
      - detect_bundesland_fn: (location) -> str | None
    """

    def __init__(
        self,
        alkis_query_fn=None,
        rag_context_fn=None,
        llm_json_fn=None,
        detect_bundesland_fn=None,
    ):
        self.alkis_query = alkis_query_fn
        self.rag_context = rag_context_fn
        self.llm_json = llm_json_fn
        self.detect_bundesland = detect_bundesland_fn

    def run(
        self,
        doc_ids: list[str],
        full_text: str,
        wea_statuses: list = None,
        project_area_polygon: list[list[float]] = None,
        location: str = "",
        project_center: tuple[float, float] = None,
    ) -> PipelineResult:
        """Execute the full 13-step pipeline."""
        timings = {}
        wea_statuses = wea_statuses or []

        # Step 1: Define Project Area
        t = time.time()
        project_area = self._step1_define_project_area(
            doc_ids, full_text, project_area_polygon, wea_statuses, location, project_center
        )
        timings["step1_project_area_s"] = round(time.time() - t, 2)
        logger.info(f"Step 1: Project area defined via {project_area.source}, area={project_area.area_km2} km2")

        # Step 2: Collect Cadastral Parcels
        t = time.time()
        bundesland = self.detect_bundesland(location) if self.detect_bundesland and location else None
        raw_parcels = self._step2_collect_parcels(project_area, bundesland, wea_statuses)
        timings["step2_collect_parcels_s"] = round(time.time() - t, 2)
        logger.info(f"Step 2: Collected {len(raw_parcels)} raw parcels")

        # Step 3: Filter Relevant Parcels
        t = time.time()
        relevant_parcels = self._step3_filter_relevant(raw_parcels, project_area)
        timings["step3_filter_s"] = round(time.time() - t, 2)
        logger.info(f"Step 3: {len(relevant_parcels)} parcels within project area")

        # Steps 4-5: Extract and Build Contract Dataset
        t = time.time()
        contracts = self._step4_5_extract_contracts(doc_ids, full_text)
        timings["step4_5_contracts_s"] = round(time.time() - t, 2)
        total_refs = sum(len(c.referenced_parcels) for c in contracts)
        logger.info(f"Steps 4-5: {len(contracts)} contracts, {total_refs} parcel references")

        # Step 6: Match Contracts to Parcels
        t = time.time()
        secured, fuzzy_matches = self._step6_match(relevant_parcels, contracts, wea_statuses)
        timings["step6_match_s"] = round(time.time() - t, 2)
        logger.info(f"Step 6: {len(secured)} exact, {len(fuzzy_matches)} fuzzy matches")

        # Step 7: Identify Unsecured
        t = time.time()
        secured_ids = {p.normalized_id for p in secured}
        fuzzy_ids = {p.normalized_id for p in fuzzy_matches}
        unsecured = self._step7_unsecured(relevant_parcels, secured_ids, fuzzy_ids)
        timings["step7_unsecured_s"] = round(time.time() - t, 2)
        logger.info(f"Step 7: {len(unsecured)} unsecured parcels")

        # Step 8: Handle Ambiguity
        t = time.time()
        uncertain = self._step8_ambiguity(fuzzy_matches)
        timings["step8_ambiguity_s"] = round(time.time() - t, 2)
        logger.info(f"Step 8: {len(uncertain)} uncertain parcels")

        # Steps 9-11: Classify All + Colors
        t = time.time()
        classified = self._step9_11_classify(secured, unsecured, uncertain)
        timings["step9_11_classify_s"] = round(time.time() - t, 2)
        green = sum(1 for p in classified if p.color == "green")
        red = sum(1 for p in classified if p.color == "red")
        yellow = sum(1 for p in classified if p.color == "yellow")
        logger.info(f"Steps 9-11: {len(classified)} total — green={green}, red={red}, yellow={yellow}")

        # Clearance Zones
        clearance_zones = build_clearance_zones(wea_statuses, bundesland or "")

        # Step 12: Generate GeoJSON
        t = time.time()
        geojson = generate_geojson(classified, project_area, wea_statuses, clearance_zones)
        timings["step12_geojson_s"] = round(time.time() - t, 2)

        # Step 13: Validate
        t = time.time()
        validation = validate_results(classified, contracts, project_area, full_text)
        timings["step13_validate_s"] = round(time.time() - t, 2)
        logger.info(f"Step 13: Validation {'PASSED' if validation.passed else 'FAILED'} ({len(validation.issues)} issues)")

        return PipelineResult(
            project_area=project_area,
            classified_parcels=classified,
            contracts=contracts,
            clearance_zones=clearance_zones,
            validation=validation,
            geojson=geojson,
            timings=timings,
        )

    # ── Step 1 ────────────────────────────────────────────────────────────────
    def _step1_define_project_area(
        self, doc_ids, full_text, user_polygon, wea_statuses, location, project_center
    ) -> ProjectArea:
        # Option A: User-provided polygon
        if user_polygon and len(user_polygon) >= 3:
            centroid = compute_centroid(user_polygon)
            return ProjectArea(
                name="User-Defined Area",
                polygon=user_polygon,
                centroid_lat=centroid[0],
                centroid_lng=centroid[1],
                area_km2=polygon_area_km2(user_polygon),
                source="user_drawn",
            )

        # Option B: Convex hull from WEA locations + buffer
        all_wea_points = [(w.lat, w.lng) for w in wea_statuses if w.lat != 0 and w.lng != 0]
        wea_points = filter_outlier_points(all_wea_points)
        if len(wea_points) < len(all_wea_points):
            logger.warning(
                "Step 1: dropped %d/%d WEA point(s) >%.0f km from the cluster "
                "(geocode outliers) before computing project area",
                len(all_wea_points) - len(wea_points), len(all_wea_points), _MAX_WEA_SPREAD_KM,
            )
        if wea_points:
            hull = convex_hull_from_points(wea_points, buffer_deg=0.005)
            centroid = compute_centroid(hull)
            return ProjectArea(
                name=f"Auto-generated from {len(wea_points)} WEA locations",
                polygon=hull,
                centroid_lat=centroid[0],
                centroid_lng=centroid[1],
                area_km2=polygon_area_km2(hull),
                source="wea_convex_hull",
            )

        # Option C: Circle from project center
        if project_center:
            lat, lng = project_center
            circle = make_circle_polygon(lat, lng, 2000, 32)
            return ProjectArea(
                name="Auto-generated from project center",
                polygon=circle,
                centroid_lat=lat,
                centroid_lng=lng,
                area_km2=polygon_area_km2(circle),
                source="center_buffer",
            )

        return ProjectArea(name="Unknown Area", source="none")

    # ── Step 2 ────────────────────────────────────────────────────────────────
    def _step2_collect_parcels(
        self, project_area: ProjectArea, bundesland: str, wea_statuses: list
    ) -> list[RawParcel]:
        parcels = []
        seen = set()

        if not self.alkis_query or not bundesland:
            return parcels

        query_points = []

        # WEA coordinates
        for wea in wea_statuses:
            if wea.lat != 0 and wea.lng != 0:
                query_points.append((wea.lat, wea.lng))

        # Grid points covering project area bounding box. Base resolution is
        # 300 m, but if the area is large enough that a 300 m mesh would exceed
        # the hard cap, coarsen the step so the grid stays bounded — a big area
        # must never explode into tens of thousands of ALKIS calls.
        if project_area.polygon:
            lats = [p[0] for p in project_area.polygon]
            lngs = [p[1] for p in project_area.polygon]
            min_lat, max_lat = min(lats), max(lats)
            min_lng, max_lng = min(lngs), max(lngs)
            lng_scale = math.cos(math.radians((min_lat + max_lat) / 2))
            lat_step = 300 / 111000
            lng_step = 300 / (111000 * lng_scale) if lng_scale > 0 else 0.003

            n_lat = max(1, int((max_lat - min_lat) / lat_step) + 1)
            n_lng = max(1, int((max_lng - min_lng) / lng_step) + 1)
            if n_lat * n_lng > _MAX_ALKIS_GRID_POINTS:
                factor = math.sqrt((n_lat * n_lng) / _MAX_ALKIS_GRID_POINTS)
                lat_step *= factor
                lng_step *= factor
                logger.warning(
                    "Step 2: project area is %.0f km² — coarsening ALKIS grid "
                    "from ~%d to ~%d points",
                    project_area.area_km2, n_lat * n_lng, _MAX_ALKIS_GRID_POINTS,
                )

            lat = min_lat
            while lat <= max_lat:
                lng = min_lng
                while lng <= max_lng:
                    query_points.append((lat, lng))
                    lng += lng_step
                lat += lat_step

        # Deduplicate query points (~50m resolution)
        unique_points = []
        seen_points = set()
        for lat, lng in query_points:
            key = f"{lat:.4f},{lng:.4f}"
            if key not in seen_points:
                seen_points.add(key)
                unique_points.append((lat, lng))

        logger.info(f"Step 2: Querying ALKIS at {len(unique_points)} points ({bundesland})")

        consecutive_failures = 0
        total_failures = 0
        for lat, lng in unique_points:
            try:
                alkis_results = self.alkis_query(lat, lng, bundesland, 200)
                consecutive_failures = 0
                for ap in alkis_results:
                    pnum = normalize_parcel_number(ap.get("parcelNumber", ""))
                    if not pnum or pnum in seen:
                        continue
                    seen.add(pnum)
                    norm_id = normalize_parcel_id(pnum, ap.get("gemarkung", ""), ap.get("flur", 0))
                    parcels.append(RawParcel(
                        parcel_number=pnum,
                        gemarkung=ap.get("gemarkung", ""),
                        flur=ap.get("flur", 0),
                        polygon=ap.get("polygon", []),
                        area_m2=ap.get("area_m2", 0),
                        source=ap.get("source", "alkis_wfs"),
                        normalized_id=norm_id,
                    ))
                time.sleep(0.3)
            except Exception as e:
                consecutive_failures += 1
                total_failures += 1
                logger.warning(f"ALKIS query at ({lat:.5f},{lng:.5f}) failed: {e}")
                # ALKIS clearly unreachable (e.g. HTTP 530) or persistently
                # flaky — stop rather than grinding every remaining grid point ×
                # its internal retries. The pipeline falls back to estimated
                # parcels downstream.
                if (consecutive_failures >= _MAX_ALKIS_CONSECUTIVE_FAILURES
                        or total_failures >= _MAX_ALKIS_TOTAL_FAILURES):
                    logger.warning(
                        "Step 2: ALKIS unreachable/flaky (%d consecutive, %d "
                        "total failures) — aborting cadastral lookup after %d "
                        "collected parcel(s)",
                        consecutive_failures, total_failures, len(parcels),
                    )
                    break

        return parcels

    # ── Step 3 ────────────────────────────────────────────────────────────────
    def _step3_filter_relevant(
        self, raw_parcels: list[RawParcel], project_area: ProjectArea
    ) -> list[RawParcel]:
        if not project_area.polygon or not raw_parcels:
            return raw_parcels

        relevant = []
        for parcel in raw_parcels:
            if parcel.polygon:
                if polygons_intersect(project_area.polygon, parcel.polygon):
                    relevant.append(parcel)
            else:
                relevant.append(parcel)  # No geometry — keep it
        return relevant

    # ── Steps 4-5 ─────────────────────────────────────────────────────────────
    def _step4_5_extract_contracts(
        self, doc_ids: list[str], full_text: str
    ) -> list[ContractRecord]:
        contracts = []
        seen_refs = set()

        # Layer 1: Regex
        raw_contracts = extract_contract_refs(full_text)
        for rc in raw_contracts:
            ref = rc["contract_ref"]
            if ref in seen_refs:
                continue
            seen_refs.add(ref)

            normalized_parcels = []
            raw_parcel_refs = []
            for pnum in rc.get("nearby_parcels", []):
                norm = normalize_parcel_id(pnum)
                normalized_parcels.append(norm)
                raw_parcel_refs.append(pnum)

            contracts.append(ContractRecord(
                contract_ref=ref,
                contract_type=rc.get("contract_type", "Unknown"),
                referenced_parcels=normalized_parcels,
                raw_parcel_refs=raw_parcel_refs,
                text_excerpt=rc.get("context", "")[:300],
            ))

        # Layer 2: LLM extraction
        if self.rag_context and self.llm_json:
            try:
                ctx = self.rag_context(
                    doc_ids,
                    "Nutzungsvertrag Pachtvertrag Gestattungsvertrag contract parcel agreement",
                    8,
                )
                system = (
                    "You are a legal due diligence analyst. Extract ALL contract-to-parcel mappings. "
                    "Return ONLY valid JSON. Do NOT invent data."
                )
                prompt = (
                    f"Extract contracts and their referenced parcels.\n\n"
                    f"Context:\n{ctx}\n\n"
                    f"Return JSON:\n"
                    f'[{{"contract_ref":"NV-2024-001","contract_type":"Nutzungsvertrag",'
                    f'"contracting_entity":"company","parcels":["12/4","15/7"]}}]\n'
                    f"Return [] if none found."
                )
                result = self.llm_json(system, prompt)
                if isinstance(result, dict):
                    result = result.get("contracts", result.get("data", []))
                if isinstance(result, list):
                    for item in result:
                        ref = str(item.get("contract_ref", ""))
                        if not ref or ref in seen_refs:
                            continue
                        seen_refs.add(ref)
                        parcels_raw = item.get("parcels", [])
                        normalized = [
                            normalize_parcel_id(normalize_parcel_number(p))
                            for p in parcels_raw
                        ]
                        contracts.append(ContractRecord(
                            contract_ref=ref,
                            contract_type=str(item.get("contract_type", "Unknown")),
                            contracting_entity=str(item.get("contracting_entity", "")),
                            referenced_parcels=normalized,
                            raw_parcel_refs=[str(p) for p in parcels_raw],
                        ))
            except Exception as e:
                logger.warning(f"LLM contract extraction failed: {e}")

        # Enrich: associate orphan parcels to nearby contracts
        all_text_parcels = extract_parcel_refs_enhanced(full_text)
        if contracts and all_text_parcels:
            for contract in contracts:
                if not contract.referenced_parcels:
                    for tp in all_text_parcels:
                        norm = normalize_parcel_id(tp["parcel_number"], tp.get("gemarkung", ""), tp.get("flur", 0))
                        if norm not in contract.referenced_parcels:
                            c_pos = full_text.find(contract.contract_ref)
                            if c_pos >= 0 and abs(tp["position"] - c_pos) < 2000:
                                contract.referenced_parcels.append(norm)
                                contract.raw_parcel_refs.append(tp["parcel_number"])

        return contracts

    # ── Step 6 ────────────────────────────────────────────────────────────────
    def _step6_match(
        self,
        relevant_parcels: list[RawParcel],
        contracts: list[ContractRecord],
        wea_statuses: list,
    ) -> tuple[list[ClassifiedParcel], list[ClassifiedParcel]]:
        contract_index = {}
        for contract in contracts:
            for norm_id in contract.referenced_parcels:
                contract_index[norm_id] = contract

        contract_by_number = {}
        for contract in contracts:
            for raw_ref in contract.raw_parcel_refs:
                norm_num = normalize_parcel_number(raw_ref)
                contract_by_number[norm_num] = contract

        wea_by_parcel = {}
        for w in (wea_statuses or []):
            m = re.search(r"(\d+[/\-]\d+)", w.parcel or "")
            if m:
                wea_by_parcel[normalize_parcel_number(m.group(1))] = w

        exact_matches = []
        fuzzy_matches = []

        for parcel in relevant_parcels:
            norm_id = parcel.normalized_id

            matched = contract_index.get(norm_id)
            match_type = "exact_full"

            if not matched:
                matched = contract_by_number.get(parcel.parcel_number)
                match_type = "exact_number"

            if matched:
                linked_wea = wea_by_parcel.get(parcel.parcel_number)
                exact_matches.append(ClassifiedParcel(
                    parcel_number=parcel.parcel_number,
                    gemarkung=parcel.gemarkung,
                    flur=parcel.flur,
                    polygon=parcel.polygon,
                    polygon_source="alkis_wfs" if parcel.source.startswith("alkis") else "estimated",
                    area_m2=parcel.area_m2,
                    area_ha=round(parcel.area_m2 / 10000, 2) if parcel.area_m2 else 0,
                    normalized_id=norm_id,
                    classification=ParcelClassification.SECURED,
                    color="green",
                    confidence=1.0 if match_type == "exact_full" else 0.85,
                    classification_reason=f"Matched to contract {matched.contract_ref} ({match_type})",
                    matched_contract_id=matched.contract_id,
                    matched_contract_ref=matched.contract_ref,
                    linked_wea=linked_wea.name if linked_wea else None,
                    owner=linked_wea.owner if linked_wea else "",
                ))
                continue

            # Fuzzy matching
            best_score = 0.0
            best_contract = None
            for contract in contracts:
                for contract_norm_id in contract.referenced_parcels:
                    score = fuzzy_match_parcel_id(norm_id, contract_norm_id)
                    if score > best_score:
                        best_score = score
                        best_contract = contract

            if best_score >= 0.6 and best_contract:
                linked_wea = wea_by_parcel.get(parcel.parcel_number)
                fuzzy_matches.append(ClassifiedParcel(
                    parcel_number=parcel.parcel_number,
                    gemarkung=parcel.gemarkung,
                    flur=parcel.flur,
                    polygon=parcel.polygon,
                    polygon_source="alkis_wfs" if parcel.source.startswith("alkis") else "estimated",
                    area_m2=parcel.area_m2,
                    area_ha=round(parcel.area_m2 / 10000, 2) if parcel.area_m2 else 0,
                    normalized_id=norm_id,
                    classification=ParcelClassification.UNCERTAIN,
                    color="yellow",
                    confidence=round(best_score, 2),
                    classification_reason=f"Fuzzy match to {best_contract.contract_ref} (score={best_score:.2f})",
                    matched_contract_id=best_contract.contract_id,
                    matched_contract_ref=best_contract.contract_ref,
                    linked_wea=linked_wea.name if linked_wea else None,
                    owner=linked_wea.owner if linked_wea else "",
                ))

        return exact_matches, fuzzy_matches

    # ── Step 7 ────────────────────────────────────────────────────────────────
    def _step7_unsecured(
        self, relevant_parcels: list[RawParcel], secured_ids: set, fuzzy_ids: set,
    ) -> list[ClassifiedParcel]:
        unsecured = []
        for parcel in relevant_parcels:
            if parcel.normalized_id in secured_ids or parcel.normalized_id in fuzzy_ids:
                continue
            unsecured.append(ClassifiedParcel(
                parcel_number=parcel.parcel_number,
                gemarkung=parcel.gemarkung,
                flur=parcel.flur,
                polygon=parcel.polygon,
                polygon_source="alkis_wfs" if parcel.source.startswith("alkis") else "estimated",
                area_m2=parcel.area_m2,
                area_ha=round(parcel.area_m2 / 10000, 2) if parcel.area_m2 else 0,
                normalized_id=parcel.normalized_id,
                classification=ParcelClassification.NOT_SECURED,
                color="red",
                confidence=1.0,
                classification_reason="No matching contract found in analyzed documents",
            ))
        return unsecured

    # ── Step 8 ────────────────────────────────────────────────────────────────
    def _step8_ambiguity(self, fuzzy_matches: list[ClassifiedParcel]) -> list[ClassifiedParcel]:
        for p in fuzzy_matches:
            if p.confidence < 0.75:
                p.classification_reason += " — needs manual review"
                p.notes = "Ambiguous match: identifier partially matches contract data"
        return fuzzy_matches

    # ── Steps 9-11 ────────────────────────────────────────────────────────────
    def _step9_11_classify(
        self,
        secured: list[ClassifiedParcel],
        unsecured: list[ClassifiedParcel],
        uncertain: list[ClassifiedParcel],
    ) -> list[ClassifiedParcel]:
        all_parcels = []
        seen_ids = set()

        # Priority: secured > uncertain > unsecured
        for parcel_list in [secured, uncertain, unsecured]:
            for p in parcel_list:
                if p.normalized_id in seen_ids:
                    continue
                seen_ids.add(p.normalized_id)

                if p.classification == ParcelClassification.SECURED:
                    p.color = "green"
                elif p.classification == ParcelClassification.UNCERTAIN:
                    p.color = "yellow"
                else:
                    p.color = "red"

                p.id = f"cp-{len(all_parcels) + 1:04d}"
                all_parcels.append(p)

        return all_parcels
