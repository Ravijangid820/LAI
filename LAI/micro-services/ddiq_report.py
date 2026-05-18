"""
DDiQ Report Generation Module — v4 (LAI v1 Production)
─────────────────────────────────────────────────────────────────────
13-step cadastral classification pipeline per Output Map spec.
ALKIS WFS for all Bundeslaender + contract-to-parcel matching +
GeoJSON output + clearance zones + validation.

Mount: from ddiq_report import router as ddiq_router
       app.include_router(ddiq_router, prefix="/ddiq")
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os, re, json, time, uuid, logging, math, hashlib
import requests
import psycopg2
import psycopg2.extras

# Auth — AUTH_PLAN §4.4: every protected route depends on
# ``get_current_user``. The dep is imported from the microservice's
# shared ``auth_dep`` module so api.py and this router resolve to the
# same TokenIssuer/secret instance.
from auth_dep import get_current_user
from lai.common.auth import CurrentUser

# Shared LLM client — see `_llm_*` helpers below. Importing at module
# level so a single httpx connection pool is reused across all DDiQ
# extraction passes within a worker process.
from lai.common.exceptions import LlmError
from lai.common.llm import (
    ChatMessage,
    LlmConfig,
    SyncLlmClient,
    salvage_json,
)

# Geocoding plausibility-gate helpers — used by ``geocode_address`` to
# reject Nominatim results that fall outside the named Bundesland's bbox.
# See ``bundesland_bbox.py`` for the data source + the why.
from bundesland_bbox import (
    bundesland_from_coords,
    has_bbox,
    is_in_bundesland,
)

# Deterministic reconciler for cross-source value disagreements
# (total_capacity_mw, turbine_count, bundesland, …). See ``_reconcile.py``
# for precedence rules + divergence logging.
from _reconcile import Candidate, reconcile_categorical, reconcile_numeric

# Output guardrail: post-LLM cleanup pass that strips defensive-AI
# paragraphs, removes hedge phrases, and flags mixed-language rows.
# See ``_guardrail.py`` for the sourced pattern list.
from _guardrail import apply_to_findings, apply_to_rows

# Shared PDF extractor (PyMuPDF + Tesseract OCR fallback) and chunker
# (German-legal-aware sentence splitter). One module-level instance of
# each so callers reuse the lazy ``fitz`` / ``pytesseract`` / ``PIL``
# imports and the per-instance pydantic config, instead of paying the
# cost on every upload.
from lai.common.chunk import Chunk, Chunker, ChunkerConfig
from lai.common.pdf import PdfExtractor, PdfExtractorConfig

_PDF_EXTRACTOR = PdfExtractor(
    PdfExtractorConfig(
        # Keep the existing DDiQ defaults: OCR fallback in DE+EN when an
        # embedded page produces less than 50 chars of usable text. Lazy
        # imports of fitz / pytesseract / PIL so this module loads even
        # without Tesseract on the host (the OCR path raises a typed
        # exception in that case).
        ocr_languages="deu+eng",
        min_chars_per_page=50,
        ocr_zoom=2.0,
    )
)
_CHUNKER = Chunker(
    ChunkerConfig(
        # Sized to match the historical hand-rolled chunker
        # (chunk_size=1000, overlap=200, no min/max enforced). The new
        # chunker is sentence-aware so the boundaries land cleaner; the
        # downstream INSERT into ``ddiq_doc_chunks`` is shape-compatible.
        target_chars=1000,
        max_chars=1500,
        min_chars=100,
        overlap_chars=200,
    )
)
import numpy as np
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
-- pgvector is required for ddiq_doc_chunks.embedding (4096-dim Qwen3 vectors).
-- Idempotent: no-op when the extension is already enabled. The whole
-- SCHEMA_SQL runs in one transaction, so without this every CREATE TABLE
-- below fails when the DB is fresh.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS ddiq_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), filename TEXT NOT NULL,
    size_bytes BIGINT DEFAULT 0, upload_date TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'pending', category TEXT DEFAULT 'Uncategorized',
    full_text TEXT, chunk_count INT DEFAULT 0, session_id TEXT);
CREATE TABLE IF NOT EXISTS ddiq_doc_chunks (
    id SERIAL PRIMARY KEY, doc_id UUID REFERENCES ddiq_documents(id) ON DELETE CASCADE,
    chunk_idx INT NOT NULL, text TEXT NOT NULL, embedding vector(4096), UNIQUE(doc_id, chunk_idx));
    -- Qwen3-Embedding-8B returns 4096-dim vectors. If you swap to a different
    -- embedding model (1024-dim sentence-transformers / 1536-dim ada / etc.),
    -- update this column and drop ddiq_doc_chunks first.
CREATE TABLE IF NOT EXISTS ddiq_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), created_at TIMESTAMPTZ DEFAULT NOW(),
    project_name TEXT, document_ids UUID[], preset TEXT,
    report_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Status fields for the async job pattern. NULL/legacy rows count as "done".
    status TEXT DEFAULT 'done',          -- queued | running | done | failed
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    progress_step TEXT,                  -- short label for UI ("classifying", etc.)
    progress_percent DOUBLE PRECISION DEFAULT 0.0,
    error TEXT,
    -- Stable hash of (sorted doc_ids, preset, project_name) — lets us dedup
    -- repeat requests and return the cached/in-flight report instead of
    -- recomputing the 30-60 min pipeline.
    request_fingerprint TEXT);
-- Forward-compat: add the columns if the table already exists.
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'done';
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS progress_step TEXT;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS progress_percent DOUBLE PRECISION DEFAULT 0.0;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE ddiq_reports ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;
-- Track A item 6: the fingerprint index is now UNIQUE so two concurrent
-- /report/generate calls with identical (doc_ids, preset, project_name)
-- can't both write a row. The old (non-unique) index is dropped first
-- because ``CREATE UNIQUE INDEX IF NOT EXISTS`` with the same name would
-- silently no-op if the existing index isn't unique. New name avoids
-- collision with any in-flight code referencing the old one.
DROP INDEX IF EXISTS ddiq_reports_fingerprint_idx;
CREATE UNIQUE INDEX IF NOT EXISTS ddiq_reports_fingerprint_uniq_idx
    ON ddiq_reports(request_fingerprint) WHERE request_fingerprint IS NOT NULL;
CREATE TABLE IF NOT EXISTS ddiq_geocode_cache (
    address TEXT PRIMARY KEY, lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL, cached_at TIMESTAMPTZ DEFAULT NOW(),
    -- TTL on the geocode cache. Rows are honored only while
    -- ``expires_at > NOW()``; pre-TTL rows (NULL ``expires_at``) are
    -- treated as expired so any wrong-state Nominatim answers cached
    -- before the bbox gate landed get re-geocoded once.
    expires_at TIMESTAMPTZ);
ALTER TABLE ddiq_geocode_cache ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS ddiq_parcel_cache (
    coord_key TEXT PRIMARY KEY, parcel_data JSONB NOT NULL, cached_at TIMESTAMPTZ DEFAULT NOW(),
    -- TTL on the parcel cache. Cadastral data updates quarterly at most
    -- but 30 days is conservative and matches the geocode-cache pattern
    -- (Track A item 3). NULL ``expires_at`` is treated as expired so any
    -- pre-TTL row is refetched once.
    expires_at TIMESTAMPTZ);
ALTER TABLE ddiq_parcel_cache ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
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


# ─── Connection pool ───────────────────────────────────────────────────────
# Every endpoint used to call psycopg2.connect() directly, paying the TCP +
# auth handshake cost (~5-50ms) per request. /report/generate opened ~20+
# connections in a single call. A single shared ThreadedConnectionPool
# eliminates the cost; a thin _PooledConn wrapper makes existing call sites
# (`conn.close()`) return the connection to the pool instead of really
# closing it, so we don't have to refactor every endpoint.

from psycopg2.pool import ThreadedConnectionPool

_pg_pool: Optional[ThreadedConnectionPool] = None


class _PooledConn:
    """Proxy a psycopg2 connection from the pool. ``close()`` returns it
    to the pool; everything else delegates to the underlying connection
    so existing code continues to work."""
    def __init__(self, conn, pool: ThreadedConnectionPool):
        self.__dict__["_conn"] = conn
        self.__dict__["_pool"] = pool

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        # Mirror writes onto the underlying connection.
        setattr(self._conn, name, value)

    def close(self):
        if self._conn is None or self._pool is None:
            return
        try:
            # If the txn is in a bad state, return aborted so the pool can
            # reset/discard the connection cleanly.
            if not self._conn.closed:
                self._pool.putconn(self._conn)
        finally:
            self.__dict__["_conn"] = None
            self.__dict__["_pool"] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Mirror psycopg2 connection-as-context-manager semantics: commit
        # on clean exit, rollback on exception, then return to pool.
        try:
            if exc is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self.close()


def init_pool() -> None:
    global _pg_pool
    if _pg_pool is not None:
        return
    _pg_pool = ThreadedConnectionPool(
        minconn=int(os.getenv("DB_POOL_MIN", "2")),
        maxconn=int(os.getenv("DB_POOL_MAX", "20")),
        **DB_CONFIG,
    )
    logger.info(f"DDiQ DB pool: {_pg_pool.minconn}/{_pg_pool.maxconn} connections")


def close_pool() -> None:
    global _pg_pool
    if _pg_pool is not None:
        _pg_pool.closeall()
        _pg_pool = None


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
    # Technical attributes (P1 #7) — pulled from Erläuterungsbericht / BImSchG
    # permit. Hub height drives the 10H clearance for Bayern/Hessen.
    hub_height_m: Optional[float] = None
    rotor_diameter_m: Optional[float] = None
    rated_power_kw: Optional[float] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    # Status code per BImSchG procedure: errichtet | genehmigt | geplant | abgenommen
    status_code: Optional[str] = None
    permit_ref: Optional[str] = None      # Aktenzeichen of the BImSchG Bescheid
    warranty_end: Optional[str] = None    # ISO date or free text
class InfraPoint(BaseModel):
    name: str; type: str; lat: float; lng: float
class CadastralParcel(BaseModel):
    id: str; parcelNumber: str; gemarkung: str; flur: int; polygon: list[list[float]]
    status: str; owner: str; area: float; contractRef: Optional[str] = None
    linkedWEA: Optional[str] = None; notes: Optional[str] = None
    polygonSource: str = "estimated"  # "alkis_wfs", "document", "estimated"
    confidence: float = 0.0
    normalizedId: str = ""

# ─── Evidence + quantification (P0 #1, #4) ──────────────────────────────────
# Every Finding/TimelineEntry/Grundbuch/Rückbau check carries Evidence so a
# lawyer can verify the LLM's claim by jumping to the right page of the
# right document. Without this, output is unverifiable.
class Evidence(BaseModel):
    doc_id: Optional[str] = None
    doc_filename: Optional[str] = None
    page: Optional[int] = None        # currently no per-page chunking — left None
    excerpt: str = ""                 # short snippet (≤300 chars) from the chunk
    clause: Optional[str] = None      # e.g. "§4 Abs. 1 BImSchG", "Pachtvertrag §7"

class Quantification(BaseModel):
    """Materiality scorecard on a finding. Lawyer DD ranks by impact, not text."""
    mw_affected: Optional[float] = None
    eur_impact_estimate: Optional[float] = None
    days_until_deadline: Optional[int] = None
    rationale: Optional[str] = None   # how the LLM arrived at these numbers

class Finding(BaseModel):
    domain: str
    severity: str
    text: str
    # P0 additions
    evidence: list[Evidence] = []
    quantification: Optional[Quantification] = None
    legal_basis: Optional[str] = None        # "§4 BImSchG" / "§35 Abs. 1 Nr. 5 BauGB" / "§44 BNatSchG"
    recommended_action: Optional[str] = None
    # section | cross_document | deadline | grundbuch | rueckbau | regulatory
    kind: str = "section"

# ─── Timeline (P0 #2) ───────────────────────────────────────────────────────
class TimelineEntry(BaseModel):
    """Date-bound milestone or deadline pulled from the documents.
    Surfaces 'permit valid until 2027-06-30, renewal 6 months prior' style
    findings that pure-RAG Q&A misses."""
    kind: str  # permit_expiry | lease_term_end | renewal_deadline | warranty_end | bond_validity | construction_milestone | objection_window | other
    date: str  # ISO YYYY-MM-DD when known, free-text fallback otherwise
    description: str
    legal_basis: Optional[str] = None       # e.g. "§70 VwGO Widerspruchsfrist"
    evidence: list[Evidence] = []
    days_from_now: Optional[int] = None
    urgency: Optional[str] = None           # expired | urgent | soon | future

# ─── Grundbuch consistency (P1 #6) ──────────────────────────────────────────
class GrundbuchCheck(BaseModel):
    """Per-parcel: does Pachtvertrag-lessor match the registered Eigentümer?
    What encumbrances (Belastungen) are on the title? Without this, a parcel
    can show 'secured' even if the contract is signed by someone with no
    legal title."""
    parcel_id: str                          # normalized: gemarkung:flur:parcel_number
    registered_owner: Optional[str] = None
    lessor_name: Optional[str] = None
    owner_match: Optional[bool] = None      # None when undeterminable from documents
    match_confidence: float = 0.0
    encumbrances: list[str] = []            # "Wegerecht zugunsten Gemeinde", "§24 BauGB Vorkaufsrecht", "Hypothek 250k €"
    evidence: list[Evidence] = []
    note: Optional[str] = None

# ─── Rückbaubürgschaft (P1 #9) ──────────────────────────────────────────────
class RueckbauBond(BaseModel):
    """§35 Abs. 5 BauGB requires a decommissioning bond. Recurring DD red flag.
    Pulled out of the BImSchG-Bescheid Auflagen or a separate Bürgschaftsurkunde."""
    amount_eur: Optional[float] = None
    provider: Optional[str] = None          # bank, insurer, parent guarantor
    beneficiary: Optional[str] = None       # usually the Standortgemeinde
    valid_until: Optional[str] = None       # ISO date
    instrument_type: Optional[str] = None   # "Bürgschaft" | "Hinterlegung" | "Konzernbürgschaft"
    sufficient: Optional[bool] = None       # vs. expected Rückbaukosten (LLM's read)
    evidence: list[Evidence] = []
    note: Optional[str] = None

class DDiQReportData(BaseModel):
    projectName: str; preparedBy: str; preparedFor: str; date: str; projectCenter: dict
    # Defaults to empty so we can construct the report at the start of the
    # pipeline and fill fields in as each phase completes — supports
    # incremental persistence, so a mid-pipeline crash still leaves a
    # usable report instead of an empty placeholder row.
    sections: list[AusgabeblattSection] = []
    weaStatuses: list[WEAStatus] = []
    infrastructure: list[InfraPoint] = []
    parcels: list[CadastralParcel] = []
    findings: list[Finding] = []
    analyzedDocuments: list[str] = []
    projectArea: Optional[dict] = None          # Project area polygon data
    clearanceZones: Optional[list[dict]] = None  # WEA clearance zone circles
    validation: Optional[dict] = None            # Validation report
    geojson: Optional[dict] = None               # GeoJSON FeatureCollection
    # P0/P1 additions
    timeline: list[TimelineEntry] = []
    crossDocFindings: list[Finding] = []         # inter-document inconsistencies
    grundbuchChecks: list[GrundbuchCheck] = []
    rueckbauBond: Optional[RueckbauBond] = None
    documentMap: list[dict] = []                 # [{"id": uuid, "filename": str}] for evidence rendering
    # ── Reconciled cross-source values (Track A item 4) ────────────────
    # Single source of truth for fields the pipeline historically
    # disagreed about across sections. ``None`` / 0 means no candidate
    # source returned a value; downstream code treats those as "unknown"
    # rather than substituting a fallback. See ``_reconcile.py`` and the
    # reconciliation block in ``_generate_report_core``.
    turbineCount: int = 0
    bundesland: Optional[str] = None             # lowercase, e.g. "niedersachsen"
class GenerateReportRequest(BaseModel):
    document_ids: list[str]; preset: str = "full"
    project_name: Optional[str] = None; prepared_for: Optional[str] = None
class GenerateReportResponse(BaseModel):
    report_id: str; report: DDiQReportData; timings: dict


# ═══════════════════════════════════════════════════════════════════════════════
# CORE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_conn():
    """Return a connection from the pool (lazy-init the pool on first use).

    Call ``conn.close()`` as before — that returns it to the pool, not
    actually closes it. Use ``with get_conn() as conn:`` to also pick up
    auto-commit-on-success / rollback-on-exception."""
    if _pg_pool is None:
        init_pool()
    return _PooledConn(_pg_pool.getconn(), _pg_pool)

def clean_value(val, fallback: str = "Not specified in documents") -> str:
    s = str(val).strip()
    return fallback if s.lower() in ("null", "none", "n/a", "na", "nil", "undefined", "") else s

def extract_pdf_text(file_bytes: bytes) -> tuple[str, int]:
    """Extract joined text + page count from a PDF.

    Thin shim around :class:`lai.common.pdf.PdfExtractor` that preserves
    the legacy ``(text, page_count)`` tuple shape so the upload route
    needs no other changes. The shared extractor adds:

      - Structured ``PdfExtractError`` / ``PdfOcrUnavailableError`` on
        unrecoverable input (was: raw PyMuPDF exceptions).
      - Provenance tagging per page (embedded vs OCR vs empty) — we
        discard the tag here since the legacy return type is bare text;
        promoting to a structured caller is a future refactor.
      - Quality gate via ``min_page_text_chars`` — same 50-char threshold
        the legacy code used.
      - Bounded ``max_pages`` so a 10k-page PDF can't OOM the worker.
    """
    result = _PDF_EXTRACTOR.extract_bytes(file_bytes)
    return result.text, result.page_count


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[dict]:
    """Greedy sentence-aware chunking; returns the legacy [{"idx", "text"}] shape.

    Thin shim around :class:`lai.common.chunk.Chunker`. The ``chunk_size``
    / ``overlap`` arguments are accepted for backwards compatibility but
    are IGNORED — the module-level ``_CHUNKER`` is pre-configured with
    matching values (``target_chars=1000``, ``overlap_chars=200``). If
    a caller ever passes different values, we'd need to either propagate
    them as a one-off Chunker or document the override; no current
    caller does so.

    The downstream INSERT into ``ddiq_doc_chunks`` uses ``["idx"]`` and
    ``["text"]``, so the return shape is preserved exactly.
    """
    # Pre-existing callers pass only the defaults; warn loudly if not.
    if chunk_size != 1000 or overlap != 200:
        logger.warning(
            "chunk_text called with non-default args (chunk_size=%s, overlap=%s); "
            "the shared Chunker config is fixed at 1000/200 — values ignored.",
            chunk_size, overlap,
        )
    return [
        {"idx": c.index, "text": c.text}
        for c in _CHUNKER.chunk(text)
    ]

# Embedding service shape:
#   - The current LAI runtime exposes vLLM's OpenAI-compatible API at
#     /v1/embeddings (POST {model, input: [...]} → {data: [{embedding}, ...]})
#   - Older HuggingFace TEI servers used /embed with {inputs: ...}
# We try OpenAI-shape first; on 404 fall back to TEI-shape so this code
# works regardless of which embedding server the host is running.
def _embed_via_openai(texts: list[str], timeout: int = 120) -> list[list[float]]:
    resp = requests.post(
        f"{EMBEDDING_URL}/v1/embeddings",
        json={"model": "Qwen/Qwen3-Embedding-8B", "input": texts},
        timeout=timeout,
    )
    resp.raise_for_status()
    return [item["embedding"] for item in resp.json().get("data", [])]


def _embed_via_tei(texts: list[str], timeout: int = 120) -> list[list[float]]:
    resp = requests.post(
        f"{EMBEDDING_URL}/embed",
        json={"inputs": texts},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def embed_texts(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    all_emb: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            all_emb.extend(_embed_via_openai(batch))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                all_emb.extend(_embed_via_tei(batch))
            else:
                raise
    return all_emb


def embed_single(text: str) -> list[float]:
    return embed_texts([text])[0]

def _assert_owns_documents(doc_ids, user_id) -> None:
    """Raise 404 if ``user_id`` does not own every document in ``doc_ids``.

    Single ownership check used at the boundary of every endpoint that
    takes a list of doc IDs. Returning 404 (not 403) matches AUTH_PLAN
    §6 rule 4 — never leak the existence of another tenant's row.
    """
    if not doc_ids:
        return
    conn = get_conn(); cur = conn.cursor()
    try:
        ph = ",".join(["%s"] * len(doc_ids))
        cur.execute(
            f"SELECT COUNT(*) FROM ddiq_documents "
            f"WHERE id::text IN ({ph}) AND user_id = %s",
            (*doc_ids, str(user_id)),
        )
        n = cur.fetchone()[0]
    finally:
        cur.close(); conn.close()
    if int(n) != len(set(doc_ids)):
        raise HTTPException(404, "Document not found")


def search_doc_chunks(doc_ids, query_embedding, top_k=15, user_id=None):
    """Pgvector search over ``doc_ids`` chunks, scoped to ``user_id``.

    When ``user_id`` is supplied (every protected route does), the join
    filter also enforces tenant isolation at the SQL layer — even if a
    caller bypassed :func:`_assert_owns_documents`, no chunks belonging
    to another user can leak.
    """
    if not doc_ids: return []
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    ph = ",".join(["%s"] * len(doc_ids))
    user_clause = " AND d.user_id = %s" if user_id is not None else ""
    sql = f"""SELECT c.text, c.doc_id, d.filename,
              1-(c.embedding<=>%s::vector) AS similarity
              FROM ddiq_doc_chunks c JOIN ddiq_documents d ON d.id=c.doc_id
              WHERE c.doc_id::text IN ({ph})
              AND c.embedding IS NOT NULL{user_clause}
              ORDER BY c.embedding<=>%s::vector LIMIT %s"""
    params = (emb_str, *doc_ids)
    if user_id is not None:
        params = (*params, str(user_id))
    params = (*params, emb_str, top_k)
    cur.execute(sql, params)
    rows = cur.fetchall(); cur.close(); conn.close(); return [dict(r) for r in rows]

def get_all_text_for_docs(doc_ids, user_id=None):
    """Concatenate ``full_text`` from the given documents, scoped to ``user_id``."""
    conn = get_conn(); cur = conn.cursor()
    ph = ",".join(["%s"] * len(doc_ids))
    if user_id is not None:
        cur.execute(
            f"SELECT full_text FROM ddiq_documents "
            f"WHERE id::text IN ({ph}) AND user_id = %s",
            (*doc_ids, str(user_id)),
        )
    else:
        cur.execute(
            f"SELECT full_text FROM ddiq_documents WHERE id::text IN ({ph})",
            tuple(doc_ids),
        )
    texts = [row[0] for row in cur.fetchall() if row[0]]; cur.close(); conn.close()
    return "\n\n---\n\n".join(texts)

def rerank(query, chunks, top_k=5):
    texts = [c["text"] for c in chunks]
    try:
        resp = requests.post(f"{RERANKER_URL}/rerank", json={"query": query, "texts": texts, "truncate": True}, timeout=30)
        resp.raise_for_status(); ranked = sorted(resp.json(), key=lambda x: x["score"], reverse=True)[:top_k]
        return [chunks[item["index"]] for item in ranked]
    except Exception: return chunks[:top_k]

# ── LLM client (shared) ─────────────────────────────────────────────────────
# Single module-level SyncLlmClient. Each uvicorn worker (process) instantiates
# its own client and reuses one httpx connection pool across all extraction
# passes; the underlying httpx.Client is thread-safe.
#
# Config is built from the legacy DDiQ env vars (LLM_URL / LLM_MODEL) rather
# than the LAI_LLM_* prefix lai.common defaults to, so this drop-in does not
# require any change to docker-compose.yml. A future cleanup can switch the
# compose to LAI_LLM_BASE_URL / LAI_LLM_MODEL and drop the explicit kwargs.
_LLM_CONFIG = LlmConfig(base_url=LLM_URL, model=LLM_MODEL)
_LLM_CLIENT = SyncLlmClient(_LLM_CONFIG)


def llm_call(system, user, temperature=0.1, max_tokens=2048):
    """Single-shot chat completion. Returns the stripped string content.

    Backed by :class:`lai.common.llm.SyncLlmClient`, which adds retry with
    exponential backoff, server-side ``<think>`` stripping, structured
    logging, and Prometheus metrics over what the legacy hand-rolled
    ``requests.post`` provided. The signature is preserved exactly so the
    11 in-module call sites — plus the ``llm_json_fn`` callback handed to
    :class:`CadastralPipeline` — keep working without changes.

    Returns ``""`` on retry-exhausted / transport / invalid-response
    failure, matching the legacy behaviour of returning an empty string
    on null content. The caller's JSON parse will then take its own
    error path instead of crashing the pipeline.
    """
    try:
        return _LLM_CLIENT.generate(
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except LlmError as exc:
        logger.warning(f"llm_call failed ({type(exc).__name__}): {exc}")
        return ""


def llm_json(system, user, temperature=0.0):
    """Two-shot JSON-structured completion. Returns ``dict`` / ``list`` or ``{}``.

    Strategy:
      1. Call the LLM, strip code fences, ``json.loads``.
      2. On parse failure, run the salvage path
         (:func:`lai.common.llm.salvage_json`) which extracts the first
         balanced JSON substring with full string-context awareness.
      3. On second parse failure, retry once with a strengthened
         instruction (mirrors the legacy two-shot behaviour).
      4. If everything fails, return ``{}`` rather than raising — the
         legacy uncaught :class:`json.JSONDecodeError` on the second
         attempt would crash the entire pipeline mid-report.
    """
    def _attempt(sys_prompt, user_prompt):
        raw = llm_call(sys_prompt, user_prompt, temperature, max_tokens=4096)
        if not raw:
            return None
        # Strip ```json fences before parse; salvage_json handles them
        # too, but doing it here keeps the fast path cheap.
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return json.loads(salvage_json(raw))
            except (json.JSONDecodeError, ValueError):
                return None

    parsed = _attempt(system, user)
    if parsed is not None:
        return parsed

    parsed = _attempt(system + "\n\nCRITICAL: Return ONLY valid JSON.", user)
    if parsed is not None:
        return parsed

    logger.warning("llm_json: both attempts failed to produce valid JSON; returning {}")
    return {}

def rag_context(doc_ids, question, top_k=5):
    emb = embed_single(question); chunks = search_doc_chunks(doc_ids, emb, top_k=20)
    if not chunks: return "(No relevant content found)"
    reranked = rerank(question, chunks, top_k=top_k)
    return "\n\n".join([f"[Doc: {c.get('filename','?')}]\n{c['text'][:800]}" for c in reranked])


def rag_context_with_meta(doc_ids, question, top_k=5):
    """Same retrieval as rag_context, but also returns the chunk metadata
    so callers can attach Evidence pointers ({doc_id, doc_filename,
    excerpt}) to whatever facts the LLM extracts. Format mirrors
    rag_context with a [#1], [#2]... numbering so the LLM can cite
    chunks back by index, which we then resolve to Evidence."""
    emb = embed_single(question); chunks = search_doc_chunks(doc_ids, emb, top_k=20)
    if not chunks: return "(No relevant content found)", []
    reranked = rerank(question, chunks, top_k=top_k)
    parts = []
    for i, c in enumerate(reranked, 1):
        parts.append(f"[#{i} | Doc: {c.get('filename','?')}]\n{c['text'][:800]}")
    return "\n\n".join(parts), reranked


def evidence_from_chunks(reranked, indices):
    """Resolve LLM-cited chunk indices (1-based) to Evidence records.
    Tolerates strings ('1','#1','chunk_1') and out-of-range silently."""
    out = []
    if not reranked: return out
    for idx in indices or []:
        try:
            n = int(re.sub(r"[^0-9]", "", str(idx)))
        except Exception:
            continue
        if 1 <= n <= len(reranked):
            c = reranked[n-1]
            out.append(Evidence(
                doc_id=str(c.get("doc_id")) if c.get("doc_id") else None,
                doc_filename=c.get("filename"),
                excerpt=(c.get("text", "") or "")[:300],
            ))
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# GEOCODING + PARCEL POLYGON
# ═══════════════════════════════════════════════════════════════════════════════

def geocode_address(address, expected_bundesland: Optional[str] = None):
    """Geocode ``address`` via the cached Nominatim wrapper.

    Args:
        address: The address string to geocode. Pre-trimmed; an empty /
            whitespace-only value short-circuits to ``None``.
        expected_bundesland: Optional Bundesland name (lowercase, as
            returned by :func:`detect_bundesland`). When provided AND a
            bbox exists for it (see :data:`bundesland_bbox.BUNDESLAND_BBOX`),
            any Nominatim result whose coordinates fall outside the bbox
            is rejected — the gate that closes the "turbines in Bremen"
            failure mode from the 2026-04 smoke-test where Nominatim
            resolved a Cuxhaven address to the city-state of Bremen
            ~70 km south-west. Rejected results are NOT cached; the next
            call with a more specific address gets a fresh attempt.

    Returns:
        ``(lat, lng)`` on success, ``None`` if the address is empty, the
        Nominatim call fails, no result is returned, or the bbox gate
        rejects the result.
    """
    if not address or not address.strip():
        return None
    conn = get_conn()
    cur = conn.cursor()

    # Cache lookup: only honor non-expired rows. NULL ``expires_at`` is
    # treated as expired so legacy rows pre-dating the TTL column get
    # re-geocoded once (and re-validated against the new bbox gate).
    cur.execute(
        "SELECT lat, lng FROM ddiq_geocode_cache "
        "WHERE address = %s AND expires_at IS NOT NULL AND expires_at > NOW()",
        (address,),
    )
    row = cur.fetchone()
    if row:
        cur.close(); conn.close()
        return (row[0], row[1])

    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "de"},
            headers={"User-Agent": NOMINATIM_UA},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            cur.close(); conn.close()
            return None

        lat = float(results[0]["lat"])
        lng = float(results[0]["lon"])

        # Plausibility gate. Unknown Bundeslaender pass through silently
        # (see ``is_in_bundesland`` docstring); known ones reject + log.
        if (
            expected_bundesland
            and has_bbox(expected_bundesland)
            and not is_in_bundesland(lat, lng, expected_bundesland)
        ):
            logger.warning(
                "Geocoding rejected for '%s': Nominatim returned "
                "(%.4f, %.4f) which is outside the %s bbox. Likely a "
                "wrong-Bundesland same-name resolution; not caching.",
                address, lat, lng, expected_bundesland,
            )
            cur.close(); conn.close()
            time.sleep(1.1)  # we did consume a Nominatim request
            return None

        # Cache. ``ON CONFLICT (address) DO UPDATE`` so a stale row (NULL
        # ``expires_at`` from before the TTL column existed, or just an
        # expired entry) gets refreshed instead of silently re-locking
        # itself for the next fetch cycle.
        cur.execute(
            "INSERT INTO ddiq_geocode_cache (address, lat, lng, expires_at) "
            "VALUES (%s, %s, %s, NOW() + INTERVAL '90 days') "
            "ON CONFLICT (address) DO UPDATE SET "
            "  lat = EXCLUDED.lat, "
            "  lng = EXCLUDED.lng, "
            "  cached_at = NOW(), "
            "  expires_at = EXCLUDED.expires_at",
            (address, lat, lng),
        )
        conn.commit()
        cur.close(); conn.close()
        time.sleep(1.1)
        return (lat, lng)
    except Exception as e:
        logger.warning(f"Geocoding failed for '{address}': {e}")

    cur.close(); conn.close()
    return None

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

    # Check cache. Track A item 6: filter on ``expires_at`` so legacy
    # rows (NULL ``expires_at`` from before the TTL column existed) are
    # treated as expired and re-fetched once.
    cache_key = f"alkis:{lat:.5f},{lng:.5f}"
    try:
        conn = get_conn(); cur = conn.cursor()
        cur.execute(
            "SELECT parcel_data FROM ddiq_parcel_cache "
            "WHERE coord_key = %s AND expires_at IS NOT NULL AND expires_at > NOW()",
            (cache_key,),
        )
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

        # Cache. ON CONFLICT … DO UPDATE refreshes ``expires_at`` so
        # stale rows self-heal on the next miss.
        try:
            conn = get_conn(); cur = conn.cursor()
            cur.execute(
                "INSERT INTO ddiq_parcel_cache (coord_key, parcel_data, expires_at) "
                "VALUES (%s, %s, NOW() + INTERVAL '30 days') "
                "ON CONFLICT (coord_key) DO UPDATE SET "
                "  parcel_data = EXCLUDED.parcel_data, "
                "  cached_at = NOW(), "
                "  expires_at = EXCLUDED.expires_at",
                (cache_key, json.dumps(parcels)),
            )
            conn.commit(); cur.close(); conn.close()
        except Exception: pass

        return parcels
    except Exception as e:
        logger.warning(f"ALKIS WFS failed for {bundesland}: {e}")
    return []


def _parse_alkis_feature(feature: dict) -> Optional[dict]:
    """Parse one GeoJSON feature from ALKIS INSPIRE WFS.

    Track A item 6 fix: the inner ``flur`` / ``area_m2`` loops used to
    read ``pass; break`` on parse failure — two statements on one line,
    so the ``break`` fired regardless of whether the conversion
    succeeded. A non-numeric value in the first matching key silently
    pinned the field to 0 instead of falling through to the next
    candidate key. Replaced with explicit early-success ``break`` so
    failed conversions correctly try the remaining keys.
    """
    props = feature.get("properties", {})
    geom = feature.get("geometry", {})

    # Parcel number — straight string read; first non-empty wins.
    pnum: Optional[str] = None
    for key in ("label", "flurstuecksnummer", "flstnrzae", "bezeichnung", "flstNr"):
        v = props.get(key)
        if v:
            pnum = str(v).strip()
            break
    # Fallback to ``nationalCadastralReference`` if no direct field matched.
    if not pnum:
        ncr = str(props.get("nationalCadastralReference", ""))
        if ncr:
            parts = ncr.split("-")
            if len(parts) >= 3:
                pnum = re.sub(r"^0+", "", parts[-1])
                pnum = re.sub(r"/0+", "/", pnum)
    if not pnum:
        return None

    # Gemarkung — first non-empty key wins.
    gemarkung = ""
    for key in ("gemarkungsname", "gemarkung", "gemeinde", "municipality"):
        v = props.get(key)
        if v:
            gemarkung = str(v).strip()
            break

    # Flur — break only on SUCCESSFUL int conversion; otherwise try the
    # next candidate key. Previously the bare ``pass; break`` exited the
    # loop on first failure.
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

    # Area — same fix as flur.
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

    # Polygon — GeoJSON ``[lng, lat]`` → Leaflet ``[lat, lng]``.
    polygon: list[list[float]] = []
    if geom.get("type") == "Polygon" and geom.get("coordinates"):
        polygon = [[pt[1], pt[0]] for pt in geom["coordinates"][0]]
    elif geom.get("type") == "MultiPolygon" and geom.get("coordinates"):
        largest = max(geom["coordinates"], key=lambda p: len(p[0]))
        polygon = [[pt[1], pt[0]] for pt in largest[0]]

    return {
        "parcelNumber": pnum,
        "gemarkung": gemarkung,
        "flur": flur,
        "polygon": polygon,
        "area_m2": area_m2,
        "source": "ALKIS WFS",
    }


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
    # Detect the project's Bundesland from the same location string we're
    # about to geocode — chains seamlessly because ``detect_bundesland``
    # returns the exact lowercase keys ``BUNDESLAND_BBOX`` uses. Falls
    # through to ``None`` (no gate) when location doesn't name a state.
    expected_bl = detect_bundesland(location) if location else None
    if location:
        coords = geocode_address(location, expected_bundesland=expected_bl)
        if coords:
            return coords
        for part in [p.strip() for p in location.replace(",", " ").split() if len(p.strip()) > 2]:
            coords = geocode_address(f"{part}, Germany", expected_bundesland=expected_bl)
            if coords:
                return coords
    name = get_section_value(sections, "overview", "Project Name")
    if name:
        clean = re.sub(r"(?i)windpark|windenergie|wind\s*farm", "", name).strip()
        if clean:
            # The project name may itself name a Bundesland we missed in
            # the location string ("Windpark Lamstedt Bayern e.g.).
            name_bl = expected_bl or detect_bundesland(clean)
            coords = geocode_address(f"{clean}, Germany", expected_bundesland=name_bl)
            if coords:
                return coords
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

EXTRACTION_SYSTEM = """You are a senior German legal due-diligence analyst for wind-energy
projects. Read the supplied document context and answer like a Berufsanwalt working
on an acquisition red-flag report.

Rules:
- ALWAYS return valid JSON only. No markdown, no preamble, no trailing text.
- Cite specific German statutes when relevant: BImSchG (§§4,6,10,15,52), BauGB (§35
  privileged use, §35 Abs. 5 Rückbau), BNatSchG (§44 Zugriffsverbote, §45 Ausnahme),
  UVPG, EEG (Marktwert, Marktprämie §20, Direktvermarktung §35a), TA Lärm,
  22./32. BImSchV, AVV Kennzeichnung, VwGO §70 (Widerspruchsfrist), §550 BGB Schriftform.
- For every fact-bearing answer, identify the supporting context chunks by their
  [#N] index from the supplied context and return them in the "evidence_chunks" array.
  Empty array if you have no source. Never fabricate citations.
- Use null for unknown optional fields. Don't guess monetary amounts or dates.
- Distinguish formal status (BImSchG §6 erteilt) from construction status (errichtet)
  from operational status (in Betrieb genommen) — these are different things."""


# SECTION_QUESTIONS — each entry is {label, question, anchor (statutory hook)}.
# The anchor keeps the question grounded in a specific German legal framework so
# the LLM doesn't drift into generic Q&A. Questions deliberately ask for facts a
# real DD lawyer would scribble in the margin.
SECTION_QUESTIONS = {
    "overview": [
        {"label": "Project Name", "anchor": "Genehmigungsbescheid / Pachtvertrag",
         "question": "What is the project name (Windpark-Bezeichnung) as it appears on the Genehmigungsbescheid title or Pachtvertrag preamble?"},
        {"label": "Location", "anchor": "Lageplan / Erläuterungsbericht",
         "question": "Project site: Bundesland, Landkreis, Gemeinde, Gemarkung. Cite the Lageplan or Erläuterungsbericht."},
        {"label": "Project Status", "anchor": "BImSchG §§4, 6, 10, 15",
         "question": "Current status under BImSchG: §10 Antrag eingereicht / Auslegung / §6 erteilt (bestandskräftig?) / §15 Inbetriebnahme angezeigt? Distinguish formal permit status from construction status."},
        {"label": "Project Type", "anchor": "EEG bestehende-Anlage rules",
         "question": "Greenfield, repowering (per §6 EEG bestehende-Anlage rules), or expansion? If repowering, are old turbines fully decommissioned?"},
        {"label": "Number of WEA", "anchor": "BImSchG-Bescheid",
         "question": "Total Windenergieanlagen, broken down by status (errichtet + genehmigt + geplant). State count for each class."},
        {"label": "Type & Capacity", "anchor": "Erläuterungsbericht",
         "question": "Per turbine: manufacturer, type designation, rated power kW, hub height m, rotor diameter m. Cite the Erläuterungsbericht."},
        {"label": "Total Capacity", "anchor": "BImSchG-Bescheid",
         "question": "Total installed/planned MW. State the numerator if some WEA are still in Genehmigung-erteilt status (not yet built)."},
        {"label": "Project Company", "anchor": "Gesellschaftsvertrag / HRB",
         "question": "Projektgesellschaft (Pächterin / Antragstellerin). Rechtsform (GmbH & Co. KG, etc.) and HR-Nummer if cited."},
        {"label": "Investors", "anchor": "Gesellschaftsvertrag / Gesellschafterliste",
         "question": "Gesellschafter / Kommanditisten / Investorenkreis from the Gesellschaftsvertrag or share register."},
        {"label": "Grid Connection", "anchor": "Netzanschlussvertrag / Einspeisezusage",
         "question": "Netzverknüpfungspunkt, Netzbetreiber, vereinbarte Anschlussleistung MW, geplantes Inbetriebnahmedatum. Reference the Netzanschlussvertrag / Einspeisezusage."},
        {"label": "Wind Priority Zone", "anchor": "BauGB §35 Abs. 1 Nr. 5 / Regionalplan",
         "question": "Designation per Regionalplan / Flächennutzungsplan (Vorrang-/Vorbehaltsgebiet Windenergie). Privileged use under §35 Abs. 1 Nr. 5 BauGB? Any concentrating-effect plan under §35 Abs. 3 Satz 3 BauGB?"},
    ],
    "land": [
        {"label": "Site Control Coverage", "anchor": "Pachtvertrag / Nutzungsvertrag",
         "question": "Which Flurstücke are secured by Pachtvertrag, Nutzungsvertrag, or registered Dienstbarkeit? Quote the percentage of project-area parcels covered. Flag missing parcels by Flurstücksnummer."},
        {"label": "Lessor vs Owner", "anchor": "BGB §873 / Grundbuch",
         "question": "Are all Pachtverträge signed by the registered Eigentümer per Grundbuch? Flag any case where Verpächter ≠ Eigentümer or where the signing party lacks Vertretungsmacht (Prokura, Vollmacht)."},
        {"label": "Term & Extension", "anchor": "Pachtvertrag Laufzeit",
         "question": "Pachtdauer (typically 25–30 yr) plus Verlängerungsoptionen. Compare to expected operational life and EEG-award duration. Flag any mismatch."},
        {"label": "Land Registry Encumbrances", "anchor": "Grundbuch Abt. II/III",
         "question": "Bestehende Eintragungen im Grundbuch Abt. II (Lasten) and Abt. III (Hypotheken/Grundschulden): Wegerecht, Leitungsrecht, Vorkaufsrecht §24 BauGB, Hypothek, Reallast. Quote Grundbuch references where given."},
        {"label": "Cable & Access Easements", "anchor": "BGB §§1090ff Dienstbarkeit",
         "question": "Are easements for Kabeltrasse and Zuwegung secured by registered Dienstbarkeit? List affected Wegeparzellen and confirm Eintragung im Grundbuch."},
        {"label": "Setback / 10H Compliance", "anchor": "BauGB §35 / 10H-Regelung BayBO Art. 82",
         "question": "Are Abstandsflächen and (for Bayern/Hessen) the 10H-Mindestabstand zu Wohnbebauung satisfied? State the actual distance to the nearest Wohnhaus and the regulatory minimum."},
        {"label": "Reinstatement / Rückbau", "anchor": "BauGB §35 Abs. 5",
         "question": "Rückbauverpflichtung at end of lease per §35 Abs. 5 BauGB: Bürgschaftshöhe, Bürge (Bank, Konzern), Laufzeit. Sufficient vs. expected Rückbaukosten?"},
        {"label": "Lease Defects", "anchor": "BGB §550 Schriftform / §10 Abs. 1 BauGB",
         "question": "Inhaltliche oder formelle Mängel in den Pachtverträgen (Schriftform §550 BGB, Vertretungsmacht, Übertragbarkeit auf Rechtsnachfolger, Anpassungsklauseln)."},
    ],
    "permits": [
        {"label": "BImSchG Permit Status", "anchor": "BImSchG §§4, 6, 10",
         "question": "Status nach §4 i.V.m. §6 BImSchG: Aktenzeichen, Ausfertigungsdatum, Bestandskraft (§70 VwGO Widerspruchsfrist abgelaufen?). Welche Auflagen und Nebenbestimmungen sind kritisch?"},
        {"label": "BauGB Privileged Use", "anchor": "BauGB §35 Abs. 1 Nr. 5 / §35 Abs. 3 Satz 3",
         "question": "Außenbereichsprivileg nach §35 Abs. 1 Nr. 5 BauGB. Konkurrenz mit konzentrierender Planung im Regionalplan (§35 Abs. 3 Satz 3 BauGB)? Konflikt mit Flächennutzungs- oder Bebauungsplan?"},
        {"label": "UVP / Environmental Impact", "anchor": "UVPG §§7-9",
         "question": "UVP-Vorprüfung (§7 UVPG) oder UVP-Pflicht (§§9-13 UVPG)? Wann wurde der UVP-Bericht erstellt? Welche Umweltauswirkungen wurden festgestellt und welche Ausgleichs-/Vermeidungsmaßnahmen verlangt?"},
        {"label": "Species Protection", "anchor": "BNatSchG §§44, 45",
         "question": "Artenschutzrechtliche Prüfung (§44 BNatSchG): Verbotstatbestände (Tötung/Verletzung, Störung, Zerstörung von Fortpflanzungs-/Ruhestätten) erfüllt? Ausnahmegenehmigung (§45 Abs. 7 BNatSchG) erforderlich? Welche Schutzmaßnahmen (Abschaltzeiten Rotmilan, Mäusebussard, Fledermaus)?"},
        {"label": "Noise & Shadow", "anchor": "TA Lärm / 22./32. BImSchV",
         "question": "TA Lärm Tag-/Nacht-Immissionsrichtwerte am nächstgelegenen IO eingehalten? Schattenwurfprognose nach 22./32. BImSchV (max. 30 min/Tag, 30 h/Jahr)? Abschaltautomatik vorgesehen?"},
        {"label": "Aviation / Lighting", "anchor": "AVV Kennzeichnung / EEG §9 BNK",
         "question": "Tageskennzeichnung und Nachtkennzeichnung nach AVV Kennzeichnung. Bedarfsgesteuerte Nachtkennzeichnung (BNK) gemäß §9 EEG aktiv? DFS-/Wehrbereich-Stellungnahme positiv?"},
        {"label": "Authority Consultations", "anchor": "VwVfG §§28, 30 / TÖB-Beteiligung",
         "question": "Beteiligung Träger öffentlicher Belange (Naturschutzbehörde, Landwirtschaftskammer, Forstbehörde, Wasserbehörde, ggf. DFS und Wehrbereich) abgeschlossen? Liegen positive Stellungnahmen vor?"},
        {"label": "Recurring Inspections", "anchor": "DIBt-Richtlinie / BImSchV",
         "question": "Wiederkehrende Prüfungen: jährliche WEA-Inspektion (DIBt-Richtlinie), Sicherheitsüberprüfung Turm (10-Jahres-Rhythmus), Schallpegel-Nachprüfung. Termine und Verantwortlichkeiten?"},
    ],
    "economics": [
        {"label": "EEG Subsidy Regime", "anchor": "EEG §22 Ausschreibung / §23a Marktwert",
         "question": "EEG-Förderregime: Ausschreibungszuschlag (Gebotstermin, anzulegender Wert ct/kWh)? Marktwert-Korrektur Wind an Land (§23a EEG)? Inbetriebnahme-Frist (regelmäßig 30 Mt nach Zuschlag) eingehalten oder Pönale?"},
        {"label": "Direktvermarktung", "anchor": "EEG §§20, 35a",
         "question": "Direktvermarktungsvertrag: Marktprämie nach §20 EEG, Direktvermarkter, Vertragslaufzeit, Fernsteuerbarkeit (§35a EEG)? Volumen- und Profilrisiko?"},
        {"label": "PPA / Off-Take", "anchor": "Stromabnahmevertrag",
         "question": "Stromabnahmevertrag: Abnehmer-Bonität, Kontraktlaufzeit, Pricing-Mechanismus (Festpreis / Floor / Cap), Volumenrisiko, Curtailment-Pass-Through, Change-of-Control-Klausel?"},
        {"label": "Financing", "anchor": "Senior debt / Equity",
         "question": "Finanzierungsstruktur: Senior Debt (Kreditgeber, Tranchen, DSCR-Covenants, Tilgungsprofil), Equity-Anteil, Nachrangkapital. Sponsor-Recourse oder non-recourse?"},
        {"label": "Securities", "anchor": "BGB Grundschuld / Forderungsabtretung",
         "question": "Besicherung: Grundschuld auf Pachtflächen, Forderungsabtretung Stromerlöse, Kontoverpfändung, Step-in-Rechte aus Direct Agreements? Bondworthiness der Sicherheiten?"},
        {"label": "O&M Service Level", "anchor": "Wartungsvertrag",
         "question": "Wartungsvertrag (Vollwartung / Basiswartung): Verfügbarkeitsgarantie (Marktstandard 97–98%), Pönalen bei Unterschreitung, Reaktionszeiten, Restlaufzeit?"},
        {"label": "Manufacturer Warranty", "anchor": "Werkvertrag §§631ff BGB",
         "question": "Hersteller-Gewährleistung: Anspruchsdauer, Ausschlüsse (höhere Gewalt, Wartungsverstöße), Haftungs-Cap, End-of-Warranty-Inspection-Termin?"},
        {"label": "Insurance Coverage", "anchor": "Allgefahren / BU / Haftpflicht",
         "question": "Versicherungsumfang: Allgefahrenversicherung (Sach), Maschinenbruch, Betriebsunterbrechung BI 12-18 Mt, Haftpflicht (Mindestdeckung 5 Mio €), D&O. Versicherer-Bonität und Selbstbehalte?"},
        {"label": "Tax Structure", "anchor": "GewStG §29 / UStG §15a",
         "question": "Gewerbesteuerzerlegung (§29 GewStG): 90% Standortgemeinde / 10% Sitzgemeinde-Anteil korrekt aufgeteilt? §15a UStG-Berichtigungsrisiko bei Vorsteuerabzug auf WEA-Investition?"},
        {"label": "Open Liabilities", "anchor": "VwGO §§70, 74 Widerspruch/Klage",
         "question": "Eventualverbindlichkeiten: laufende Widerspruchsverfahren §70 VwGO, Anfechtungsklagen §74 VwGO, behördliche Anhörungen, Schadenersatzklagen Anwohner, vorvertragliche Rücktrittsrechte?"},
    ],
}


def analyze_section(doc_ids, section_id):
    """Run the section's questions through evidence-aware RAG. Each row
    carries the chunks the LLM cited so the frontend can show 'click to
    see source'. Falls back gracefully if a question has no hit."""
    questions = SECTION_QUESTIONS.get(section_id, [])
    title_map = {"overview": "Project Overview", "land": "Land Security & Ownership",
                 "permits": "Permits & Regulatory Conditions", "economics": "Economics & Operations"}
    rows = []
    for q in questions:
        # Backwards-compatible: tuples like ("label","question") still work.
        if isinstance(q, tuple):
            label, question = q[0], q[1]; anchor = None
        else:
            label, question, anchor = q.get("label"), q.get("question"), q.get("anchor")
        ctx, reranked = rag_context_with_meta(doc_ids, question, top_k=5)
        anchor_hint = f"\nLegal anchor: {anchor}" if anchor else ""
        prompt = (
            f"""Answer this DD question based ONLY on the supplied context. Cite the
chunk numbers ([#1], [#2]...) you used in evidence_chunks.

Context:
{ctx}

Question: {question}{anchor_hint}

Respond JSON: {{"value":"answer as string","ampel":"green"/"yellow"/"red"/null,"note":"short risk note or null","evidence_chunks":[1,3]}}
IMPORTANT: value MUST be a string, never a bare number ("10 contracts" not 10).
Set ampel=red for material gaps/non-compliance, yellow for risks worth flagging,
green for verified-compliant, null when not enough information.""")
        try:
            result = llm_json(EXTRACTION_SYSTEM, prompt)
            val = clean_value(result.get("value"), "Information not found in documents")
            note_raw = result.get("note"); note = clean_value(note_raw, "") if note_raw else None
            if note == "": note = None
            row = AusgabeblattRow(
                label=label, value=val,
                ampel=result.get("ampel") if result.get("ampel") in ("green","yellow","red") else None,
                note=note,
            )
            # Stash evidence onto the row via a dynamic attr; AusgabeblattRow
            # doesn't have a field for it but findings/timeline that re-derive
            # from these rows pull evidence from a parallel structure below.
            row.__dict__["_evidence"] = evidence_from_chunks(reranked, result.get("evidence_chunks", []))
            row.__dict__["_anchor"] = anchor
            rows.append(row)
        except Exception as e:
            logger.error(f"Section {section_id}/{label}: {e}")
            rows.append(AusgabeblattRow(label=label, value="Could not extract", ampel="red", note=f"Error: {str(e)[:80]}"))
    return AusgabeblattSection(id=section_id, title=title_map.get(section_id, section_id.title()), rows=rows)


# ─── P0 #2: Timeline / deadline extraction ──────────────────────────────────
def extract_timeline(doc_ids, full_text):
    """Pull every date-bound milestone out of the documents and tag it.
    A real DD report ranks issues by their proximity to a deadline; without
    this pass the pipeline is date-blind and misses things like
    'BImSchG-Genehmigung gilt bis 2027-06-30, Verlängerungsantrag 6 Mt vorher'."""
    ctx, reranked = rag_context_with_meta(
        doc_ids,
        "Frist Ablauf Bestandskraft Inbetriebnahme Genehmigung gültig bis Verlängerung Pachtdauer Bürgschaft Laufzeit",
        top_k=8,
    )
    prompt = f"""Extract every date or deadline relevant to the wind-park DD.

Context:
{ctx}

Return JSON array. Each entry:
{{"kind":"permit_expiry|lease_term_end|renewal_deadline|warranty_end|bond_validity|construction_milestone|objection_window|other",
  "date":"YYYY-MM-DD or free text if month/year only",
  "description":"what this date governs (e.g. 'BImSchG permit Aktenzeichen 12-345 expires')",
  "legal_basis":"statute if applicable, e.g. 'VwGO §70 Widerspruchsfrist'",
  "evidence_chunks":[1,2]}}

Look specifically for:
- BImSchG permit Ausfertigungsdatum + Bestandskraft (3 Mt nach Zustellung per §70 VwGO)
- Pachtvertrag-Laufzeit-Ende and Verlängerungsoptionen-Frist
- Bürgschaft (Rückbaubürgschaft) Ablaufdatum
- EEG-Zuschlag-Inbetriebnahmefrist (regelmäßig 30 Mt nach Gebotstermin)
- Hersteller-Gewährleistung Ende
- Netzanschluss vereinbartes Inbetriebnahmedatum
- DIBt/§52 BImSchG wiederkehrende Prüfungstermine
Return [] if nothing date-bound is in the documents. Never invent a date."""
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict):
            result = result.get("timeline", result.get("data", []))
        if not isinstance(result, list):
            return []
        from datetime import datetime, date
        today = date.today()
        out: list[TimelineEntry] = []
        for r in result:
            ds = str(r.get("date", "")).strip()
            if not ds:
                continue
            days = None
            urgency = None
            try:
                d = datetime.strptime(ds[:10], "%Y-%m-%d").date()
                days = (d - today).days
                urgency = (
                    "expired" if days < 0 else
                    "urgent" if days <= 30 else
                    "soon" if days <= 180 else
                    "future"
                )
            except Exception:
                pass
            ev = evidence_from_chunks(reranked, r.get("evidence_chunks", []))
            out.append(TimelineEntry(
                kind=str(r.get("kind", "other")),
                date=ds,
                description=str(r.get("description", "")),
                legal_basis=r.get("legal_basis"),
                evidence=ev,
                days_from_now=days,
                urgency=urgency,
            ))
        return sorted(out, key=lambda t: (t.days_from_now if t.days_from_now is not None else 99999))
    except Exception as e:
        logger.error(f"Timeline extraction: {e}")
        return []


# ─── P0 #3: Cross-document consistency check ────────────────────────────────
def check_cross_doc_consistency(sections, weas, parcels, total_capacity_mw=None):
    """Detect contradictions BETWEEN the analysed documents — the classic
    DD red flag that pure-RAG Q&A misses because each question runs in
    isolation. Examples: BImSchG permit count ≠ lease parcel count, lease
    term shorter than EEG-award duration, lessor names inconsistent."""
    facts = {
        "sections": [{"section": s.title, "label": r.label, "value": r.value, "ampel": r.ampel}
                     for s in sections for r in s.rows],
        "wea_count": len(weas),
        "wea_status_codes": [w.status_code for w in weas if w.status_code],
        "parcel_count": len(parcels),
        "parcel_secured": sum(1 for p in parcels if p.status == "secured"),
        "parcel_not_secured": sum(1 for p in parcels if p.status == "not_secured"),
        "total_capacity_mw": total_capacity_mw,
    }
    prompt = f"""You are doing the cross-document consistency check on a wind-park DD.
Scan these extracted facts for contradictions, missing-document red flags, or
inconsistencies that a Berufsanwalt would immediately challenge.

Facts:
{json.dumps(facts, ensure_ascii=False, indent=2)}

Look for:
- Turbine count differs across BImSchG-Bescheid / Pachtvertrag / EEG-Zuschlag
- Total MW from sections doesn't match (#turbines × rated power)
- Pachtdauer < expected operational life (typically 25 yr)
- Lessor / Verpächter names inconsistent across leases
- Project Company in Pachtvertrag ≠ Antragstellerin im BImSchG-Antrag
- Number of secured parcels < number of WEA (each WEA needs Standort + Zuwegung)
- BImSchG permit erteilt but no Pachtvertrag for one or more parcels
- EEG-Zuschlag erteilt but Inbetriebnahme-Frist conflicts with construction status
- Cited capacity in Erläuterungsbericht ≠ EEG-Zuschlag MW
- Missing core document type: BImSchG-Bescheid / Pachtvertrag / Netzanschluss / Rückbaubürgschaft

Return JSON array. Each entry:
{{"text":"clear factual statement of the inconsistency",
  "severity":"red|yellow",
  "domain":"Land|Permits|Economics|Regulatory|General",
  "legal_basis":"if applicable",
  "recommended_action":"what to do about it",
  "quantification":{{"mw_affected":..,"eur_impact_estimate":..,"days_until_deadline":..,"rationale":".."}}}}

Return [] if no inconsistencies found. Never fabricate — only flag what the
facts clearly contradict."""
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict):
            result = result.get("inconsistencies", result.get("findings", result.get("data", [])))
        if not isinstance(result, list):
            return []
        out: list[Finding] = []
        for r in result:
            text = str(r.get("text", "")).strip()
            if not text:
                continue
            q_raw = r.get("quantification") or {}
            quant = None
            if isinstance(q_raw, dict) and any(q_raw.get(k) is not None for k in ("mw_affected","eur_impact_estimate","days_until_deadline")):
                quant = Quantification(
                    mw_affected=q_raw.get("mw_affected"),
                    eur_impact_estimate=q_raw.get("eur_impact_estimate"),
                    days_until_deadline=q_raw.get("days_until_deadline"),
                    rationale=q_raw.get("rationale"),
                )
            out.append(Finding(
                domain=str(r.get("domain", "General")),
                severity=r.get("severity") if r.get("severity") in ("red", "yellow", "green") else "yellow",
                text=text,
                legal_basis=r.get("legal_basis"),
                recommended_action=r.get("recommended_action"),
                quantification=quant,
                kind="cross_document",
            ))
        return out
    except Exception as e:
        logger.error(f"Cross-doc consistency: {e}")
        return []


