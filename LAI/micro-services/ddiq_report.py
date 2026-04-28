"""
DDiQ Report Generation Module — v4 (LAI v1 Production)
─────────────────────────────────────────────────────────────────────
13-step cadastral classification pipeline per Output Map spec.
ALKIS WFS for all Bundeslaender + contract-to-parcel matching +
GeoJSON output + clearance zones + validation.

Mount: from ddiq_report import router as ddiq_router
       app.include_router(ddiq_router, prefix="/ddiq")
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os, re, json, time, uuid, logging, math
import requests
import psycopg2
import psycopg2.extras
import fitz  # PyMuPDF
import numpy as np
import pytesseract
from PIL import Image
import io
from cadastral_pipeline import (
    CadastralPipeline, PipelineResult, ProjectArea, ClassifiedParcel,
    ClearanceZone, ValidationReport, ContractRecord,
    ParcelClassification, generate_geojson, validate_results,
    normalize_parcel_number, normalize_parcel_id,
    build_clearance_zones, make_circle_polygon,
)

logger = logging.getLogger("ddiq")
router = APIRouter(tags=["DDiQ Report"])

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

LLM_URL       = os.getenv("LLM_URL", "http://localhost:8001/v1")
LLM_MODEL     = os.getenv("LLM_MODEL", "legal-lora")
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "http://localhost:8002")
RERANKER_URL  = os.getenv("RERANKER_URL", "http://localhost:8004")
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA  = "LAI-DDiQ/1.0 (legal-ai-report-generator)"

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "lai_db"),
    "user":     os.getenv("DB_USER", "lai_user"),
    "password": os.getenv("DB_PASSWORD", "lai_test_password_2024"),
}

MAX_FILE_SIZE = 50 * 1024 * 1024

# ═══════════════════════════════════════════════════════════════════════════════
# ALKIS WFS — German Federal State Cadastral Services (INSPIRE CadastralParcels)
# ═══════════════════════════════════════════════════════════════════════════════

ALKIS_WFS_ENDPOINTS = {
    "niedersachsen": {"url": "https://www.opengeodata.lgln.niedersachsen.de/doorman/noauth/wfs_ni_inspire-flurstuecke_alkis", "typename": "cp:CadastralParcel", "label": "Niedersachsen LGLN"},
    "nordrhein-westfalen": {"url": "https://www.wfs.nrw.de/geobasis/wfs_nw_inspire-flurstuecke_alkis", "typename": "cp:CadastralParcel", "label": "NRW Geobasis"},
    "schleswig-holstein": {"url": "https://service.gdi-sh.de/WFS_SH_INSPIRE_CP", "typename": "cp:CadastralParcel", "label": "SH GDI"},
    "brandenburg": {"url": "https://inspire.brandenburg.de/services/cp_wfs", "typename": "cp:CadastralParcel", "label": "Brandenburg LGB"},
    "mecklenburg-vorpommern": {"url": "https://www.geodaten-mv.de/dienste/inspire_cp_alkis_download", "typename": "cp:CadastralParcel", "label": "MV LAIV"},
    "sachsen-anhalt": {"url": "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_INSPIRE_CP_WFS/guest", "typename": "cp:CadastralParcel", "label": "SA LVG"},
    "hessen": {"url": "https://www.gds.hessen.de/wfs2/aaa-bkg/inspire_cp_alkis", "typename": "cp:CadastralParcel", "label": "Hessen HVBG"},
    "thueringen": {"url": "https://www.geoproxy.geoportal-th.de/geoproxy/services/inspire_cp_alkis_wfs", "typename": "cp:CadastralParcel", "label": "Thueringen TLVermGeo"},
    "sachsen": {"url": "https://geodienste.sachsen.de/wfs_geobasis_inspire_cp/guest", "typename": "cp:CadastralParcel", "label": "Sachsen GeoSN"},
    "rheinland-pfalz": {"url": "https://www.geoportal.rlp.de/spatial-objects/314/services/inspire_cp_alkis_wfs", "typename": "cp:CadastralParcel", "label": "RLP LVermGeo"},
    "bayern": {"url": "https://geoservices.bayern.de/wfs/ogc_inspire_cp.cgi", "typename": "cp:CadastralParcel", "label": "Bayern LDBV"},
    "baden-wuerttemberg": {"url": "https://owsproxy.lgl-bw.de/owsproxy/wfs/WFS_ALKIS_INSPIRE_CP", "typename": "cp:CadastralParcel", "label": "BW LGL"},
}

BUNDESLAND_KEYWORDS = {
    "niedersachsen": ["niedersachsen", "hannover", "braunschweig", "oldenburg", "osnabrück", "lüneburg", "göttingen", "wolfsburg", "cuxhaven", "hude", "hatten", "lamstedt"],
    "nordrhein-westfalen": ["nordrhein-westfalen", "nrw", "düsseldorf", "köln", "münster", "detmold", "arnsberg", "dortmund", "essen"],
    "schleswig-holstein": ["schleswig-holstein", "kiel", "lübeck", "flensburg", "husum", "dithmarschen"],
    "brandenburg": ["brandenburg", "potsdam", "cottbus", "uckermark", "prignitz"],
    "mecklenburg-vorpommern": ["mecklenburg", "vorpommern", "rostock", "schwerin", "stralsund", "rügen"],
    "sachsen-anhalt": ["sachsen-anhalt", "magdeburg", "halle", "dessau", "stendal", "altmark"],
    "bayern": ["bayern", "bavaria", "münchen", "nürnberg", "augsburg"],
    "hessen": ["hessen", "wiesbaden", "frankfurt", "kassel", "darmstadt"],
    "thüringen": ["thüringen", "erfurt", "jena", "weimar"],
    "sachsen": ["sachsen", "dresden", "leipzig", "chemnitz"],
    "rheinland-pfalz": ["rheinland-pfalz", "mainz", "koblenz", "trier"],
    "baden-württemberg": ["baden-württemberg", "stuttgart", "karlsruhe", "freiburg"],
    "saarland": ["saarland", "saarbrücken"],
    "bremen": ["bremen", "bremerhaven"],
    "hamburg": ["hamburg"],
    "berlin": ["berlin"],
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ddiq_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), filename TEXT NOT NULL,
    size_bytes BIGINT DEFAULT 0, upload_date TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'pending', category TEXT DEFAULT 'Uncategorized',
    full_text TEXT, chunk_count INT DEFAULT 0, session_id TEXT);
CREATE TABLE IF NOT EXISTS ddiq_doc_chunks (
    id SERIAL PRIMARY KEY, doc_id UUID REFERENCES ddiq_documents(id) ON DELETE CASCADE,
    chunk_idx INT NOT NULL, text TEXT NOT NULL, embedding vector(1024), UNIQUE(doc_id, chunk_idx));
CREATE TABLE IF NOT EXISTS ddiq_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), created_at TIMESTAMPTZ DEFAULT NOW(),
    project_name TEXT, document_ids UUID[], preset TEXT, report_data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS ddiq_geocode_cache (
    address TEXT PRIMARY KEY, lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL, cached_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS ddiq_parcel_cache (
    coord_key TEXT PRIMARY KEY, parcel_data JSONB NOT NULL, cached_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS ddiq_project_areas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT,
    polygon JSONB NOT NULL, centroid_lat DOUBLE PRECISION, centroid_lng DOUBLE PRECISION,
    area_km2 DOUBLE PRECISION DEFAULT 0, source TEXT DEFAULT 'user_drawn',
    created_at TIMESTAMPTZ DEFAULT NOW(), report_id UUID);
CREATE TABLE IF NOT EXISTS ddiq_contracts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), doc_id UUID,
    contract_ref TEXT, contract_type TEXT, contracting_entity TEXT,
    raw_text_excerpt TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), report_id UUID);
CREATE TABLE IF NOT EXISTS ddiq_contract_parcels (
    id SERIAL PRIMARY KEY, contract_id UUID REFERENCES ddiq_contracts(id) ON DELETE CASCADE,
    parcel_identifier TEXT NOT NULL, match_type TEXT DEFAULT 'exact',
    confidence DOUBLE PRECISION DEFAULT 1.0);
CREATE TABLE IF NOT EXISTS ddiq_classified_parcels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), report_id UUID,
    parcel_number TEXT NOT NULL, gemarkung TEXT, flur INT DEFAULT 0,
    normalized_id TEXT, polygon JSONB, polygon_source TEXT DEFAULT 'estimated',
    classification TEXT NOT NULL DEFAULT 'not_secured',
    color TEXT DEFAULT 'red', confidence DOUBLE PRECISION DEFAULT 0,
    matched_contract_id UUID, classification_reason TEXT,
    area_ha DOUBLE PRECISION DEFAULT 0, owner TEXT, linked_wea TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW());
CREATE INDEX IF NOT EXISTS idx_ddiq_chunks_doc ON ddiq_doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_ddiq_classified_report ON ddiq_classified_parcels(report_id);
CREATE INDEX IF NOT EXISTS idx_ddiq_contracts_report ON ddiq_contracts(report_id);
"""