# ─── P1 #9: Rückbaubürgschaft extraction ────────────────────────────────────
def extract_rueckbau_bond(doc_ids):
    """§35 Abs. 5 BauGB requires the operator to post a decommissioning bond.
    Recurring DD red flag — without verifying it, the project can be blocked
    at financial close. Pull amount, provider, beneficiary, validity from
    the Auflagen of the BImSchG-Bescheid or a separate Bürgschaftsurkunde."""
    ctx, reranked = rag_context_with_meta(
        doc_ids,
        "Rückbau Bürgschaft Sicherheitsleistung Hinterlegung Konzernbürgschaft §35 BauGB Abriss Beseitigung",
        top_k=6,
    )
    prompt = f"""Extract the Rückbaubürgschaft (decommissioning bond) facts.

Context:
{ctx}

Return JSON object:
{{"amount_eur": <number or null>,
  "provider": "<bank/insurer/parent or null>",
  "beneficiary": "<usually Standortgemeinde>",
  "valid_until": "YYYY-MM-DD or null",
  "instrument_type": "Bürgschaft|Hinterlegung|Konzernbürgschaft|null",
  "sufficient": <true/false/null — your read on whether the amount covers
   expected Rückbaukosten (typical 80-150k €/MW)>,
  "note": "one-sentence assessment",
  "evidence_chunks":[1,3]}}

If no Rückbau bond is mentioned, return null fields with note='not found in documents'.
Never fabricate amounts."""
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        if not isinstance(result, dict):
            return None
        if all(result.get(k) is None for k in ("amount_eur", "provider", "valid_until", "instrument_type")):
            # Nothing real extracted; surface a placeholder so the lawyer
            # knows the absence is intentional, not a UI bug.
            return RueckbauBond(note=str(result.get("note", "Rückbaubürgschaft not found in supplied documents.")))
        return RueckbauBond(
            amount_eur=result.get("amount_eur"),
            provider=result.get("provider"),
            beneficiary=result.get("beneficiary"),
            valid_until=result.get("valid_until"),
            instrument_type=result.get("instrument_type"),
            sufficient=result.get("sufficient"),
            note=result.get("note"),
            evidence=evidence_from_chunks(reranked, result.get("evidence_chunks", [])),
        )
    except Exception as e:
        logger.error(f"Rückbaubürgschaft extraction: {e}")
        return None


# ─── P1 #6: Grundbuch lessor-vs-owner check ─────────────────────────────────
def check_grundbuch_match(doc_ids, parcels):
    """Compare Pachtvertrag-lessor against the registered Eigentümer per
    Grundbuch. A parcel can show 'secured' under contract logic even if
    the lessor has no legal title — this is the next layer of validation."""
    if not parcels:
        return []
    # Sample a manageable subset of secured parcels — Grundbuch lookup
    # is the most expensive LLM pass per parcel. Lawyer-grade DD would
    # check every one externally, but we surface the LLM's read on what's
    # actually extractable from the supplied PDFs.
    target = [p for p in parcels if p.status == "secured" and p.normalizedId][:25]
    if not target:
        return []
    ctx, reranked = rag_context_with_meta(
        doc_ids,
        "Grundbuch Eigentümer Eintragung Belastung Wegerecht Vorkaufsrecht Hypothek Reallast Pächter Verpächter",
        top_k=10,
    )
    parcel_list = [{"parcel_id": p.normalizedId, "owner_per_alkis": p.owner,
                    "lessor_or_contract_ref": p.contractRef or "unknown"} for p in target]
    prompt = f"""For each parcel in the list, judge whether the registered Grundbuch-
Eigentümer matches the Verpächter named in the Pachtvertrag, and list any
encumbrances (Belastungen) you can find in the documents.

Context:
{ctx}

Parcels:
{json.dumps(parcel_list, ensure_ascii=False)}

Return JSON array. Each entry:
{{"parcel_id":"...",
  "registered_owner":"Eigentümer per Grundbuch or null",
  "lessor_name":"Verpächter per Pachtvertrag or null",
  "owner_match":<true/false/null — null if undeterminable>,
  "match_confidence":<0.0..1.0>,
  "encumbrances":["Wegerecht zugunsten Gemeinde X","§24 BauGB Vorkaufsrecht",...],
  "note":"short explanation",
  "evidence_chunks":[1,4]}}

Only return parcels that appear in the supplied list. owner_match=null is fine
if the documents don't show enough — don't guess. encumbrances=[] is fine when
nothing is registered."""
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict):
            result = result.get("checks", result.get("data", []))
        if not isinstance(result, list):
            return []
        out: list[GrundbuchCheck] = []
        for r in result:
            pid = str(r.get("parcel_id", "")).strip()
            if not pid:
                continue
            try:
                conf = float(r.get("match_confidence", 0.0))
            except Exception:
                conf = 0.0
            out.append(GrundbuchCheck(
                parcel_id=pid,
                registered_owner=r.get("registered_owner"),
                lessor_name=r.get("lessor_name"),
                owner_match=r.get("owner_match"),
                match_confidence=conf,
                encumbrances=[str(x) for x in (r.get("encumbrances") or []) if x],
                note=r.get("note"),
                evidence=evidence_from_chunks(reranked, r.get("evidence_chunks", [])),
            ))
        return out
    except Exception as e:
        logger.error(f"Grundbuch check: {e}")
        return []