def init_db():
    try:
        conn = psycopg2.connect(**DB_CONFIG); cur = conn.cursor()
        cur.execute(SCHEMA_SQL); conn.commit(); cur.close(); conn.close()
        logger.info("DDiQ tables initialized")
    except Exception as e:
        logger.warning(f"DDiQ DB init skipped: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class DocumentOut(BaseModel):
    id: str; name: str; size: float; uploadDate: str; type: str; status: str; category: str
class DocumentListResponse(BaseModel):
    documents: list[DocumentOut]; total: int
class UploadDocResponse(BaseModel):
    id: str; filename: str; pages: int; chunks: int; status: str; message: str
class AusgabeblattRow(BaseModel):
    label: str; value: str; ampel: Optional[str] = None; note: Optional[str] = None
class AusgabeblattSection(BaseModel):
    id: str; title: str; rows: list[AusgabeblattRow]
class WEAStatus(BaseModel):
    name: str; ampel: str; owner: str; parcel: str; contract: str; lat: float; lng: float; address: str
    clearance_radius_m: float = 1000.0
class InfraPoint(BaseModel):
    name: str; type: str; lat: float; lng: float
class CadastralParcel(BaseModel):
    id: str; parcelNumber: str; gemarkung: str; flur: int; polygon: list[list[float]]
    status: str; owner: str; area: float; contractRef: Optional[str] = None
    linkedWEA: Optional[str] = None; notes: Optional[str] = None
    polygonSource: str = "estimated"  # "alkis_wfs", "document", "estimated"
    confidence: float = 0.0
    normalizedId: str = ""
class Finding(BaseModel):
    domain: str; severity: str; text: str
class DDiQReportData(BaseModel):
    projectName: str; preparedBy: str; preparedFor: str; date: str; projectCenter: dict
    sections: list[AusgabeblattSection]; weaStatuses: list[WEAStatus]
    infrastructure: list[InfraPoint]; parcels: list[CadastralParcel]
    findings: list[Finding]; analyzedDocuments: list[str]
    projectArea: Optional[dict] = None          # Project area polygon data
    clearanceZones: Optional[list[dict]] = None  # WEA clearance zone circles
    validation: Optional[dict] = None            # Validation report
    geojson: Optional[dict] = None               # GeoJSON FeatureCollection
class GenerateReportRequest(BaseModel):
    document_ids: list[str]; preset: str = "full"
    project_name: Optional[str] = None; prepared_for: Optional[str] = None
class GenerateReportResponse(BaseModel):
    report_id: str; report: DDiQReportData; timings: dict


# ═══════════════════════════════════════════════════════════════════════════════
# CORE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_conn(): return psycopg2.connect(**DB_CONFIG)

def clean_value(val, fallback: str = "Not specified in documents") -> str:
    s = str(val).strip()
    return fallback if s.lower() in ("null", "none", "n/a", "na", "nil", "undefined", "") else s

def extract_pdf_text(file_bytes: bytes) -> tuple[str, int]:
    doc = fitz.open(stream=file_bytes, filetype="pdf"); pages = []
    for page in doc:
        text = page.get_text().strip()
        if not text or len(text) < 50:
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img, lang="deu+eng")
        if text.strip(): pages.append(text.strip())
    doc.close(); return "\n\n".join(pages), len(pages)

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[dict]:
    chunks = []; start = 0; idx = 0
    while start < len(text):
        ct = text[start:start+chunk_size].strip()
        if ct: chunks.append({"idx": idx, "text": ct}); idx += 1
        start += chunk_size - overlap
    return chunks