def extract_wea_statuses(doc_ids, full_text, sections, project_center=None):
    """Extract WEA per-turbine attributes including the technical fields a
    DD lawyer needs: hub height (drives 10H clearance for Bayern/Hessen),
    rotor diameter, rated power, manufacturer, model, status code, BImSchG
    permit reference, warranty end date."""
    context = rag_context(
        doc_ids,
        "Windenergieanlage WEA Hersteller Typ Nabenhöhe Rotordurchmesser Nennleistung "
        "Aktenzeichen Genehmigung errichtet geplant Standort Flurstück Gewährleistung",
    )
    prompt = f"""Extract ALL wind turbines (WEA/WKA) from the documents.

Context:
{context}

Text (first 6000 chars):
{full_text[:6000]}

Return JSON array of turbines. Each turbine:
{{"name":"WEA Hude 1",
  "owner":"name or Unknown",
  "parcel":"Flurstücksnummer or empty",
  "contract":"Pachtvertrag-Ref or 'Not specified'",
  "address":"municipality, state",
  "ampel":"green|yellow|red",
  "hub_height_m":<number or null — Nabenhöhe in metres>,
  "rotor_diameter_m":<number or null — Rotordurchmesser in metres>,
  "rated_power_kw":<number or null — Nennleistung in kW>,
  "manufacturer":"Vestas|Enercon|Nordex|Siemens Gamesa|GE|null",
  "model":"E-138 EP3|V126|N163|... (Typenbezeichnung) or null",
  "status_code":"errichtet|genehmigt|geplant|abgenommen|null",
  "permit_ref":"BImSchG-Aktenzeichen or null",
  "warranty_end":"YYYY-MM-DD or null"}}

Rules:
- Status: errichtet=physically built, genehmigt=permit issued not yet built,
  geplant=planned only, abgenommen=accepted into operation. Be honest about
  ambiguity — set null rather than guessing.
- If "7 WEA in Hude" create WEA Hude 1 through WEA Hude 7 with shared attrs.
- Hub height matters: 10H rule means a 200m turbine in Bayern needs 2km clearance.
  Pull this from the Erläuterungsbericht / Genehmigungsbescheid wherever possible.
- Use "yellow" ampel for pre-check docs where status is unknown."""

    weas_raw = []
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict):
            weas_raw = result.get("turbines", result.get("wea", result.get("data", [])))
        elif isinstance(result, list):
            weas_raw = result
    except Exception as e:
        logger.error(f"WEA extraction: {e}")

    if not weas_raw:
        wea_count = parse_wea_count(get_section_value(sections, "overview", "Number of WEA"))
        pname = get_section_value(sections, "overview", "Project Name")
        loc = get_section_value(sections, "overview", "Location")
        company = get_section_value(sections, "overview", "Project Company")
        if wea_count > 0:
            short = re.sub(r"(?i)windpark|windenergie|wind\s*farm|projekt", "", pname).strip().split()[0] if pname else "WEA"
            for i in range(1, wea_count+1):
                weas_raw.append({"name": f"WEA {short} {i}", "owner": company or "See contracts",
                                 "parcel": "", "contract": "See contract review",
                                 "address": loc, "ampel": "yellow"})

    def _num(v):
        try: return float(v) if v not in (None, "") else None
        except Exception: return None

    # WEAs sit inside the project area, so the project's centroid pins
    # the expected Bundesland for every per-WEA geocode below. None if
    # we have no project_center yet (rare; falls back to no gate).
    project_bl = bundesland_from_coords(*project_center) if project_center else None
    statuses = []
    for idx, w in enumerate(weas_raw):
        addr = str(w.get("address", ""))
        coords = geocode_address(addr, expected_bundesland=project_bl) if addr else None
        if not coords and project_center: coords = project_center
        sc = w.get("status_code")
        if sc not in ("errichtet", "genehmigt", "geplant", "abgenommen"):
            sc = None
        statuses.append(WEAStatus(
            name=str(w.get("name", f"WEA {idx+1}")),
            ampel=w.get("ampel","yellow") if w.get("ampel") in ("green","yellow","red") else "yellow",
            owner=clean_value(w.get("owner"), "Unknown"),
            parcel=clean_value(w.get("parcel"), ""),
            contract=clean_value(w.get("contract"), "Not specified"),
            lat=coords[0] if coords else 0.0, lng=coords[1] if coords else 0.0,
            address=addr,
            hub_height_m=_num(w.get("hub_height_m")),
            rotor_diameter_m=_num(w.get("rotor_diameter_m")),
            rated_power_kw=_num(w.get("rated_power_kw")),
            manufacturer=w.get("manufacturer") or None,
            model=w.get("model") or None,
            status_code=sc,
            permit_ref=w.get("permit_ref") or None,
            warranty_end=w.get("warranty_end") or None,
        ))

    # Scatter duplicate coordinates so the map doesn't stack pins.
    if statuses:
        cg = {}
        for i, s in enumerate(statuses): cg.setdefault(f"{s.lat:.6f},{s.lng:.6f}", []).append(i)
        for key, indices in cg.items():
            if len(indices) > 1:
                clat, clng = statuses[indices[0]].lat, statuses[indices[0]].lng
                for j, idx in enumerate(indices):
                    angle = (2*math.pi*j)/len(indices); s = statuses[idx]
                    s2 = s.copy(update={"lat": clat+0.003*math.cos(angle),
                                         "lng": clng+0.003*math.sin(angle)})
                    statuses[idx] = s2

    # Deduplicate names per address group.
    nc = {}
    for s in statuses: nc[s.name] = nc.get(s.name, 0) + 1
    if any(c > 1 for c in nc.values()):
        ag = {}
        for i, s in enumerate(statuses): ag.setdefault(s.address, []).append(i)
        for addr, indices in ag.items():
            short = addr.split(",")[0].strip().split()[-1] if addr else "Park"
            for j, idx in enumerate(indices):
                s = statuses[idx]
                statuses[idx] = s.copy(update={"name": f"WEA {short} {j+1}"})
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

    # Same plausibility logic as ``extract_wea_statuses``: infrastructure
    # (substations, cable termini, access roads) sits inside or adjacent
    # to the project area, so the project Bundesland gates per-point
    # geocode results.
    project_bl = bundesland_from_coords(*project_center) if project_center else None
    points = []
    for p in infra_raw:
        addr = str(p.get("address", ""))
        coords = geocode_address(addr, expected_bundesland=project_bl) if addr else None
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


def _findings_prompt_for_issue(issue: dict, total_capacity_mw: Optional[float] = None) -> str:
    """Build the per-row LLM prompt asking for ONE Finding object.

    Pre-serialise the issue dict outside the f-string so its JSON braces
    don't collide with f-string brace escaping.
    """
    issue_json = json.dumps(issue, ensure_ascii=False)
    capacity_hint = (
        f"\nProject total capacity (for MW-affected sizing): {total_capacity_mw} MW"
        if total_capacity_mw
        else ""
    )
    return f"""You are drafting ONE entry of the FINDINGS chapter of a wind-park red-flag DD report.
For the single material issue below, produce ONE Finding with:
- domain: "Land" | "Permits" | "Economics" | "Regulatory" | "General"
- severity: "red" (deal-blocker) | "yellow" (open issue, manageable) | "green" (resolved)
- text: 1-2 sentence factual statement of the issue.
- legal_basis: cite the specific German statute (e.g. "BImSchG §6", "BauGB §35 Abs. 5",
  "BNatSchG §44", "VwGO §70") if known. null otherwise.
- recommended_action: concrete next step a lawyer would take (e.g. "Obtain certified
  Grundbuch extract for parcel 12/4 and verify lessor identity", "Renew Bürgschaft
  with bank guarantee letter before 2027-06-30").
- quantification: object with mw_affected (number, null if unknown), eur_impact_estimate
  (number in EUR, null if unknown), days_until_deadline (integer, null if no
  date-bound deadline), rationale (one short sentence justifying the numbers).{capacity_hint}

Issue to draft for:
{issue_json}

Return a single JSON object (not an array). Use null for any field you cannot determine."""