def embed_texts(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    all_emb = []
    for i in range(0, len(texts), batch_size):
        resp = requests.post(f"{EMBEDDING_URL}/embed", json={"inputs": texts[i:i+batch_size]}, timeout=120)
        resp.raise_for_status(); all_emb.extend(resp.json())
    return all_emb

def embed_single(text: str) -> list[float]:
    resp = requests.post(f"{EMBEDDING_URL}/embed", json={"inputs": text}, timeout=30)
    resp.raise_for_status(); return resp.json()[0]

def search_doc_chunks(doc_ids, query_embedding, top_k=15):
    if not doc_ids: return []
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    ph = ",".join(["%s"] * len(doc_ids))
    sql = f"""SELECT c.text, c.doc_id, d.filename,
              1-(c.embedding<=>%s::vector) AS similarity
              FROM ddiq_doc_chunks c JOIN ddiq_documents d ON d.id=c.doc_id
              WHERE c.doc_id::text IN ({ph})
              AND c.embedding IS NOT NULL
              ORDER BY c.embedding<=>%s::vector LIMIT %s"""
    cur.execute(sql, (emb_str, *doc_ids, emb_str, top_k))
    rows = cur.fetchall(); cur.close(); conn.close(); return [dict(r) for r in rows]

def get_all_text_for_docs(doc_ids):
    conn = get_conn(); cur = conn.cursor()
    ph = ",".join(["%s"] * len(doc_ids))
    cur.execute(f"SELECT full_text FROM ddiq_documents WHERE id::text IN ({ph})", tuple(doc_ids))
    texts = [row[0] for row in cur.fetchall() if row[0]]; cur.close(); conn.close()
    return "\n\n---\n\n".join(texts)

def rerank(query, chunks, top_k=5):
    texts = [c["text"] for c in chunks]
    try:
        resp = requests.post(f"{RERANKER_URL}/rerank", json={"query": query, "texts": texts, "truncate": True}, timeout=30)
        resp.raise_for_status(); ranked = sorted(resp.json(), key=lambda x: x["score"], reverse=True)[:top_k]
        return [chunks[item["index"]] for item in ranked]
    except Exception: return chunks[:top_k]

def llm_call(system, user, temperature=0.1, max_tokens=2048):
    resp = requests.post(f"{LLM_URL}/chat/completions", json={"model": LLM_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature}, timeout=300)
    resp.raise_for_status(); return resp.json()["choices"][0]["message"]["content"].strip()

def llm_json(system, user, temperature=0.0):
    raw = llm_call(system, user, temperature, max_tokens=4096)
    raw = re.sub(r"```json\s*", "", raw); raw = re.sub(r"```\s*$", "", raw)
    try: return json.loads(raw)
    except json.JSONDecodeError:
        raw2 = llm_call(system + "\n\nCRITICAL: Return ONLY valid JSON.", user, temperature, max_tokens=4096)
        raw2 = re.sub(r"```json\s*", "", raw2); raw2 = re.sub(r"```\s*$", "", raw2)
        return json.loads(raw2)

def rag_context(doc_ids, question, top_k=5):
    emb = embed_single(question); chunks = search_doc_chunks(doc_ids, emb, top_k=20)
    if not chunks: return "(No relevant content found)"
    reranked = rerank(question, chunks, top_k=top_k)
    return "\n\n".join([f"[Doc: {c.get('filename','?')}]\n{c['text'][:800]}" for c in reranked])


# ═══════════════════════════════════════════════════════════════════════════════
# GEOCODING + PARCEL POLYGON
# ═══════════════════════════════════════════════════════════════════════════════

def geocode_address(address):
    if not address or not address.strip(): return None
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT lat, lng FROM ddiq_geocode_cache WHERE address = %s", (address,))
    row = cur.fetchone()
    if row: cur.close(); conn.close(); return (row[0], row[1])
    try:
        resp = requests.get(NOMINATIM_URL, params={"q": address, "format": "json", "limit": 1, "countrycodes": "de"},
            headers={"User-Agent": NOMINATIM_UA}, timeout=10)
        resp.raise_for_status(); results = resp.json()
        if results:
            lat, lng = float(results[0]["lat"]), float(results[0]["lon"])
            cur.execute("INSERT INTO ddiq_geocode_cache (address, lat, lng) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (address, lat, lng))
            conn.commit(); cur.close(); conn.close(); time.sleep(1.1); return (lat, lng)
    except Exception as e: logger.warning(f"Geocoding failed for '{address}': {e}")
    cur.close(); conn.close(); return None

def make_parcel_polygon(lat, lng, area_ha=2.5, rotation_seed=0):
    """Create an estimated parcel polygon. Marked as 'estimated' — not real cadastral data."""
    side_m = (area_ha * 10000) ** 0.5
    dlat = (side_m/2)/111000
    dlng = (side_m/2)/67000
    # Apply slight rotation based on seed to avoid identical-looking rectangles
    angle = (rotation_seed * 0.3) % (math.pi / 4)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    corners = [
        (dlat, -dlng), (dlat, dlng), (-dlat, dlng), (-dlat, -dlng)
    ]
    rotated = []
    for dy, dx in corners:
        ry = dy * cos_a - dx * sin_a
        rx = dy * sin_a + dx * cos_a
        rotated.append([lat + ry, lng + rx])
    return rotated


# ═══════════════════════════════════════════════════════════════════════════════
# ALKIS WFS QUERY — Real Cadastral Parcels from Coordinates
# ═══════════════════════════════════════════════════════════════════════════════

def detect_bundesland(location: str) -> Optional[str]:
    loc = location.lower()
    for state, keywords in BUNDESLAND_KEYWORDS.items():
        if any(kw in loc for kw in keywords): return state
    return None

def alkis_query_parcels(lat: float, lng: float, bundesland: str, radius_m: float = 150) -> list[dict]:
    """Query ALKIS INSPIRE WFS → returns real Flurstück data with polygons."""
    config = ALKIS_WFS_ENDPOINTS.get(bundesland)
    if not config: return []

    # Check cache
    cache_key = f"alkis:{lat:.5f},{lng:.5f}"
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT parcel_data FROM ddiq_parcel_cache WHERE coord_key = %s", (cache_key,))
        row = cur.fetchone(); cur.close(); conn.close()
        if row:
            data = row[0] if isinstance(row[0], list) else json.loads(row[0])
            logger.info(f"ALKIS cache hit: {cache_key} ({len(data)} parcels)")
            return data
    except Exception: pass

    buf = radius_m / 111000
    params = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAMES": config["typename"], "SRSNAME": "EPSG:4326",
        "BBOX": f"{lat-buf},{lng-buf},{lat+buf},{lng+buf},EPSG:4326",
        "COUNT": "10", "OUTPUTFORMAT": "application/json"}

    parcels: list[dict] = []
    headers = {"User-Agent": NOMINATIM_UA}
    try:
        logger.info(f"ALKIS WFS: {config['label']} at {lat:.5f},{lng:.5f}")
        # Try JSON first
        resp = requests.get(config["url"], params=params, timeout=20, headers=headers)
        # Several state WFS (e.g. NRW INSPIRE-CP) return 400 when asked for JSON
        # because they only speak GML. Fall back without OUTPUTFORMAT.
        if resp.status_code == 400 or "application/json" not in (resp.headers.get("content-type") or "").lower():
            logger.info(f"ALKIS WFS: JSON not accepted ({resp.status_code}), retrying with GML")
            params_gml = {k: v for k, v in params.items() if k != "OUTPUTFORMAT"}
            resp = requests.get(config["url"], params=params_gml, timeout=20, headers=headers)
            resp.raise_for_status()
            parcels = _parse_alkis_xml(resp.text, lat, lng)
        else:
            resp.raise_for_status()
            try:
                data = resp.json()
                features = data.get("features", [])
                parcels = [p for p in (_parse_alkis_feature(f) for f in features) if p]
            except json.JSONDecodeError:
                logger.info("ALKIS returned non-JSON despite header; trying XML parser")
                parcels = _parse_alkis_xml(resp.text, lat, lng)
        logger.info(f"ALKIS WFS → {len(parcels)} parcels")

        # Cache
        try:
            conn = get_conn(); cur = conn.cursor()
            cur.execute("INSERT INTO ddiq_parcel_cache (coord_key, parcel_data) VALUES (%s,%s) ON CONFLICT (coord_key) DO UPDATE SET parcel_data=%s, cached_at=NOW()",
                (cache_key, json.dumps(parcels), json.dumps(parcels)))
            conn.commit(); cur.close(); conn.close()
        except Exception: pass

        return parcels
    except Exception as e:
        logger.warning(f"ALKIS WFS failed for {bundesland}: {e}")
    return []


def _parse_alkis_feature(feature: dict) -> Optional[dict]:
    """Parse one GeoJSON feature from ALKIS INSPIRE WFS."""
    props = feature.get("properties", {}); geom = feature.get("geometry", {})

    # Parcel number
    pnum = None
    for key in ["label", "flurstuecksnummer", "flstnrzae", "bezeichnung", "flstNr"]:
        if props.get(key): pnum = str(props[key]).strip(); break
    ncr = str(props.get("nationalCadastralReference", ""))
    if not pnum and ncr:
        parts = ncr.split("-")
        if len(parts) >= 3:
            pnum = re.sub(r"^0+", "", parts[-1])
            pnum = re.sub(r"/0+", "/", pnum)
    if not pnum: return None

    # Gemarkung
    gemarkung = ""
    for key in ["gemarkungsname", "gemarkung", "gemeinde", "municipality"]:
        if props.get(key): gemarkung = str(props[key]).strip(); break

    # Flur
    flur = 0
    for key in ["flurnummer", "flur", "flurNr"]:
        if props.get(key):
            try: flur = int(props[key])
            except (ValueError, TypeError): pass; break

    # Area
    area_m2 = 0
    for key in ["areaValue", "amtlicheFlaeche", "flaeche", "area"]:
        if props.get(key):
            try: area_m2 = float(props[key])
            except (ValueError, TypeError): pass; break

    # Polygon — GeoJSON [lng,lat] → Leaflet [lat,lng]
    polygon = []
    if geom.get("type") == "Polygon" and geom.get("coordinates"):
        polygon = [[pt[1], pt[0]] for pt in geom["coordinates"][0]]
    elif geom.get("type") == "MultiPolygon" and geom.get("coordinates"):
        largest = max(geom["coordinates"], key=lambda p: len(p[0]))
        polygon = [[pt[1], pt[0]] for pt in largest[0]]

    return {"parcelNumber": pnum, "gemarkung": gemarkung, "flur": flur,
            "polygon": polygon, "area_m2": area_m2, "source": "ALKIS WFS"}


def _parse_alkis_xml(xml_text, lat, lng):
    """Parse INSPIRE Cadastral Parcels GML/XML responses.

    Many state WFS (NRW, Bayern, Hessen, ...) only return GML — not JSON.
    Schema (INSPIRE CP v4.0):

        <wfs:FeatureCollection>
          <wfs:member>
            <cp:CadastralParcel>
              <cp:areaValue uom="m2">…</cp:areaValue>
              <cp:label>…</cp:label>
              <cp:nationalCadastralReference>…</cp:nationalCadastralReference>
              <cp:geometry>
                <gml:Polygon srsName="…4326">
                  <gml:exterior><gml:LinearRing>
                    <gml:posList>lat lng lat lng …</gml:posList>
                  </gml:LinearRing></gml:exterior>
                  …optional gml:interior…
                </gml:Polygon>
              </cp:geometry>
              <cp:referencePoint><gml:Point><gml:pos>lat lng</gml:pos></gml:Point></cp:referencePoint>
            </cp:CadastralParcel>
          </wfs:member>
        </wfs:FeatureCollection>

    For EPSG:4326 INSPIRE specifies lat-lng axis order, which matches what
    we use everywhere in the UI ([lat,lng] for Leaflet). For projected SRS
    we'd need pyproj — out of scope here; we request 4326 to avoid that.

    Falls back to a synthetic polygon around (lat, lng) only if the parser
    finds a CadastralParcel element with no geometry — better than dropping
    the parcel silently.
    """
    import xml.etree.ElementTree as ET
    parcels: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        logger.warning(f"ALKIS XML parse: {e}")
        return parcels

    NS_GML = "{http://www.opengis.net/gml/3.2}"
    NS_CP = "{http://inspire.ec.europa.eu/schemas/cp/4.0}"

    def _local(tag: str) -> str:
        # strip namespace for tolerant matching
        return tag.split("}", 1)[-1] if "}" in tag else tag

    def _text_of_child(parent, local_name: str) -> Optional[str]:
        for c in parent:
            if _local(c.tag) == local_name and (c.text or "").strip():
                return c.text.strip()
        return None

    def _parse_pos_list(text: str) -> list[list[float]]:
        nums = [float(x) for x in (text or "").split() if x]
        # Pairs of (lat, lng)
        return [[nums[i], nums[i + 1]] for i in range(0, len(nums) - 1, 2)]

    # Walk all CadastralParcel elements regardless of namespace prefix
    for parcel_el in root.iter():
        if _local(parcel_el.tag) != "CadastralParcel":
            continue

        label = _text_of_child(parcel_el, "label") or ""
        ncr = _text_of_child(parcel_el, "nationalCadastralReference") or ""
        area_text = _text_of_child(parcel_el, "areaValue")
        try:
            area_m2 = float(area_text) if area_text else 0.0
        except ValueError:
            area_m2 = 0.0

        # Find first gml:posList descendant for the exterior ring
        polygon: list[list[float]] = []
        for posList in parcel_el.iter(NS_GML + "posList"):
            polygon = _parse_pos_list(posList.text or "")
            if polygon:
                break
        # If no polygon, fall back to a small synthetic ring at the query point
        if not polygon:
            polygon = make_parcel_polygon(lat, lng)

        # Parcel number — prefer cp:label, otherwise tail of nationalCadastralReference
        pnum = label
        if not pnum and ncr:
            tail = re.sub(r"_+$", "", ncr).split("-")[-1]
            pnum = re.sub(r"^0+", "", tail) or tail

        parcels.append({
            "parcelNumber": pnum,
            "gemarkung": "",
            "flur": 0,
            "polygon": polygon,
            "area_m2": area_m2,
            "source": "ALKIS WFS (GML)",
            "nationalCadastralReference": ncr,
        })

    return parcels


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_section_value(sections, section_id, label):
    for sec in sections:
        if sec.id == section_id:
            for row in sec.rows:
                if row.label == label: return row.value
    return ""

def parse_wea_count(value):
    m = re.search(r"(\d+)", value); return int(m.group(1)) if m else 0

def geocode_project_location(sections):
    location = get_section_value(sections, "overview", "Location")
    if location:
        coords = geocode_address(location)
        if coords: return coords
        for part in [p.strip() for p in location.replace(",", " ").split() if len(p.strip()) > 2]:
            coords = geocode_address(f"{part}, Germany")
            if coords: return coords
    name = get_section_value(sections, "overview", "Project Name")
    if name:
        clean = re.sub(r"(?i)windpark|windenergie|wind\s*farm", "", name).strip()
        if clean:
            coords = geocode_address(f"{clean}, Germany")
            if coords: return coords
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# REGEX EXTRACTORS
# ═══════════════════════════════════════════════════════════════════════════════

PARCEL_RE = re.compile(r"(?:Flurst[üu]ck|Grundst[üu]ck|Parzelle)[s]?\s*(?:Nr\.?\s*)?(\d+[/]\d+)(?:.*?Gemarkung\s+([A-ZÄÖÜa-zäöüß]+))?(?:.*?Flur\s+(\d+))?", re.IGNORECASE|re.DOTALL)
CONTRACT_REF_RE = re.compile(r"(?:Vertrag|Nutzungsvertrag|Pachtvertrag|Gestattungsvertrag)[s-]*(?:Nr\.?\s*|nummer\s*)?([A-Z0-9][\w-]*\d+)", re.IGNORECASE)

def extract_parcel_refs(text):
    found = []; seen = set()
    for m in PARCEL_RE.finditer(text):
        num = m.group(1)
        if num in seen: continue
        seen.add(num); found.append({"parcelNumber": num, "gemarkung": m.group(2) or "", "flur": int(m.group(3)) if m.group(3) else 0})
    return found


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION ANALYSIS + WEA + INFRA + PARCELS + FINDINGS
# ═══════════════════════════════════════════════════════════════════════════════

EXTRACTION_SYSTEM = """You are a legal due diligence analyst for German wind energy projects.
Extract structured data from contract documents. ALWAYS respond with valid JSON only.
No markdown, no explanations. If not in documents, use null for optional fields."""

SECTION_QUESTIONS = {
    "overview": [("Project Name","What is the project name or wind farm name?"),("Location","What is the project location (district, state, municipality)?"),("Project Status","What is the current project status?"),("Project Type","Is this greenfield, repowering, or expansion?"),("Number of WEA","How many wind turbines (WEA) are planned or installed?"),("Type & Capacity","What is the turbine model and capacity per unit?"),("Total Capacity","What is the total installed capacity? Answer with unit MW."),("Project Company","What is the project company (Projektgesellschaft)?"),("Investors","Who are the investors or shareholders?"),("Grid Connection","What is the grid connection point and cable route length?"),("Wind Priority Zone","Is the project in a designated wind priority zone?")],
    "land": [("Usage Contracts","How many site usage contracts are signed vs. total needed?"),("Land Registry","Are easements registered in the land registry?"),("Buffer Zone Security","Are buffer/setback zones contractually secured?"),("Cable Route","Is the cable route easement secured?"),("Access Roads","Are access road agreements in place?"),("Contract Error Rate","Are there contracts with defects?"),("Contracts Reviewed","How many contracts reviewed? Answer as 'X contracts'."),("Contracting Entity","Is the contracting entity consistent across all contracts?")],
    "permits": [("BImSchG Permit","Status of BImSchG permit application?"),("Environmental Impact","Has UVP/EIA been conducted?"),("Species Protection","Are there species protection requirements?"),("Noise & Shadow","Do noise/shadow assessments meet requirements?"),("Authority Consultations","Have all authority consultations been completed?"),("Recurring Inspections","What recurring inspections exist?")],
    "economics": [("Feed-in Tariff","What EEG tariff or auction award applies?"),("PPA","Is there a PPA? With whom and at what price?"),("Profitability","What is the project IRR?"),("Financing","What is the financing structure?"),("Securities","What securities are in place?"),("Operations","Who is the O&M operator?"),("Maintenance","What is the maintenance contract scope?"),("Insurance","What insurance coverage is in place?"),("Open Liability","Are there open or contingent liabilities?")],
}


def analyze_section(doc_ids, section_id):
    questions = SECTION_QUESTIONS.get(section_id, [])
    title_map = {"overview": "Project Overview", "land": "Land Security & Ownership", "permits": "Permits & Regulatory Conditions", "economics": "Economics & Operations"}
    rows = []
    for label, question in questions:
        context = rag_context(doc_ids, question, top_k=5)
        prompt = f"""Answer this due diligence question based on documents.\n\nContext:\n{context}\n\nQuestion: {question}\n\nRespond JSON: {{"value":"answer as string","ampel":"green"/"yellow"/"red"/null,"note":"risk note or null"}}\nIMPORTANT: value MUST be a string, never a bare number. E.g. "10 contracts" not 10."""
        try:
            result = llm_json(EXTRACTION_SYSTEM, prompt)
            val = clean_value(result.get("value"), "Information not found in documents")
            note_raw = result.get("note"); note = clean_value(note_raw, "") if note_raw else None
            if note == "": note = None
            rows.append(AusgabeblattRow(label=label, value=val, ampel=result.get("ampel") if result.get("ampel") in ("green","yellow","red") else None, note=note))
        except Exception as e:
            logger.error(f"Section {section_id}/{label}: {e}")
            rows.append(AusgabeblattRow(label=label, value="Could not extract", ampel="red", note=f"Error: {str(e)[:80]}"))
    return AusgabeblattSection(id=section_id, title=title_map.get(section_id, section_id.title()), rows=rows)


def extract_wea_statuses(doc_ids, full_text, sections, project_center=None):
    context = rag_context(doc_ids, "wind turbines WEA owners parcels contract status locations")
    prompt = f"""Extract ALL wind turbines (WEA/WKA) from documents.\n\nContext:\n{context}\n\nText:\n{full_text[:6000]}\n\nReturn JSON array:\n[{{"name":"WEA Hude 1","owner":"name or Unknown","parcel":"ref or empty","contract":"ref or Not specified","address":"municipality, state","ampel":"green/yellow/red"}}]\nIMPORTANT: If "7 WEA in Hude" create "WEA Hude 1"-"WEA Hude 7". Use "yellow" for pre-check docs where status is unknown."""

    weas_raw = []
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict): weas_raw = result.get("turbines", result.get("wea", result.get("data", [])))
        elif isinstance(result, list): weas_raw = result
    except Exception as e: logger.error(f"WEA extraction: {e}")

    if not weas_raw:
        wea_count = parse_wea_count(get_section_value(sections, "overview", "Number of WEA"))
        pname = get_section_value(sections, "overview", "Project Name")
        loc = get_section_value(sections, "overview", "Location")
        company = get_section_value(sections, "overview", "Project Company")
        if wea_count > 0:
            short = re.sub(r"(?i)windpark|windenergie|wind\s*farm|projekt", "", pname).strip().split()[0] if pname else "WEA"
            for i in range(1, wea_count+1):
                weas_raw.append({"name": f"WEA {short} {i}", "owner": company or "See contracts", "parcel": "", "contract": "See contract review", "address": loc, "ampel": "yellow"})

    statuses = []
    for idx, w in enumerate(weas_raw):
        addr = str(w.get("address", ""))
        coords = geocode_address(addr) if addr else None
        if not coords and project_center: coords = project_center
        statuses.append(WEAStatus(name=str(w.get("name", f"WEA {idx+1}")),
            ampel=w.get("ampel","yellow") if w.get("ampel") in ("green","yellow","red") else "yellow",
            owner=clean_value(w.get("owner"),"Unknown"), parcel=clean_value(w.get("parcel"),""),
            contract=clean_value(w.get("contract"),"Not specified"),
            lat=coords[0] if coords else 0.0, lng=coords[1] if coords else 0.0, address=addr))

    # Scatter duplicate coordinates
    if statuses:
        cg = {}
        for i, s in enumerate(statuses): cg.setdefault(f"{s.lat:.6f},{s.lng:.6f}", []).append(i)
        for key, indices in cg.items():
            if len(indices) > 1:
                clat, clng = statuses[indices[0]].lat, statuses[indices[0]].lng
                for j, idx in enumerate(indices):
                    angle = (2*math.pi*j)/len(indices); s = statuses[idx]
                    statuses[idx] = WEAStatus(name=s.name, ampel=s.ampel, owner=s.owner, parcel=s.parcel,
                        contract=s.contract, address=s.address,
                        lat=clat+0.003*math.cos(angle), lng=clng+0.003*math.sin(angle))

    # Deduplicate names
    nc = {}
    for s in statuses: nc[s.name] = nc.get(s.name, 0) + 1
    if any(c > 1 for c in nc.values()):
        ag = {}
        for i, s in enumerate(statuses): ag.setdefault(s.address, []).append(i)
        for addr, indices in ag.items():
            short = addr.split(",")[0].strip().split()[-1] if addr else "Park"
            for j, idx in enumerate(indices):
                s = statuses[idx]
                statuses[idx] = WEAStatus(name=f"WEA {short} {j+1}", ampel=s.ampel, owner=s.owner,
                    parcel=s.parcel, contract=s.contract, lat=s.lat, lng=s.lng, address=s.address)
    return statuses