def _finding_from_llm_obj(obj: dict, source_issue: dict) -> Optional[Finding]:
    """Convert one LLM-returned JSON object into a :class:`Finding`.

    Returns ``None`` if the object is unusable (wrong shape, missing the
    mandatory ``text`` field, etc.) so the caller can emit a structured
    placeholder for this specific issue instead.

    Evidence is attached directly from ``source_issue`` rather than asked
    from the LLM — the old batched prompt routed Evidence through a
    1-indexed ``evidence_indices`` array because each LLM response covered
    multiple issues; per-row that indirection is dead weight.
    """
    if not isinstance(obj, dict):
        return None
    text = str(obj.get("text", "")).strip()
    if not text:
        return None
    sev = obj.get("severity") if obj.get("severity") in ("green", "yellow", "red") else "yellow"

    ev: list[Evidence] = []
    for e in source_issue.get("evidence") or []:
        if isinstance(e, dict):
            ev.append(Evidence(**{k: v for k, v in e.items() if k in Evidence.__fields__}))

    q_raw = obj.get("quantification") or {}
    quant = None
    if isinstance(q_raw, dict) and any(
        q_raw.get(k) is not None
        for k in ("mw_affected", "eur_impact_estimate", "days_until_deadline")
    ):
        quant = Quantification(
            mw_affected=q_raw.get("mw_affected"),
            eur_impact_estimate=q_raw.get("eur_impact_estimate"),
            days_until_deadline=q_raw.get("days_until_deadline"),
            rationale=q_raw.get("rationale"),
        )

    return Finding(
        domain=str(obj.get("domain", "General")),
        severity=sev,
        text=text,
        evidence=ev,
        quantification=quant,
        legal_basis=obj.get("legal_basis"),
        recommended_action=obj.get("recommended_action"),
        kind="section",
    )


def _placeholder_finding_for_issue(i: int, issue: dict) -> Finding:
    """Emit a structured stand-in when the LLM call for one issue failed.

    Carries the issue's section + label + Evidence so a human reviewer can
    locate the source row immediately. Previously the whole chapter was
    replaced with "Manual review required" on the first parse failure;
    per-row, the lawyer still sees the other findings in their slots and
    only this one shows the placeholder.
    """
    ev: list[Evidence] = []
    for e in issue.get("evidence") or []:
        if isinstance(e, dict):
            ev.append(Evidence(**{k: v for k, v in e.items() if k in Evidence.__fields__}))
    return Finding(
        domain="General",
        severity="yellow",
        text=(
            f"(Extraction failed for issue #{i}: "
            f"{issue.get('section', '?')} → {issue.get('label', '?')}). "
            "Manual review of the source row required."
        ),
        evidence=ev,
        kind="section",
    )


def generate_findings(doc_ids, sections, total_capacity_mw: Optional[float] = None):
    """Build evidence-aware Findings, **one LLM call per flagged row**.

    Each finding carries: legal_basis, recommended_action, quantification
    (mw_affected, eur_impact, days_until_deadline) and Evidence pointers
    back to the cited chunks in ``sections`` — a lawyer can click through
    to the source PDF instead of taking the LLM at its word.

    Per-row iteration (Track A item 2) replaces the historical single
    batched call. The previous shape was fragile: if the LLM emitted a
    malformed array OR its response was truncated mid-element, the entire
    ``llm_json`` parse would fail and the whole chapter was lost
    ("Manual review required" placeholder for everything). Per-row, a
    single malformed response only loses ONE finding and emits a
    structured placeholder in its slot — the other findings still come
    through and the lawyer can locate the missing one by its section +
    label.

    Cost: N× more LLM calls. Measured single-call latency against the
    live ``lai_analyzer_llm`` (Qwen3.6-27B in thinking-mode) is ~120-150s
    for a realistic findings prompt — substantially higher than a chat
    completion because the model reasons through the legal basis +
    quantification before emitting JSON. For a 10-row report that's
    ~20-25min of extra wall-time on top of the existing multi-minute
    pipeline. The reliability win (no more whole-chapter loss on one
    malformed row) outweighs this, but two follow-ups can claw most of
    it back if the latency starts mattering for live demos:

    1. Parallelise via ``concurrent.futures.ThreadPoolExecutor`` over a
       single shared :class:`SyncLlmClient` (its underlying
       ``httpx.Client`` is thread-safe). The analyzer LLM container
       still serialises GPU-side, but pipeline overlap and HTTP-side
       concurrency typically cut total wall-time 30-50%.
    2. Disable thinking-mode for the findings pass (``keep_thinking=
       False`` + ``LlmConfig(thinking_mode_enabled=False)``) since the
       prompt is narrow enough that we don't need the reasoning trace
       — typically halves per-call latency.
    """
    flagged = []
    for sec in sections:
        for row in sec.rows:
            if row.ampel not in ("red", "yellow"):
                continue
            ev = row.__dict__.get("_evidence") or []
            anchor = row.__dict__.get("_anchor")
            flagged.append({
                "section": sec.title, "label": row.label, "value": row.value,
                "ampel": row.ampel, "note": row.note, "anchor": anchor,
                "evidence": [e.dict() if hasattr(e, "dict") else e for e in ev],
            })

    if not flagged:
        return [Finding(
            domain="General", severity="green",
            text="No material issues identified across the analysed sections.",
            kind="section",
        )]

    out: list[Finding] = []
    failures = 0
    for i, issue in enumerate(flagged, start=1):
        prompt = _findings_prompt_for_issue(issue, total_capacity_mw)
        try:
            obj = llm_json(EXTRACTION_SYSTEM, prompt)
        except Exception as e:
            # llm_json itself shouldn't raise (it returns {} on hard
            # failure since the SyncLlmClient migration), but a transport
            # crash mid-call still could. Don't let one bad row stop
            # the rest.
            logger.warning(f"findings.issue#{i}: llm_json raised — {e}")
            obj = {}

        finding: Optional[Finding] = None
        if isinstance(obj, dict):
            finding = _finding_from_llm_obj(obj, issue)
        elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
            # Be lenient: the prompt asks for a single object, but if the
            # model returned a single-element array we'll still take it.
            finding = _finding_from_llm_obj(obj[0], issue)

        if finding is None:
            failures += 1
            out.append(_placeholder_finding_for_issue(i, issue))
        else:
            out.append(finding)

    if failures:
        logger.warning(
            f"findings: {failures}/{len(flagged)} issues fell through to "
            "placeholder (extraction failed). Other findings unaffected."
        )

    return out


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/documents", response_model=DocumentListResponse)
def list_documents(user: CurrentUser = Depends(get_current_user)):
    """List the caller's documents only. AUTH_PLAN G1."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT id, filename, size_bytes, upload_date, status, category "
        "FROM ddiq_documents WHERE user_id = %s ORDER BY upload_date DESC",
        (str(user.id),),
    )
    rows = cur.fetchall(); cur.close(); conn.close()
    return DocumentListResponse(documents=[DocumentOut(id=str(r["id"]), name=r["filename"],
        size=round(r["size_bytes"]/(1024*1024),2), uploadDate=r["upload_date"].isoformat()[:10],
        type=r["filename"].rsplit(".",1)[-1].upper() if "." in r["filename"] else "File",
        status=r["status"], category=r["category"]) for r in rows], total=len(rows))

@router.post("/documents/upload", response_model=UploadDocResponse)
def upload_document(
    file: UploadFile = File(...),
    category: str = Form("Uncategorized"),
    session_id: Optional[str] = Form(None),
    user: CurrentUser = Depends(get_current_user),
):
    # Sync handler — runs in FastAPI's threadpool. Use file.file.read()
    # (the underlying SpooledTemporaryFile) instead of `await file.read()`.
    # user_id comes from the JWT (AUTH_PLAN G3) — never from the body.
    if not file.filename.lower().endswith(".pdf"): raise HTTPException(400, "Only PDF")
    fb = file.file.read()
    if len(fb) > MAX_FILE_SIZE: raise HTTPException(400, "Too large")
    full_text, pages = extract_pdf_text(fb)
    if not full_text.strip(): raise HTTPException(400, "No text extracted")
    chunks = chunk_text(full_text)
    if not chunks: raise HTTPException(400, "No chunks")
    embs = embed_texts([c["text"] for c in chunks])
    conn = get_conn(); cur = conn.cursor(); did = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO ddiq_documents (id,user_id,filename,size_bytes,status,category,full_text,chunk_count,session_id) "
        "VALUES (%s,%s,%s,%s,'analyzed',%s,%s,%s,%s)",
        (did, str(user.id), file.filename, len(fb), category, full_text, len(chunks), session_id),
    )
    for c, e in zip(chunks, embs):
        cur.execute("INSERT INTO ddiq_doc_chunks (doc_id,chunk_idx,text,embedding) VALUES (%s,%s,%s,%s::vector)",
            (did, c["idx"], c["text"], "["+",".join(str(x) for x in e)+"]"))
    conn.commit(); cur.close(); conn.close()
    return UploadDocResponse(id=did, filename=file.filename, pages=pages, chunks=len(chunks), status="analyzed",
        message=f"{file.filename}: {pages} pages, {len(chunks)} chunks")

# ─── Request dedup ────────────────────────────────────────────────────────
# A 30-60 min pipeline run is too expensive to repeat for the same input.
# We fingerprint (sorted doc_ids, preset, project_name) and look up
# ddiq_reports.request_fingerprint before queuing or running anything.
# - status='done'  → return the cached row, no work done.
# - status in ('queued','running') and recent → return that row's id so the
#   caller polls the in-flight job instead of starting a duplicate.

_INFLIGHT_TTL = "2 hours"  # reuse window for queued/running rows


def _compute_fingerprint(doc_ids, preset, project_name, user_id) -> str:
    """Cache key for report-generation requests.

    Scoped by ``user_id`` so two tenants requesting the same documents
    do not collide — and so the cache lookup cannot return another
    user's row.
    """
    parts = [
        str(user_id),
        ",".join(sorted(doc_ids or [])),
        (preset or "").strip().lower(),
        (project_name or "").strip().lower(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _find_existing_report(fp: str, user_id) -> Optional[dict]:
    """Most recent reusable row for this fingerprint owned by ``user_id``.

    The fingerprint alone already includes the user_id, but the WHERE
    clause filters explicitly — defense in depth: if a future change
    drops user_id from the fingerprint, the SQL still refuses to leak.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""SELECT id, status, created_at, started_at
                    FROM ddiq_reports
                    WHERE request_fingerprint = %s
                      AND user_id = %s
                      AND (
                        status = 'done'
                        OR (status IN ('queued','running')
                            AND COALESCE(started_at, created_at) > NOW() - INTERVAL '{_INFLIGHT_TTL}')
                      )
                    ORDER BY created_at DESC
                    LIMIT 1""",
                (fp, str(user_id)),
            )
            return cur.fetchone()


# ─── Background-task worker for /report/generate/async ────────────────────
# /report/generate is synchronous — fine for direct API use, but the
# 30-60 minute report blocks a request the whole time. Browsers and
# proxies time out long before that. The async path lets callers POST,
# get a {report_id, status:"queued"} immediately, and poll
# /report/{id}/status (or /report/{id}) for completion.

from concurrent.futures import ThreadPoolExecutor

_report_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("REPORT_WORKERS", "2")),
    thread_name_prefix="ddiq-report",
)


def _update_report_progress(report_id: str, step: Optional[str] = None,
                            percent: Optional[float] = None,
                            status: Optional[str] = None,
                            error: Optional[str] = None,
                            user_id=None) -> None:
    """Patch a row in ddiq_reports without touching report_data. Best-effort.

    When ``user_id`` is supplied, the UPDATE is scoped — a teammate's
    background worker (or a stray retry) can never tamper with another
    user's report row.
    """
    sets, params = [], []
    if step is not None:    sets.append("progress_step = %s");    params.append(step)
    if percent is not None: sets.append("progress_percent = %s"); params.append(percent)
    if status is not None:  sets.append("status = %s");           params.append(status)
    if error is not None:   sets.append("error = %s");            params.append(error)
    if not sets:
        return
    if status == "running" and "started_at" not in [s.split(" =")[0] for s in sets]:
        sets.append("started_at = COALESCE(started_at, NOW())")
    if status in ("done", "failed"):
        sets.append("finished_at = NOW()")
    where = "WHERE id = %s"
    params.append(report_id)
    if user_id is not None:
        where += " AND user_id = %s"
        params.append(str(user_id))
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE ddiq_reports SET {', '.join(sets)} {where}",
                    params,
                )
    except Exception as e:
        logger.warning(f"progress update failed for {report_id}: {e}")


def _persist_report_jsonb(rid: str, project_name: str, doc_ids: list, preset: str,
                          report: "DDiQReportData", user_id) -> None:
    """Best-effort UPSERT of just the report_data JSONB. Used as a checkpoint
    after each major pipeline phase — if a later phase crashes, the row
    still has the partial report from the last successful checkpoint
    instead of the empty '{}' placeholder.

    Cheap (one round-trip per phase) and idempotent: re-running the same
    pipeline overwrites the row in place. Auxiliary table writes
    (ddiq_contracts, ddiq_classified_parcels, ddiq_project_areas) are
    deliberately NOT done here — those are write-once-at-end to avoid
    duplicate rows from re-running.

    ``user_id`` is set on initial INSERT only; the ON CONFLICT branch
    leaves it untouched so a stray re-call cannot reassign ownership.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ddiq_reports (id, user_id, project_name, document_ids, preset, report_data)
                       VALUES (%s, %s, %s, %s::uuid[], %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                           project_name = EXCLUDED.project_name,
                           document_ids = EXCLUDED.document_ids,
                           preset = EXCLUDED.preset,
                           report_data = EXCLUDED.report_data""",
                    (rid, str(user_id), project_name, doc_ids, preset, json.dumps(report.dict())),
                )
    except Exception as e:
        # Checkpoint failure shouldn't kill the pipeline — the next checkpoint
        # (or the final UPSERT) will catch up.
        logger.warning(f"checkpoint persist for {rid} failed: {e}")


def _run_report_generation_job(rid: str, req: "GenerateReportRequest", user_id) -> None:
    """Runs the same pipeline as the sync /report/generate, but writes
    progress + final report into the existing ddiq_reports row instead
    of returning. Errors are recorded in ``status='failed'`` + ``error``.

    ``user_id`` is threaded into every write so a worker on a shared
    pool cannot accidentally update a different tenant's row.
    """
    try:
        _update_report_progress(rid, status="running", step="starting", percent=0.0, user_id=user_id)
        report, _T = _generate_report_core(
            rid, req, user_id,
            progress=lambda step, pct: _update_report_progress(rid, step=step, percent=pct, user_id=user_id),
        )
        _update_report_progress(rid, status="done", step="done", percent=1.0, user_id=user_id)
    except HTTPException as e:
        _update_report_progress(rid, status="failed", error=f"HTTP {e.status_code}: {e.detail}", user_id=user_id)
    except Exception as e:
        logger.exception(f"report {rid} failed")
        _update_report_progress(rid, status="failed", error=str(e)[:500], user_id=user_id)


@router.post("/report/generate/async")
def generate_report_async(req: GenerateReportRequest, user: CurrentUser = Depends(get_current_user)):
    """Non-blocking variant of /report/generate. Returns
    {report_id, status} immediately and runs the pipeline in a
    background thread. Poll /report/{id}/status (or /report/{id}) for
    progress and the final result."""
    if not req.document_ids:
        raise HTTPException(400, "No documents selected")
    _assert_owns_documents(req.document_ids, user.id)

    fp = _compute_fingerprint(req.document_ids, req.preset, req.project_name, user.id)
    existing = _find_existing_report(fp, user.id)
    if existing:
        return {
            "report_id": str(existing["id"]),
            "status": existing["status"],
            "poll_url": f"/ddiq/report/{existing['id']}/status",
            "cached": True,
        }

    # Pre-create the row so the caller has a real report_id to poll.
    rid = str(uuid.uuid4())
    pname = req.project_name or "Wind Energy Project"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ddiq_reports (id, user_id, project_name, document_ids, preset,
                                              report_data, status, started_at, progress_step, progress_percent,
                                              request_fingerprint)
                   VALUES (%s, %s, %s, %s::uuid[], %s, '{}'::jsonb, 'queued', NULL, 'queued', 0.0, %s)""",
                (rid, str(user.id), pname, req.document_ids, req.preset, fp),
            )

    _report_executor.submit(_run_report_generation_job, rid, req, user.id)
    return {
        "report_id": rid,
        "status": "queued",
        "poll_url": f"/ddiq/report/{rid}/status",
        "cached": False,
    }