def extract_infrastructure(doc_ids, sections, project_center=None):
    context = rag_context(doc_ids, "substation grid connection cable route Umspannwerk Netzanschluss")
    infra_raw = []
    try:
        result = llm_json(EXTRACTION_SYSTEM, f"""Extract infrastructure.\n\nContext:\n{context}\n\nReturn JSON: [{{"name":"desc","type":"substation/cable_start/cable_end/access_road","address":"location"}}]""")
        if isinstance(result, dict): infra_raw = result.get("infrastructure", [])
        elif isinstance(result, list): infra_raw = result
    except Exception: pass

    if not infra_raw:
        grid = get_section_value(sections, "overview", "Grid Connection")
        loc = get_section_value(sections, "overview", "Location")
        if grid:
            short = loc.split(",")[0].strip() if loc else "Grid"
            infra_raw = [{"name": f"Substation {short}", "type": "substation", "address": loc},
                {"name": "Cable Start", "type": "cable_start", "address": loc},
                {"name": f"Cable End ({short})", "type": "cable_end", "address": loc}]

    points = []
    for p in infra_raw:
        addr = str(p.get("address", ""))
        coords = geocode_address(addr) if addr else None
        if not coords and project_center:
            offset = (len(points)+1)*0.004; angle = len(points)*2.1
            coords = (project_center[0]+offset*math.cos(angle), project_center[1]+offset*math.sin(angle))
        typ = p.get("type","substation")
        if typ not in ("substation","cable_start","cable_end","access_road"): typ = "substation"
        points.append(InfraPoint(name=str(p.get("name","Unknown")), type=typ,
            lat=coords[0] if coords else 0.0, lng=coords[1] if coords else 0.0))
    return points


def build_parcels(doc_ids, full_text, wea_statuses, project_center=None, location=""):
    """Three-layer: ALKIS WFS → Regex → LLM. Merges and deduplicates."""
    parcels = []; seen = set()
    bundesland = detect_bundesland(location) if location else None

    # ── Layer 1: ALKIS WFS from WEA coordinates ──
    if bundesland and bundesland in ALKIS_WFS_ENDPOINTS:
        logger.info(f"ALKIS WFS query: {bundesland} for {len(wea_statuses)} WEAs")
        for wea in wea_statuses:
            if wea.lat == 0: continue
            for ap in alkis_query_parcels(wea.lat, wea.lng, bundesland, 150):
                if ap["parcelNumber"] in seen: continue
                seen.add(ap["parcelNumber"])
                area_ha = round(ap.get("area_m2",0)/10000, 2) if ap.get("area_m2") else 2.0
                poly = ap.get("polygon") or make_parcel_polygon(wea.lat, wea.lng, area_ha or 2.5)
                parcels.append(CadastralParcel(id=f"p{len(parcels)+1}", parcelNumber=ap["parcelNumber"],
                    gemarkung=ap.get("gemarkung") or "Unknown", flur=ap.get("flur",0), polygon=poly,
                    status={"green":"secured","yellow":"negotiation","red":"open"}.get(wea.ampel,"open"),
                    owner=wea.owner, area=area_ha, linkedWEA=wea.name, notes=f"Source: {ap.get('source','ALKIS')}"))
            time.sleep(0.5)

    # ── Layer 2: Regex from document text ──
    text_refs = extract_parcel_refs(full_text)
    wea_by_parcel = {}
    for w in wea_statuses:
        m = re.search(r"(\d+/\d+)", w.parcel)
        if m: wea_by_parcel[m.group(1)] = w
    contract_refs = {m.group(1): m.group(0) for m in CONTRACT_REF_RE.finditer(full_text)}

    for ref in text_refs:
        pnum = str(ref.get("parcelNumber",""))
        if pnum in seen: continue
        seen.add(pnum); linked = wea_by_parcel.get(pnum)
        if linked: status = {"green":"secured","yellow":"negotiation","red":"open"}.get(linked.ampel,"open"); owner = linked.owner; lat,lng = linked.lat, linked.lng
        else: status = "buffer"; owner = ""; lat,lng = 0.0, 0.0
        if lat == 0 and project_center:
            a = (2*math.pi*len(parcels))/max(len(text_refs),1); lat = project_center[0]+0.004*math.cos(a); lng = project_center[1]+0.004*math.sin(a)
        cref = None
        for cr_id in contract_refs:
            pp = full_text.find(pnum); cp = full_text.find(cr_id)
            if pp >= 0 and cp >= 0 and abs(pp-cp) < 2000: cref = cr_id; break
        poly = make_parcel_polygon(lat, lng) if lat != 0 else []
        parcels.append(CadastralParcel(id=f"p{len(parcels)+1}", parcelNumber=pnum,
            gemarkung=str(ref.get("gemarkung","")) or "Unknown", flur=int(ref.get("flur",0)) if ref.get("flur") else 0,
            polygon=poly, status=status, owner=owner, area=round(2.0+(hash(pnum)%20)/10,1),
            contractRef=cref, linkedWEA=linked.name if linked else None, notes="Source: Document text"))

    # ── Layer 3: LLM fallback ──
    if not parcels:
        logger.info("No parcels from ALKIS/regex — LLM fallback")
        ctx = rag_context(doc_ids, "Flurstück Grundstück Parzelle Gemarkung land plot Grundbuch")
        try:
            result = llm_json(EXTRACTION_SYSTEM, f"""Extract land/parcel references.\n\nContext:\n{ctx}\n\nText:\n{full_text[:4000]}\n\nReturn JSON: [{{"parcelNumber":"12/4","gemarkung":"municipality","flur":0}}]\nReturn [] if none. Do NOT invent.""")
            if isinstance(result, list):
                for ref in result:
                    pnum = str(ref.get("parcelNumber",""))
                    if not pnum or pnum in seen: continue
                    seen.add(pnum); lat,lng = 0.0, 0.0
                    if project_center:
                        a = (2*math.pi*len(parcels))/max(len(result),1); lat = project_center[0]+0.004*math.cos(a); lng = project_center[1]+0.004*math.sin(a)
                    poly = make_parcel_polygon(lat, lng) if lat != 0 else []
                    parcels.append(CadastralParcel(id=f"p{len(parcels)+1}", parcelNumber=pnum,
                        gemarkung=str(ref.get("gemarkung","")) or "Unknown", flur=int(ref.get("flur",0)) if ref.get("flur") else 0,
                        polygon=poly, status="buffer", owner="", area=2.0, notes="Source: LLM"))
        except Exception as e: logger.warning(f"LLM parcel: {e}")

    alkis_n = sum(1 for p in parcels if p.notes and "ALKIS" in (p.notes or ""))
    logger.info(f"Parcels: {len(parcels)} total (ALKIS:{alkis_n}, text:{len(text_refs)}, llm:{len(parcels)-alkis_n-len(text_refs)})")
    return parcels