@router.get("/report/{report_id}/status")
def get_report_status(report_id: str, user: CurrentUser = Depends(get_current_user)):
    """Poll endpoint for the async report-generation flow. Cheap — only
    reads the row's status fields, not the full payload."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, status, progress_step, progress_percent, started_at,
                          finished_at, error, project_name
                   FROM ddiq_reports WHERE id = %s AND user_id = %s""",
                (report_id, str(user.id)),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Report not found")
    return {
        "report_id": str(row["id"]),
        "status": row["status"] or "done",
        "step": row["progress_step"],
        "percent": float(row["progress_percent"] or 0.0),
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "error": row["error"],
        "project_name": row["project_name"],
    }


def _generate_report_core(rid: str, req: "GenerateReportRequest", user_id, progress=None) -> "tuple[DDiQReportData, dict]":
    """Inner pipeline used by both the sync handler and the async worker.
    ``progress(step, percent)`` is invoked at each major step when given.

    Tenant isolation: ownership of every doc in ``req.document_ids`` is
    asserted at the boundary, and ``user_id`` is plumbed into every
    write (``ddiq_reports``, ``ddiq_project_areas``, ``ddiq_contracts``,
    ``ddiq_classified_parcels``). Read helpers also filter by user_id
    as defense in depth.
    """
    _assert_owns_documents(req.document_ids, user_id)

    if progress is None:
        progress = lambda step, pct: None  # noqa: E731

    t0 = time.time(); T = {}

    progress("gathering", 0.01)
    t = time.time()
    full_text = get_all_text_for_docs(req.document_ids, user_id=user_id)
    if not full_text.strip(): raise HTTPException(404, "No text")
    T["gather_s"] = round(time.time()-t, 2)

    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        f"SELECT id, filename FROM ddiq_documents "
        f"WHERE id::text IN ({','.join(['%s']*len(req.document_ids))}) AND user_id = %s",
        (*req.document_ids, str(user_id)),
    )
    _doc_rows = cur.fetchall(); cur.close(); conn.close()
    doc_names = [r[1] for r in _doc_rows]
    document_map = [{"id": str(r[0]), "filename": r[1]} for r in _doc_rows]

    t = time.time()
    try: meta = llm_json(EXTRACTION_SYSTEM, f"""Extract metadata.\n\n{rag_context(req.document_ids,'project name company location',3)}\n\nReturn: {{"projectName":"name","preparedFor":"company"}}""")
    except Exception: meta = {}
    T["meta_s"] = round(time.time()-t, 2)
    # Triple fallback: explicit request → LLM-extracted metadata → hard default.
    # The middle term can be None when the LLM returns {"projectName": null}, so
    # we can't rely on dict.get's default arg here.
    pname = req.project_name or meta.get("projectName") or "Wind Energy Project"
    pfor = req.prepared_for or meta.get("preparedFor") or "Client"
    progress("metadata", 0.05)

    # ── Build the report object up-front and persist it incrementally as
    # each phase completes. If the pipeline crashes mid-run, the row in
    # ddiq_reports.report_data still has the partial state from the last
    # successful checkpoint instead of the empty '{}' placeholder. The
    # final checkpoint at the end of this function is byte-identical to
    # what a single end-of-pipeline UPSERT would have written.
    from datetime import datetime
    report = DDiQReportData(
        projectName=pname, preparedBy="LAI Due Diligence System", preparedFor=pfor,
        date=datetime.now().strftime("%d %B %Y"),
        projectCenter={"lat": 53.0, "lng": 9.0},  # placeholder — overwritten after geocoding
        analyzedDocuments=doc_names,
        documentMap=document_map,
    )
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    t = time.time()
    progress("sections", 0.07)
    sections = [analyze_section(req.document_ids, s) for s in ["overview","land","permits","economics"]]
    T["sections_s"] = round(time.time()-t, 2)
    progress("sections_done", 0.55)  # sections is the bulk (~80% of wall time)
    report.sections = sections
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    t = time.time()
    progress("geocoding", 0.55)
    pc = geocode_project_location(sections)
    ploc = get_section_value(sections, "overview", "Location")
    logger.info(f"Center: {pc}, Location: {ploc}")
    T["geocode_s"] = round(time.time()-t, 2)

    progress("wea_extraction", 0.58)
    t = time.time(); weas = extract_wea_statuses(req.document_ids, full_text, sections, pc); T["wea_s"] = round(time.time()-t, 2)
    report.weaStatuses = weas
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    progress("infrastructure", 0.70)
    t = time.time(); infra = extract_infrastructure(req.document_ids, sections, pc); T["infra_s"] = round(time.time()-t, 2)
    report.infrastructure = infra
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # ── 13-Step Cadastral Pipeline ────────────────────────────────────────
    progress("cadastral", 0.78)
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

    # Stash cadastral artifacts on the report so the checkpoint right after
    # this phase is materially complete (parcels + project area + clearance
    # zones + validation + geojson) — even if findings/timeline/etc fail
    # later, the lawyer still gets a usable map from this checkpoint.
    lats0 = [w.lat for w in weas if w.lat != 0]; lngs0 = [w.lng for w in weas if w.lng != 0]
    center0 = {"lat": sum(lats0)/len(lats0) if lats0 else (pc[0] if pc else 53.0),
               "lng": sum(lngs0)/len(lngs0) if lngs0 else (pc[1] if pc else 9.0)}
    clearance_data0 = [
        {"wea_name": z.wea_name, "center_lat": z.center_lat, "center_lng": z.center_lng,
         "radius_m": z.radius_m, "polygon": z.polygon,
         "radius_source": z.__dict__.get("_radius_source")}
        for z in pipeline_result.clearance_zones
    ] if pipeline_result.clearance_zones else None
    pa0 = pipeline_result.project_area
    project_area_data0 = {
        "name": pa0.name, "polygon": pa0.polygon,
        "centroid_lat": pa0.centroid_lat, "centroid_lng": pa0.centroid_lng,
        "area_km2": pa0.area_km2, "source": pa0.source,
    } if pa0 and pa0.polygon else None
    report.projectCenter = center0
    report.parcels = parcels
    report.projectArea = project_area_data0
    report.clearanceZones = clearance_data0
    report.validation = pipeline_result.validation.dict() if pipeline_result.validation else None
    report.geojson = pipeline_result.geojson if pipeline_result.geojson else None
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # ── Cross-source reconciliation ──────────────────────────────────────
    # Compute canonical values for fields that multiple upstream sources
    # disagree about (the "four-conflicting-turbine-counts" failure mode
    # from the smoke test). Each downstream consumer uses the reconciled
    # value, so a single report can no longer show different numbers in
    # different sections. Precedence: cadastral > llm > regex > fallback.
    # See ``_reconcile.py`` for the rationale.

    # total_capacity_mw: the overview section's typed cell is a regex hit;
    # sum(WEA.rated_power_kw) is an LLM-extracted derived value. We take
    # the LLM-derived sum first when present, falling back to the regex.
    cap_str = get_section_value(sections, "overview", "Total Capacity")
    regex_total_mw: Optional[float] = None
    try:
        m = re.search(r"([\d,.]+)\s*MW", cap_str or "", re.IGNORECASE)
        if m:
            regex_total_mw = float(m.group(1).replace(",", "."))
    except Exception:
        regex_total_mw = None

    wea_capacities_kw = [w.rated_power_kw for w in weas if w.rated_power_kw]
    sum_total_mw: Optional[float] = (
        round(sum(wea_capacities_kw) / 1000.0, 3) if wea_capacities_kw else None
    )

    total_mw_reconciled = reconcile_numeric(
        "total_capacity_mw",
        [
            Candidate(value=sum_total_mw, provenance="llm",
                      source="sum(weas.rated_power_kw)/1000"),
            Candidate(value=regex_total_mw, provenance="regex",
                      source="overview.Total Capacity"),
        ],
    )
    total_mw: Optional[float] = total_mw_reconciled.value if total_mw_reconciled else None

    # turbine_count: parse_wea_count(overview cell) is regex; len(weas) is
    # the LLM extraction. When the LLM successfully extracted per-WEA
    # rows, that count is more trustworthy than a regex on a single cell.
    regex_wea_count: Optional[int] = None
    try:
        rc = parse_wea_count(get_section_value(sections, "overview", "Number of WEA"))
        regex_wea_count = rc if rc > 0 else None
    except Exception:
        regex_wea_count = None
    llm_wea_count: Optional[int] = len(weas) if weas else None

    turbine_count_reconciled = reconcile_numeric(
        "turbine_count",
        [
            Candidate(value=llm_wea_count, provenance="llm",
                      source="len(weas) — extract_wea_statuses"),
            Candidate(value=regex_wea_count, provenance="regex",
                      source="overview.Number of WEA"),
        ],
    )
    # Stash the reconciled count on the report so the UI and the cross-doc
    # consistency check both quote the same number.
    report.turbineCount = (
        int(turbine_count_reconciled.value)
        if turbine_count_reconciled and turbine_count_reconciled.value is not None
        else 0
    )

    # bundesland: keyword scan on the location string vs. bbox derivation
    # from the geocoded project_center. The bbox derivation is grounded
    # in real coordinates from Nominatim, so it gets the "cadastral"
    # precedence; the keyword scan is the regex layer.
    bl_from_keyword: Optional[str] = detect_bundesland(ploc) if ploc else None
    bl_from_coords: Optional[str] = bundesland_from_coords(*pc) if pc else None

    bundesland_reconciled = reconcile_categorical(
        "bundesland",
        [
            Candidate(value=bl_from_coords, provenance="cadastral",
                      source="bundesland_from_coords(project_center)"),
            Candidate(value=bl_from_keyword, provenance="regex",
                      source="detect_bundesland(location)"),
        ],
    )
    report.bundesland = bundesland_reconciled.value if bundesland_reconciled else None

    logger.info(
        "reconciled: total_mw=%s  turbine_count=%s  bundesland=%s",
        total_mw, report.turbineCount, report.bundesland,
    )

    progress("findings", 0.85)
    t = time.time()
    findings = generate_findings(req.document_ids, sections, total_capacity_mw=total_mw)
    T["findings_s"] = round(time.time()-t, 2)
    report.findings = findings  # may be augmented with deadline/rueckbau/grundbuch findings later
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # P0 #2: Timeline / deadline pass
    progress("timeline", 0.88)
    t = time.time()
    timeline = extract_timeline(req.document_ids, full_text)
    T["timeline_s"] = round(time.time()-t, 2)
    report.timeline = timeline
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # P0 #3: Cross-document consistency check
    progress("cross_doc", 0.91)
    t = time.time()
    cross_doc_findings = check_cross_doc_consistency(sections, weas, parcels, total_capacity_mw=total_mw)
    T["cross_doc_s"] = round(time.time()-t, 2)
    report.crossDocFindings = cross_doc_findings
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # P1 #9: Rückbaubürgschaft extraction
    progress("rueckbau", 0.93)
    t = time.time()
    rueckbau = extract_rueckbau_bond(req.document_ids)
    T["rueckbau_s"] = round(time.time()-t, 2)
    report.rueckbauBond = rueckbau
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # P1 #6: Grundbuch lessor-vs-owner check on secured parcels
    progress("grundbuch", 0.95)
    t = time.time()
    grundbuch_checks = check_grundbuch_match(req.document_ids, parcels)
    T["grundbuch_s"] = round(time.time()-t, 2)
    report.grundbuchChecks = grundbuch_checks
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # Promote material timeline events (urgent / expired) into Findings so
    # the lawyer's findings list reflects deadline pressure, not just
    # section issues.
    deadline_findings: list[Finding] = []
    for te in timeline:
        if te.urgency in ("expired", "urgent"):
            sev = "red" if te.urgency == "expired" else "yellow"
            deadline_findings.append(Finding(
                domain="Regulatory" if "permit" in te.kind or "objection" in te.kind else "General",
                severity=sev, kind="deadline",
                text=f"{te.kind.replace('_',' ').title()}: {te.description} (date: {te.date}).",
                legal_basis=te.legal_basis,
                evidence=te.evidence,
                quantification=Quantification(days_until_deadline=te.days_from_now,
                    rationale=f"Urgency='{te.urgency}'") if te.days_from_now is not None else None,
                recommended_action=("File renewal / Verlängerungsantrag immediately."
                                    if te.urgency == "urgent" else
                                    "Already past — investigate compliance gap and remediation path."),
            ))

    # Promote Rückbaubürgschaft gaps into findings
    if rueckbau and (rueckbau.amount_eur is None or rueckbau.sufficient is False):
        sev = "red" if rueckbau.amount_eur is None else "yellow"
        deadline_findings.append(Finding(
            domain="Regulatory", severity=sev, kind="rueckbau",
            text=("No Rückbaubürgschaft found in supplied documents — required under §35 Abs. 5 BauGB."
                  if rueckbau.amount_eur is None else
                  f"Rückbaubürgschaft amount ({rueckbau.amount_eur:.0f} EUR) appears insufficient vs. expected Rückbaukosten."),
            legal_basis="BauGB §35 Abs. 5",
            evidence=rueckbau.evidence,
            recommended_action="Obtain certified Bürgschaftsurkunde (bank or parent guarantee) before financial close.",
        ))

    # Promote Grundbuch mismatches into findings
    for gc in grundbuch_checks:
        if gc.owner_match is False and gc.match_confidence >= 0.5:
            deadline_findings.append(Finding(
                domain="Land", severity="red", kind="grundbuch",
                text=f"Lessor on parcel {gc.parcel_id} ({gc.lessor_name or 'unknown'}) does not match registered Grundbuch owner ({gc.registered_owner or 'unknown'}).",
                legal_basis="BGB §873 / Grundbuchordnung",
                evidence=gc.evidence,
                recommended_action="Obtain certified Grundbuchauszug and verify Vertretungsmacht of the signing party before closing.",
            ))

    # Combine all findings; section findings stay first, then derived.
    all_findings = list(findings) + deadline_findings
    report.findings = all_findings

    # ── Output guardrail pass (Track A item 5) ────────────────────────
    # Strip defensive-AI paragraphs ("Die vorliegenden Kontextausschnitte
    # enthalten keine Informationen zu …"), hedge phrases, and mark
    # mixed-language rows. Runs last so it sees every section + every
    # finding the pipeline produced. Pure mutation of the in-memory
    # ``report`` object — no I/O.
    section_lang_hint = "de"  # current corpus + clients are DE-first
    row_counts = {"defensive": 0, "hedges": 0, "mixed_lang": 0}
    for sec in report.sections:
        c = apply_to_rows(
            sec.rows,
            target_language=section_lang_hint,
            section_language_hint=section_lang_hint,
        )
        for k, v in c.items():
            row_counts[k] += v
    finding_counts = apply_to_findings(
        report.findings, target_language=section_lang_hint,
    )
    cross_doc_counts = apply_to_findings(
        report.crossDocFindings, target_language=section_lang_hint,
    )
    logger.info(
        "guardrail: rows defensive=%d hedges=%d mixed=%d  "
        "findings defensive=%d hedges=%d  cross_doc defensive=%d hedges=%d",
        row_counts["defensive"], row_counts["hedges"], row_counts["mixed_lang"],
        finding_counts["defensive"], finding_counts["hedges"],
        cross_doc_counts["defensive"], cross_doc_counts["hedges"],
    )

    # Final report_data checkpoint — byte-identical to what the
    # original single end-of-pipeline UPSERT wrote.
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # Auxiliary-table writes (ddiq_project_areas / ddiq_contracts /
    # ddiq_classified_parcels). These are write-once-at-end because they
    # have no ON CONFLICT handling — running them twice would create
    # duplicates. The report_data JSONB above already has a copy of all
    # this data; the relational tables are for query performance only.
    pa = pipeline_result.project_area
    uid = str(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Persist project area first (parent of contracts and parcels).
            # user_id is taken from the JWT, never from the request body
            # (AUTH_PLAN G3).
            if pa and pa.polygon:
                cur.execute(
                    """INSERT INTO ddiq_project_areas (user_id, name, polygon, centroid_lat, centroid_lng, area_km2, source, report_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (uid, pa.name, json.dumps(pa.polygon), pa.centroid_lat, pa.centroid_lng, pa.area_km2, pa.source, rid))

            # Persist contracts (parent of classified_parcels via matched_contract_id)
            for contract in pipeline_result.contracts:
                cur.execute(
                    """INSERT INTO ddiq_contracts (id, user_id, contract_ref, contract_type, contracting_entity, raw_text_excerpt, report_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (contract.contract_id, uid, contract.contract_ref, contract.contract_type,
                     contract.contracting_entity, contract.text_excerpt[:500], rid))
                for pref in contract.referenced_parcels:
                    cur.execute(
                        "INSERT INTO ddiq_contract_parcels (contract_id, parcel_identifier) VALUES (%s,%s)",
                        (contract.contract_id, pref))

            # Persist classified parcels last
            for cp in pipeline_result.classified_parcels:
                cur.execute(
                    """INSERT INTO ddiq_classified_parcels
                       (user_id, report_id, parcel_number, gemarkung, flur, normalized_id, polygon, polygon_source,
                        classification, color, confidence, matched_contract_id, classification_reason, area_ha, owner, linked_wea)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (uid, rid, cp.parcel_number, cp.gemarkung, cp.flur, cp.normalized_id,
                     json.dumps(cp.polygon), cp.polygon_source, cp.classification.value,
                     cp.color, cp.confidence, cp.matched_contract_id, cp.classification_reason,
                     cp.area_ha, cp.owner, cp.linked_wea))

    T["total_s"] = round(time.time()-t0, 2)
    return report, T


@router.post("/report/generate", response_model=GenerateReportResponse)
def generate_report(req: GenerateReportRequest, user: CurrentUser = Depends(get_current_user)):
    """Synchronous report generation — kept for back-compat. Blocks the
    request for the entire pipeline runtime (~30-60 min). Prefer
    /report/generate/async + /report/{id}/status for any UI-driven flow."""
    if not req.document_ids:
        raise HTTPException(400, "No documents selected")
    _assert_owns_documents(req.document_ids, user.id)

    fp = _compute_fingerprint(req.document_ids, req.preset, req.project_name, user.id)
    existing = _find_existing_report(fp, user.id)
    if existing and existing["status"] == "done":
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT report_data FROM ddiq_reports WHERE id = %s AND user_id = %s",
                    (existing["id"], str(user.id)),
                )
                row = cur.fetchone()
        if row and row["report_data"]:
            return GenerateReportResponse(
                report_id=str(existing["id"]),
                report=DDiQReportData(**row["report_data"]),
                timings={"cached": True},
            )

    rid = str(uuid.uuid4())
    # Track A item 6: wrap the sync pipeline so a crash mid-run marks the
    # row ``status='failed'`` with the error captured, rather than
    # leaving it stuck in whatever progress state the last incremental
    # persist wrote. Mirrors ``_run_report_generation_job``'s async
    # error-recovery shape.
    try:
        report, T = _generate_report_core(rid, req, user.id)
    except HTTPException as e:
        try:
            _update_report_progress(
                rid, status="failed", error=f"HTTP {e.status_code}: {e.detail}", user_id=user.id,
            )
        except Exception:
            pass  # row may not exist yet if the crash was pre-first-persist
        raise
    except Exception as e:
        logger.exception(f"sync report {rid} failed")
        try:
            _update_report_progress(rid, status="failed", error=str(e)[:500], user_id=user.id)
        except Exception:
            pass
        raise HTTPException(500, f"Report generation failed: {e}") from e

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ddiq_reports SET request_fingerprint = %s WHERE id = %s AND user_id = %s",
                (fp, rid, str(user.id)),
            )
    return GenerateReportResponse(report_id=rid, report=report, timings=T)

@router.get("/reports")
def list_reports(limit: int = 50, user: CurrentUser = Depends(get_current_user)):
    """Recent DDiQ reports for the Past Reports browser. Returns lightweight
    summary rows (no full report_data) so the listing is cheap even with
    hundreds of historical reports. Click-to-load fetches the full payload
    via GET /report/{id}."""
    if limit < 1: limit = 1
    if limit > 200: limit = 200
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, project_name, status, created_at, started_at, finished_at,
                          progress_percent, error, preset,
                          COALESCE(array_length(document_ids, 1), 0) AS doc_count,
                          COALESCE(jsonb_array_length(report_data->'findings'), 0) AS finding_count
                   FROM ddiq_reports
                   WHERE user_id = %s
                   ORDER BY created_at DESC NULLS LAST
                   LIMIT %s""",
                (str(user.id), limit),
            )
            rows = cur.fetchall()
    return {
        "reports": [
            {
                "report_id": str(r["id"]),
                "project_name": r["project_name"],
                "status": r["status"] or "done",
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
                "progress_percent": float(r["progress_percent"] or 0.0),
                "error": r["error"],
                "doc_count": r["doc_count"] or 0,
                "finding_count": r["finding_count"] or 0,
                "preset": r["preset"],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/report/{report_id}")
def get_report(report_id: str, user: CurrentUser = Depends(get_current_user)):
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM ddiq_reports WHERE id = %s AND user_id = %s",
        (report_id, str(user.id)),
    )
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: raise HTTPException(404, "Not found")
    return {"report_id": str(row["id"]), "created_at": row["created_at"].isoformat(),
            "project_name": row["project_name"], "report": row["report_data"]}


@router.delete("/report/{report_id}")
def delete_report(report_id: str, user: CurrentUser = Depends(get_current_user)):
    """Hard-delete a report and all its cadastral artifacts. Idempotent —
    returns 404 if the id is unknown OR owned by a different user. The
    report_data JSONB has its own copy of parcels/contracts/areas, but
    the relational rows in the auxiliary tables (ddiq_classified_parcels,
    ddiq_contracts, ddiq_project_areas) reference report_id without an
    FK CASCADE, so we clean them up explicitly in one transaction.

    ddiq_contract_parcels.contract_id has ON DELETE CASCADE on its FK to
    ddiq_contracts(id), so deleting the contracts row automatically
    drops its child contract_parcels rows.

    AUTH_PLAN G2: every DELETE includes the user filter. Load-then-mutate
    means even if the cascade DELETEs were re-ordered, a cross-tenant
    delete is structurally impossible.
    """
    uid = str(user.id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ddiq_reports WHERE id = %s AND user_id = %s",
                (report_id, uid),
            )
            if not cur.fetchone():
                raise HTTPException(404, "Report not found")
            cur.execute(
                "DELETE FROM ddiq_classified_parcels WHERE report_id = %s AND user_id = %s",
                (report_id, uid),
            )
            cur.execute(
                "DELETE FROM ddiq_contracts WHERE report_id = %s AND user_id = %s",
                (report_id, uid),
            )
            cur.execute(
                "DELETE FROM ddiq_project_areas WHERE report_id = %s AND user_id = %s",
                (report_id, uid),
            )
            cur.execute(
                "DELETE FROM ddiq_reports WHERE id = %s AND user_id = %s",
                (report_id, uid),
            )
    return {"deleted": True, "report_id": report_id}


@router.get("/report/{report_id}/geojson")
def get_report_geojson(report_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return GeoJSON FeatureCollection for GIS import (QGIS, ArcGIS, MapBox, etc.)."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT report_data FROM ddiq_reports WHERE id = %s AND user_id = %s",
        (report_id, str(user.id)),
    )
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
def validate_report(report_id: str, user: CurrentUser = Depends(get_current_user)):
    """Run validation checks on a generated report (Step 13)."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT report_data FROM ddiq_reports WHERE id = %s AND user_id = %s",
        (report_id, str(user.id)),
    )
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
def create_project_area(req: ProjectAreaRequest, user: CurrentUser = Depends(get_current_user)):
    """Define a project area polygon (Step 1 of Output Map).

    AUTH_PLAN G3: user_id is taken from the JWT, never from the request.
    """
    if len(req.polygon) < 3:
        raise HTTPException(400, "Polygon must have at least 3 points")

    from cadastral_pipeline import compute_centroid, polygon_area_km2
    centroid = compute_centroid(req.polygon)
    area = polygon_area_km2(req.polygon)

    pa_id = str(uuid.uuid4())
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""INSERT INTO ddiq_project_areas (id, user_id, name, polygon, centroid_lat, centroid_lng, area_km2, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'user_drawn')""",
        (pa_id, str(user.id), req.name, json.dumps(req.polygon), centroid[0], centroid[1], area))
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

def reap_orphans() -> None:
    """Mark queued/running reports as failed at startup. After a backend
    restart the in-process ThreadPoolExecutor tasks are gone, so any row
    left in those states is dead weight — without this the UI would
    poll forever. Safe with a single uvicorn worker (our deployment);
    with multiple workers this would race against siblings still
    booting and should move into a leader-election or external job
    runner."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE ddiq_reports
                       SET status = 'failed',
                           error = 'orphaned: backend restarted mid-job',
                           finished_at = NOW()
                       WHERE status IN ('queued','running')"""
                )
                n = cur.rowcount
        if n:
            logger.warning(f"reaped {n} orphaned report(s) from previous run")
    except Exception as e:
        logger.warning(f"orphan reap failed: {e}")


@router.on_event("startup")
async def startup():
    init_pool()
    init_db()
    reap_orphans()


@router.on_event("shutdown")
async def shutdown():
    close_pool()