def generate_findings(doc_ids, sections):
    issues = [f"[{sec.title}] {row.label}: {row.value}" + (f" — {row.note}" if row.note else "")
        for sec in sections for row in sec.rows if row.ampel in ("red","yellow")]
    if not issues: return [Finding(domain="General", severity="green", text="No critical issues identified.")]
    prompt = f"""Generate action items.\n\nIssues:\n{chr(10).join(f'- {i}' for i in issues)}\n\nReturn JSON: [{{"domain":"Land Security/Permits/Economics/General","severity":"red/yellow/green","text":"recommendation"}}]"""
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict): result = result.get("findings", [])
        if isinstance(result, list):
            return [Finding(domain=str(f.get("domain","General")),
                severity=f.get("severity","yellow") if f.get("severity") in ("green","yellow","red") else "yellow",
                text=str(f.get("text",""))) for f in result if f.get("text")]
    except Exception as e: logger.error(f"Findings: {e}")
    return [Finding(domain="General", severity="yellow", text="Manual review required.")]


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/documents", response_model=DocumentListResponse)
async def list_documents():
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, filename, size_bytes, upload_date, status, category FROM ddiq_documents ORDER BY upload_date DESC")
    rows = cur.fetchall(); cur.close(); conn.close()
    return DocumentListResponse(documents=[DocumentOut(id=str(r["id"]), name=r["filename"],
        size=round(r["size_bytes"]/(1024*1024),2), uploadDate=r["upload_date"].isoformat()[:10],
        type=r["filename"].rsplit(".",1)[-1].upper() if "." in r["filename"] else "File",
        status=r["status"], category=r["category"]) for r in rows], total=len(rows))

@router.post("/documents/upload", response_model=UploadDocResponse)
async def upload_document(file: UploadFile = File(...), category: str = Form("Uncategorized"), session_id: Optional[str] = Form(None)):
    if not file.filename.lower().endswith(".pdf"): raise HTTPException(400, "Only PDF")
    fb = await file.read()
    if len(fb) > MAX_FILE_SIZE: raise HTTPException(400, "Too large")
    full_text, pages = extract_pdf_text(fb)
    if not full_text.strip(): raise HTTPException(400, "No text extracted")
    chunks = chunk_text(full_text)
    if not chunks: raise HTTPException(400, "No chunks")
    embs = embed_texts([c["text"] for c in chunks])
    conn = get_conn(); cur = conn.cursor(); did = str(uuid.uuid4())
    cur.execute("INSERT INTO ddiq_documents (id,filename,size_bytes,status,category,full_text,chunk_count,session_id) VALUES (%s,%s,%s,'analyzed',%s,%s,%s,%s)",
        (did, file.filename, len(fb), category, full_text, len(chunks), session_id))
    for c, e in zip(chunks, embs):
        cur.execute("INSERT INTO ddiq_doc_chunks (doc_id,chunk_idx,text,embedding) VALUES (%s,%s,%s,%s::vector)",
            (did, c["idx"], c["text"], "["+",".join(str(x) for x in e)+"]"))
    conn.commit(); cur.close(); conn.close()
    return UploadDocResponse(id=did, filename=file.filename, pages=pages, chunks=len(chunks), status="analyzed",
        message=f"{file.filename}: {pages} pages, {len(chunks)} chunks")

@router.post("/report/generate", response_model=GenerateReportResponse)
async def generate_report(req: GenerateReportRequest):
    if not req.document_ids: raise HTTPException(400, "No documents selected")
    t0 = time.time(); T = {}

    t = time.time()
    full_text = get_all_text_for_docs(req.document_ids)
    if not full_text.strip(): raise HTTPException(404, "No text")
    T["gather_s"] = round(time.time()-t, 2)

    conn = get_conn(); cur = conn.cursor()
    cur.execute(f"SELECT filename FROM ddiq_documents WHERE id::text IN ({','.join(['%s']*len(req.document_ids))})", tuple(req.document_ids))
    doc_names = [r[0] for r in cur.fetchall()]; cur.close(); conn.close()

    t = time.time()
    try: meta = llm_json(EXTRACTION_SYSTEM, f"""Extract metadata.\n\n{rag_context(req.document_ids,'project name company location',3)}\n\nReturn: {{"projectName":"name","preparedFor":"company"}}""")
    except Exception: meta = {}
    T["meta_s"] = round(time.time()-t, 2)
    pname = req.project_name or meta.get("projectName", "Wind Energy Project")
    pfor = req.prepared_for or meta.get("preparedFor", "Client")

    t = time.time()
    sections = [analyze_section(req.document_ids, s) for s in ["overview","land","permits","economics"]]
    T["sections_s"] = round(time.time()-t, 2)

    t = time.time()
    pc = geocode_project_location(sections)
    ploc = get_section_value(sections, "overview", "Location")
    logger.info(f"Center: {pc}, Location: {ploc}")
    T["geocode_s"] = round(time.time()-t, 2)

    t = time.time(); weas = extract_wea_statuses(req.document_ids, full_text, sections, pc); T["wea_s"] = round(time.time()-t, 2)
    t = time.time(); infra = extract_infrastructure(req.document_ids, sections, pc); T["infra_s"] = round(time.time()-t, 2)

    # ── 13-Step Cadastral Pipeline ────────────────────────────────────────
    t = time.time()
    pipeline = CadastralPipeline(
        alkis_query_fn=alkis_query_parcels,
        rag_context_fn=rag_context,
        llm_json_fn=llm_json,
        detect_bundesland_fn=detect_bundesland,
    )
    pipeline_result = pipeline.run(
        doc_ids=req.document_ids,
        full_text=full_text,
        wea_statuses=weas,
        project_area_polygon=None,  # Will auto-generate from WEA hull
        location=ploc,
        project_center=pc,
    )
    T["cadastral_pipeline_s"] = round(time.time()-t, 2)

    # Convert pipeline ClassifiedParcels to legacy CadastralParcel format for backwards compat
    parcels = []
    for cp in pipeline_result.classified_parcels:
        status_map = {
            ParcelClassification.SECURED: "secured",
            ParcelClassification.NOT_SECURED: "not_secured",
            ParcelClassification.UNCERTAIN: "uncertain",
        }
        parcels.append(CadastralParcel(
            id=cp.id,
            parcelNumber=cp.parcel_number,
            gemarkung=cp.gemarkung,
            flur=cp.flur,
            polygon=cp.polygon,
            status=status_map.get(cp.classification, "not_secured"),
            owner=cp.owner,
            area=cp.area_ha if cp.area_ha else round(cp.area_m2 / 10000, 2) if cp.area_m2 else 2.0,
            contractRef=cp.matched_contract_ref,
            linkedWEA=cp.linked_wea,
            notes=cp.notes or cp.classification_reason,
            polygonSource=cp.polygon_source,
            confidence=cp.confidence,
            normalizedId=cp.normalized_id,
        ))

    # If pipeline found no parcels, fall back to legacy build_parcels
    if not parcels:
        t = time.time(); legacy_parcels = build_parcels(req.document_ids, full_text, weas, pc, ploc); T["parcels_legacy_s"] = round(time.time()-t, 2)
        parcels = legacy_parcels

    t = time.time(); findings = generate_findings(req.document_ids, sections); T["findings_s"] = round(time.time()-t, 2)

    lats = [w.lat for w in weas if w.lat != 0]; lngs = [w.lng for w in weas if w.lng != 0]
    center = {"lat": sum(lats)/len(lats) if lats else (pc[0] if pc else 53.0),
              "lng": sum(lngs)/len(lngs) if lngs else (pc[1] if pc else 9.0)}

    from datetime import datetime

    # Prepare clearance zones for report
    clearance_data = [
        {"wea_name": z.wea_name, "center_lat": z.center_lat, "center_lng": z.center_lng,
         "radius_m": z.radius_m, "polygon": z.polygon}
        for z in pipeline_result.clearance_zones
    ] if pipeline_result.clearance_zones else None

    # Prepare project area for report
    pa = pipeline_result.project_area
    project_area_data = {
        "name": pa.name, "polygon": pa.polygon,
        "centroid_lat": pa.centroid_lat, "centroid_lng": pa.centroid_lng,
        "area_km2": pa.area_km2, "source": pa.source,
    } if pa and pa.polygon else None

    report = DDiQReportData(projectName=pname, preparedBy="LAI Due Diligence System", preparedFor=pfor,
        date=datetime.now().strftime("%d %B %Y"), projectCenter=center, sections=sections,
        weaStatuses=weas, infrastructure=infra, parcels=parcels, findings=findings,
        analyzedDocuments=doc_names,
        projectArea=project_area_data,
        clearanceZones=clearance_data,
        validation=pipeline_result.validation.dict() if pipeline_result.validation else None,
        geojson=pipeline_result.geojson if pipeline_result.geojson else None,
    )

    rid = str(uuid.uuid4()); conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO ddiq_reports (id,project_name,document_ids,preset,report_data) VALUES (%s,%s,%s::uuid[],%s,%s)",
        (rid, pname, req.document_ids, req.preset, json.dumps(report.dict())))

    # Persist project area first (parent of contracts and parcels)
    if pa and pa.polygon:
        cur.execute("""INSERT INTO ddiq_project_areas (name, polygon, centroid_lat, centroid_lng, area_km2, source, report_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (pa.name, json.dumps(pa.polygon), pa.centroid_lat, pa.centroid_lng, pa.area_km2, pa.source, rid))

    # Persist contracts (must come before classified_parcels since matched_contract_id references them)
    for contract in pipeline_result.contracts:
        cur.execute("""INSERT INTO ddiq_contracts (id, contract_ref, contract_type, contracting_entity, raw_text_excerpt, report_id)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (contract.contract_id, contract.contract_ref, contract.contract_type,
             contract.contracting_entity, contract.text_excerpt[:500], rid))
        for pref in contract.referenced_parcels:
            cur.execute("INSERT INTO ddiq_contract_parcels (contract_id, parcel_identifier) VALUES (%s,%s)",
                (contract.contract_id, pref))

    # Persist classified parcels last
    for cp in pipeline_result.classified_parcels:
        cur.execute("""INSERT INTO ddiq_classified_parcels
            (report_id, parcel_number, gemarkung, flur, normalized_id, polygon, polygon_source,
             classification, color, confidence, matched_contract_id, classification_reason, area_ha, owner, linked_wea)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (rid, cp.parcel_number, cp.gemarkung, cp.flur, cp.normalized_id,
             json.dumps(cp.polygon), cp.polygon_source, cp.classification.value,
             cp.color, cp.confidence, cp.matched_contract_id, cp.classification_reason,
             cp.area_ha, cp.owner, cp.linked_wea))

    conn.commit(); cur.close(); conn.close()
    T["total_s"] = round(time.time()-t0, 2)
    return GenerateReportResponse(report_id=rid, report=report, timings=T)

@router.get("/report/{report_id}")
async def get_report(report_id: str):
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM ddiq_reports WHERE id = %s", (report_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: raise HTTPException(404, "Not found")
    return {"report_id": str(row["id"]), "created_at": row["created_at"].isoformat(),
            "project_name": row["project_name"], "report": row["report_data"]}


@router.get("/report/{report_id}/geojson")
async def get_report_geojson(report_id: str):
    """Return GeoJSON FeatureCollection for GIS import (QGIS, ArcGIS, MapBox, etc.)."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT report_data FROM ddiq_reports WHERE id = %s", (report_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: raise HTTPException(404, "Report not found")

    report_data = row["report_data"]
    if isinstance(report_data, str):
        report_data = json.loads(report_data)

    # Return pre-computed GeoJSON if available
    if report_data.get("geojson"):
        return JSONResponse(
            content=report_data["geojson"],
            headers={
                "Content-Type": "application/geo+json",
                "Content-Disposition": f'attachment; filename="lai_report_{report_id[:8]}.geojson"',
            },
        )

    # Fallback: build GeoJSON from parcels in report_data
    features = []
    for parcel in report_data.get("parcels", []):
        polygon = parcel.get("polygon", [])
        if not polygon:
            continue
        # Convert [lat,lng] to GeoJSON [lng,lat]
        ring = [[pt[1], pt[0]] for pt in polygon]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "parcelNumber": parcel.get("parcelNumber", ""),
                "status": parcel.get("status", ""),
                "gemarkung": parcel.get("gemarkung", ""),
                "flur": parcel.get("flur", 0),
                "owner": parcel.get("owner", ""),
                "area_ha": parcel.get("area", 0),
                "polygonSource": parcel.get("polygonSource", "estimated"),
            },
        })

    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"crs": "EPSG:4326", "generated_by": "LAI v1"},
    }
    return JSONResponse(
        content=geojson,
        headers={
            "Content-Type": "application/geo+json",
            "Content-Disposition": f'attachment; filename="lai_report_{report_id[:8]}.geojson"',
        },
    )


@router.get("/report/{report_id}/validate")
async def validate_report(report_id: str):
    """Run validation checks on a generated report (Step 13)."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT report_data FROM ddiq_reports WHERE id = %s", (report_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: raise HTTPException(404, "Report not found")

    report_data = row["report_data"]
    if isinstance(report_data, str):
        report_data = json.loads(report_data)

    # Return pre-computed validation if available
    if report_data.get("validation"):
        return report_data["validation"]

    return {"message": "Validation data not available for this report. Re-generate the report to include validation."}


class ProjectAreaRequest(BaseModel):
    polygon: list[list[float]]               # [[lat, lng], ...]
    name: Optional[str] = "User-Defined Area"

class ProjectAreaResponse(BaseModel):
    id: str
    name: str
    polygon: list[list[float]]
    centroid_lat: float
    centroid_lng: float
    area_km2: float
    source: str


@router.post("/project-area", response_model=ProjectAreaResponse)
async def create_project_area(req: ProjectAreaRequest):
    """Define a project area polygon (Step 1 of Output Map)."""
    if len(req.polygon) < 3:
        raise HTTPException(400, "Polygon must have at least 3 points")

    from cadastral_pipeline import compute_centroid, polygon_area_km2
    centroid = compute_centroid(req.polygon)
    area = polygon_area_km2(req.polygon)

    pa_id = str(uuid.uuid4())
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO ddiq_project_areas (id, name, polygon, centroid_lat, centroid_lng, area_km2, source)
        VALUES (%s, %s, %s, %s, %s, %s, 'user_drawn')""",
        (pa_id, req.name, json.dumps(req.polygon), centroid[0], centroid[1], area))
    conn.commit(); cur.close(); conn.close()

    return ProjectAreaResponse(
        id=pa_id, name=req.name, polygon=req.polygon,
        centroid_lat=centroid[0], centroid_lng=centroid[1],
        area_km2=area, source="user_drawn",
    )


@router.get("/config/map-tiles")
async def get_map_tiles():
    """Return available map tile layers for the frontend."""
    return {
        "layers": [
            {
                "id": "osm",
                "name": "OpenStreetMap",
                "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                "attribution": "OpenStreetMap contributors",
                "default": True,
            },
            {
                "id": "satellite",
                "name": "Satellite (Esri)",
                "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                "attribution": "Esri, Maxar, Earthstar Geographics",
                "default": False,
            },
            {
                "id": "topo",
                "name": "TopPlusOpen (BKG Germany)",
                "url": "https://sgx.geodatenzentrum.de/wmts_topplus_open/tile/1.0.0/web/default/WEBMERCATOR/{z}/{y}/{x}.png",
                "attribution": "BKG - Bundesamt fuer Kartographie und Geodaesie",
                "default": False,
            },
        ],
    }

@router.on_event("startup")
async def startup(): init_db()