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
import psycopg2
import psycopg2.extras

# Auth — AUTH_PLAN §4.4: every protected route depends on
# ``get_current_user``. The dep is imported from the microservice's
# shared ``auth_dep`` module so api.py and this router resolve to the
# same TokenIssuer/secret instance.
from auth_dep import get_current_user
from lai.common.auth import CurrentUser

# lai.common.llm / lai.common.exceptions imports moved to ``ddiq.llm``
# (H-5 phase 2). The shared SyncLlmClient singleton and the
# llm_call / llm_json helpers live there now; this file imports them
# at the top of the section below.

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

# Jurisdiction validator (H-2): scan finalised section / finding text
# for cross-Bundesland rule citations (e.g. Bayern's 10H BayBO mentioned
# in a Niedersachsen report). See ``lai.common.jurisdiction`` for the
# rule set and detection logic. We import only what we use to keep the
# top-of-file imports auditable.
from lai.common.jurisdiction import (
    JurisdictionWarning as _LcJurisdictionWarning,
    check_jurisdiction,
    detect_bundesland,
)

# Public-registry connectors (H-3): Nominatim geocoder + ALKIS INSPIRE
# WFS. The Postgres caches around them (``ddiq_geocode_cache`` /
# ``ddiq_parcel_cache``) stay DDiQ-internal because they use the
# microservice's psycopg2 pool; the actual HTTP + parsing + retries
# now live in ``lai.common.connectors``.
from lai.common.connectors import (
    AlkisClient,
    AlkisError,
    NominatimClient,
    NominatimError,
)

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

# NOTE: the embedding-client singleton is defined LATER in the file,
# next to the LLM-client singleton, because both depend on env vars
# (``EMBEDDING_URL``, ``LLM_URL``) that are read at module-import time
# further down. Search for ``_EMBEDDING_CLIENT`` for the init site.
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
# Nominatim + ALKIS configs come from ``lai.common.connectors`` now —
# search for ``_NOMINATIM_CLIENT`` / ``_ALKIS_CLIENT`` for the
# singletons. The legacy NOMINATIM_URL / NOMINATIM_UA constants were
# removed in commit (H-3); their values now live in the new
# ``NominatimConfig`` defaults.

# DB layer (schema + pool + lifecycle) moved to ``ddiq.db`` in H-5.
# ``DB_CONFIG`` and ``MAX_FILE_SIZE`` are re-exported here so call
# sites that still reference them by attribute (api.py, in-tree
# tests) don't break.
from ddiq.db import (  # noqa: E402 — re-exported for legacy callers
    DB_CONFIG,
    MAX_FILE_SIZE,
    SCHEMA_SQL,
    close_pool,
    get_conn,
    init_db,
    init_pool,
    reap_orphans,
)

# ═══════════════════════════════════════════════════════════════════════════════
# ALKIS WFS endpoints and Bundesland keyword table moved to:
#   - ``lai.common.connectors.config.ALKIS_WFS_ENDPOINTS`` (12 state WFS)
#   - ``lai.common.jurisdiction.BUNDESLAND_KEYWORDS`` (16-state keyword set)
# H-3 extraction commit. Kept here as one-line aliases for the few
# legacy call sites that referenced them by name — none in this file
# after the H-3 refactor, but a downstream consumer in cadastral_pipeline
# may still import ``ALKIS_WFS_ENDPOINTS`` for label display.
# ═══════════════════════════════════════════════════════════════════════════════

from lai.common.connectors.config import ALKIS_WFS_ENDPOINTS  # noqa: E402,F401 — re-export for legacy callers
from lai.common.jurisdiction import BUNDESLAND_KEYWORDS  # noqa: E402,F401 — re-export for legacy callers


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SETUP — moved to ``ddiq.db`` in H-5.
# Schema (SCHEMA_SQL), connection pool (init_pool / close_pool /
# get_conn / _PooledConn), and startup helpers (init_db,
# reap_orphans) live in ``ddiq/db.py`` now. The import block earlier
# in this file re-binds them onto the module namespace so call sites
# using ``ddiq_report.SCHEMA_SQL`` (or any other moved name) keep
# working unchanged.
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS — moved to ``ddiq.models`` in H-5.
# DocumentOut, DocumentListResponse, UploadDocResponse,
# AusgabeblattRow / Section, WEAStatus, InfraPoint, CadastralParcel,
# Evidence, Quantification, Finding, TimelineEntry, GrundbuchCheck,
# RueckbauBond, DDiQReportData, GenerateReportRequest /
# GenerateReportResponse, ProjectAreaRequest / Response — all now in
# ``ddiq/models.py``. Imported below and re-bound onto this module
# so call sites doing ``ddiq_report.Finding`` etc. keep working.
# ═══════════════════════════════════════════════════════════════════════════════


from ddiq.models import (  # noqa: E402 — re-exported for legacy callers
    AusgabeblattRow,
    AusgabeblattSection,
    CadastralParcel,
    DDiQReportData,
    DocumentListResponse,
    DocumentOut,
    Evidence,
    Finding,
    GenerateReportRequest,
    GenerateReportResponse,
    GrundbuchCheck,
    InfraPoint,
    ProjectAreaRequest,
    ProjectAreaResponse,
    Quantification,
    RueckbauBond,
    TimelineEntry,
    UploadDocResponse,
    WEAStatus,
)


# LLM + embedding infrastructure (H-5 phase 2). The module-level
# singletons (one ``SyncLlmClient`` + one ``SyncEmbeddingClient`` per
# uvicorn / Celery worker process) live in ``ddiq.llm`` now.
# ``EXTRACTION_SYSTEM`` is the shared system prompt every extractor
# sends as ``system``; the per-extractor module imports it directly
# from ``ddiq.llm``, so this re-export is purely for legacy callers
# still doing ``ddiq_report.EXTRACTION_SYSTEM``.
from ddiq.llm import (  # noqa: E402 — re-exported for legacy callers
    EXTRACTION_SYSTEM,
    embed_single,
    embed_texts,
    get_embedding_client,
    get_llm_client,
    llm_call,
    llm_json,
)

# Retrieval helpers (H-5 phase 2). Pgvector search + reranker call +
# context rendering + Evidence resolution all live in ``ddiq.rag``.
from ddiq.rag import (  # noqa: E402 — re-exported for legacy callers
    evidence_from_chunks,
    get_all_text_for_docs,
    rag_context,
    rag_context_with_meta,
    rerank,
    search_doc_chunks,
)

# Per-domain extractors (H-5 phase 2). The remaining inline extractors
# (extract_wea_statuses, extract_infrastructure, build_parcels,
# analyze_section, extract_parcel_refs) are too tightly coupled to
# this file's geocoding + cadastral_pipeline integration to extract
# without a third phase; they stay here for now.
from ddiq.extractors import (  # noqa: E402 — re-exported for legacy callers
    _finding_from_llm_obj,
    _findings_prompt_for_issue,
    _placeholder_finding_for_issue,
    check_cross_doc_consistency,
    check_grundbuch_match,
    extract_rueckbau_bond,
    extract_timeline,
    generate_findings,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CORE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

# get_conn() lives in ``ddiq.db`` (imported above).


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

# embed_texts() / embed_single() moved to ``ddiq.llm`` (H-5 phase 2).


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


# search_doc_chunks() / get_all_text_for_docs() / rerank() moved to
# ``ddiq.rag`` (H-5 phase 2).


# ── LLM + embedding singletons — moved to ``ddiq.llm`` (H-5 phase 2).
# The LlmConfig + SyncLlmClient + EmbeddingConfig + SyncEmbeddingClient
# construction happens once at ddiq.llm import time. Use
# ``get_llm_client()`` / ``get_embedding_client()`` if you need the
# raw client; otherwise call ``llm_call`` / ``llm_json`` /
# ``embed_texts`` / ``embed_single`` directly.
#
# Helpers moved with them: llm_call, llm_json (ddiq.llm) +
# rag_context, rag_context_with_meta, evidence_from_chunks (ddiq.rag).

# Connector singletons (H-3) — single httpx.Client per upstream.
# Pydantic-settings defaults are intentionally permissive: the actual
# endpoint URLs are hard-coded in lai.common.connectors.config (one
# per Bundesland for ALKIS; hosted OSM for Nominatim). Override via
# ``LAI_NOMINATIM_*`` / ``LAI_ALKIS_*`` env vars if a self-hosted
# Nominatim or an alternate WFS proxy is configured.
_NOMINATIM_CLIENT = NominatimClient()
_ALKIS_CLIENT = AlkisClient()


# ═══════════════════════════════════════════════════════════════════════════════
# GEOCODING + PARCEL POLYGON
# ═══════════════════════════════════════════════════════════════════════════════

def geocode_address(
    address: str,
    expected_bundesland: Optional[str] = None,
) -> Optional[tuple[float, float]]:
    """Geocode ``address`` to ``(lat, lng)`` with DDiQ-side caching.

    Thin shim that wraps :class:`lai.common.connectors.NominatimClient`
    with a Postgres-backed cache (``ddiq_geocode_cache``). The cache
    stays DDiQ-internal because it uses this microservice's psycopg2
    pool; the actual HTTP + tenacity retry + bbox plausibility gate
    now live in the shared connector.

    Lookup → cache fast path (only non-expired rows; legacy rows with
    NULL ``expires_at`` are treated as expired).

    Miss → call the connector. The bbox gate inside the connector
    rejects wrong-Bundesland results (the "Cuxhaven→Bremen" failure
    mode from the wind-lawyer's smoke test). Rejected results are
    NOT cached; the next call with a more specific address gets a
    fresh attempt.

    Returns ``None`` on:
      - empty address
      - Nominatim returned no result
      - bbox gate rejected the result
      - transport failure after the connector's retry budget exhausted
        (we swallow connector errors here because geocoding is a
        nice-to-have for the report; mid-pipeline crashes from a
        Nominatim outage would be disproportionate)
    """
    if not address or not address.strip():
        return None
    conn = get_conn()
    cur = conn.cursor()
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
        result = _NOMINATIM_CLIENT.geocode(
            address, expected_bundesland=expected_bundesland,
        )
    except NominatimError as e:
        # The connector exhausts retries / surfaces invalid responses
        # as typed errors. Log + return None — the bbox gate's
        # warning log already fires inside the connector when that
        # specific rejection happens.
        logger.warning("Geocoding failed for %r: %s", address, e)
        cur.close(); conn.close()
        return None

    if result is None:
        # No result from Nominatim OR bbox-rejected. Don't cache.
        cur.close(); conn.close()
        return None

    lat, lng = result
    # ``ON CONFLICT (address) DO UPDATE`` so a stale row (NULL
    # ``expires_at`` from before the TTL column existed, or an
    # expired entry) gets refreshed instead of silently locking
    # itself out for the next fetch cycle.
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
    return (lat, lng)

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

# NOTE: ``detect_bundesland`` is imported from ``lai.common.jurisdiction``
# at the top of this file. The legacy inline definition that used to
# live here shadowed the import; deleted in H-3 (commit follows).

def alkis_query_parcels(
    lat: float,
    lng: float,
    bundesland: str,
    radius_m: float = 150,
) -> list[dict]:
    """Query ALKIS INSPIRE WFS with DDiQ-side caching.

    Thin shim that wraps :class:`lai.common.connectors.AlkisClient` with
    a Postgres cache (``ddiq_parcel_cache``). The HTTP + tenacity retry
    + JSON/GML shape detection live inside the connector.

    Cache lookup → fast path on non-expired rows (Track A item 6 added
    the ``expires_at`` column; pre-TTL rows are treated as expired and
    re-fetched once).

    Returns ``[]`` on:
      - unsupported Bundesland (city-states have no WFS)
      - empty result inside the bbox
      - connector error (retry exhausted, invalid response) — we
        swallow rather than crash mid-pipeline; the report just shows
        no ALKIS data for that WEA
    """
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
    except Exception:
        # Cache lookup failure is non-fatal — fall through to the
        # connector. The cache write path below has its own except
        # block.
        pass

    try:
        parcels = _ALKIS_CLIENT.query_parcels(
            lat=lat, lng=lng, bundesland=bundesland, radius_m=radius_m,
        )
    except AlkisError as e:
        logger.warning("ALKIS WFS failed for %s: %s", bundesland, e)
        return []

    # Cache. ON CONFLICT … DO UPDATE refreshes ``expires_at`` so stale
    # rows self-heal on the next miss.
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
    except Exception:
        # Cache write failure shouldn't drop the result — return what
        # we got.
        pass
    return parcels


# NOTE: ``_parse_alkis_feature`` and ``_parse_alkis_xml`` moved to
# ``lai.common.connectors._parsers`` (H-3). Same logic, with the
# Track A item 6 bug-fix preserved (break only on successful parse,
# not on first non-None key). Tests for the pure parsers live in
# ``tests/unit/common/connectors/test_parsers.py``.


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

# EXTRACTION_SYSTEM moved to ``ddiq.llm`` (H-5 phase 2) so the per-
# domain extractor modules (ddiq.extractors.*) can import it without
# circling back through this file.


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
            # E10: evidence + anchor are real AusgabeblattRow fields now,
            # so they serialize through model_dump → JSONB + API response
            # (was a __dict__ shadow attr that was silently dropped).
            row = AusgabeblattRow(
                label=label, value=val,
                ampel=result.get("ampel") if result.get("ampel") in ("green","yellow","red") else None,
                note=note,
                evidence=evidence_from_chunks(reranked, result.get("evidence_chunks", [])),
                anchor=anchor,
            )
            rows.append(row)
        except Exception as e:
            logger.error(f"Section {section_id}/{label}: {e}")
            rows.append(AusgabeblattRow(label=label, value="Could not extract", ampel="red", note=f"Error: {str(e)[:80]}"))
    return AusgabeblattSection(id=section_id, title=title_map.get(section_id, section_id.title()), rows=rows)


# extract_timeline / check_cross_doc_consistency / extract_rueckbau_bond
# / check_grundbuch_match — moved to ``ddiq.extractors.*`` (H-5 phase 2).


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


# _findings_prompt_for_issue / _finding_from_llm_obj /
# _placeholder_finding_for_issue / generate_findings — moved to
# ``ddiq.extractors.findings`` (H-5 phase 2).


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


# ─── Background-task queue for /report/generate/async (H-4) ───────────────
# /report/generate is synchronous — fine for direct API use, but the
# 30-60 minute report blocks a request the whole time. Browsers and
# proxies time out long before that. The async path lets callers POST,
# get a {report_id, status:"queued"} immediately, and poll
# /report/{id}/status (or /report/{id}) for completion.
#
# H-4: moved from an in-process ``ThreadPoolExecutor(max_workers=2)`` to
# a Redis-backed Celery queue. The thread-pool design had two prod
# problems: (1) docker restarts orphaned reports mid-pipeline because
# the threads die with the API process, (2) hard ceiling of 2
# concurrent reports per backend. Celery's acks_late semantics put a
# crashed worker's message back on the queue automatically, and you
# scale by running more ``lai-worker`` containers. See
# ``micro-services/worker.py`` for the worker module + Celery config.
from worker import generate_report_task


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
                    (rid, str(user_id), project_name, doc_ids, preset, json.dumps(report.model_dump())),
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

    # Enqueue via Celery (H-4). The worker process picks up the message
    # from the ``ddiq`` queue on the shared Redis broker. Returns a
    # Celery ``AsyncResult`` whose ``.id`` is the task UUID; we capture
    # it on the row for traceability (correlate Celery logs to DB rows).
    #
    # ``model_dump(mode="json")`` because Celery JSON-serialises the
    # message body; ``UUID`` instances aren't JSON-serialisable by
    # default, but ``mode="json"`` converts them to strings. The worker
    # rehydrates via ``GenerateReportRequest.model_validate``.
    async_result = generate_report_task.delay(
        rid, req.model_dump(mode="json"), str(user.id),
    )

    # Best-effort: attach the celery task id to the row so an operator
    # can grep flower / worker logs by report_id. Non-fatal if the
    # column doesn't exist yet (gets added by init_db on next boot).
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ddiq_reports SET celery_task_id = %s "
                    "WHERE id = %s AND user_id = %s",
                    (async_result.id, rid, str(user.id)),
                )
    except Exception:
        pass  # column missing or transient DB issue — non-blocking

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
    report.validation = pipeline_result.validation.model_dump() if pipeline_result.validation else None
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

    # ── Jurisdiction scan (H-2) ───────────────────────────────────────
    # Cross-Bundesland rule check. For each section row + finding (the
    # guardrail-cleaned versions, so we don't false-match canonical
    # "Nicht in den vorgelegten Dokumenten enthalten" placeholders),
    # detect mentions of Bundesland-specific rules — e.g. Bayern's 10H
    # BayBO setback — that don't belong to the matter's actual state.
    # Empty when ``report.bundesland is None`` (the validator
    # short-circuits) so this is safe to call unconditionally.
    #
    # De-duplicates across the whole report: if the same rule is cited
    # in three different rows, only one warning is emitted (keyed by
    # rule label). That matches the chat-side validator's behaviour and
    # avoids drowning the UI in repetition.
    report.jurisdictionWarnings = []
    if report.bundesland:
        seen_rule_labels: set[str] = set()
        scan_texts: list[str] = []
        for sec in report.sections:
            for row in sec.rows:
                val = (getattr(row, "value", None) or "").strip()
                note = (getattr(row, "note", None) or "").strip()
                if val:
                    scan_texts.append(val)
                if note:
                    scan_texts.append(note)
        for f in report.findings:
            t = (getattr(f, "text", None) or "").strip()
            if t:
                scan_texts.append(t)
        for f in report.crossDocFindings:
            t = (getattr(f, "text", None) or "").strip()
            if t:
                scan_texts.append(t)

        for txt in scan_texts:
            for w in check_jurisdiction(txt, report.bundesland):
                if w.rule_label in seen_rule_labels:
                    continue
                seen_rule_labels.add(w.rule_label)
                # Convert the dataclass to a dict so the report JSONB
                # round-trips through Postgres + Pydantic without
                # depending on lai.common imports at deserialisation
                # time. Shape matches serve_rag's JurisdictionWarningOut.
                report.jurisdictionWarnings.append({
                    "rule_label": w.rule_label,
                    "rule_bundesland": w.rule_bundesland,
                    "expected_bundesland": w.expected_bundesland,
                    "excerpt": w.excerpt,
                })

    if report.jurisdictionWarnings:
        logger.warning(
            "jurisdiction: %d cross-Bundesland rule citation(s) detected "
            "(matter is in %s). Rules: %s",
            len(report.jurisdictionWarnings),
            report.bundesland,
            ", ".join(
                f"{w['rule_label']} ({w['rule_bundesland']})"
                for w in report.jurisdictionWarnings
            ),
        )
    else:
        logger.info(
            "jurisdiction: no cross-Bundesland rule citations "
            "(matter bundesland=%s; scanned %d text fragments)",
            report.bundesland, len(scan_texts) if report.bundesland else 0,
        )

    # Final report_data checkpoint — byte-identical to what the
    # original single end-of-pipeline UPSERT wrote.
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id)

    # Auxiliary-table writes (ddiq_project_areas / ddiq_contracts /
    # ddiq_classified_parcels). The report_data JSONB above already has a
    # copy of all this data; the relational tables are for query
    # performance only.
    #
    # E7: these tables are keyed by report_id and have no natural unique
    # key for a per-row ON CONFLICT (and a regeneration can yield a
    # different parcel/contract count, so per-row upsert would leave
    # stale rows from the prior run). The correct idempotency is
    # delete-then-insert by report_id, all inside one transaction:
    # re-running the same report fully replaces its aux rows instead of
    # appending duplicates. ddiq_contract_parcels cascade-deletes via its
    # FK to ddiq_contracts (ON DELETE CASCADE). user_id is included in
    # the delete predicate as defense in depth (AUTH_PLAN G3).
    pa = pipeline_result.project_area
    uid = str(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ddiq_classified_parcels WHERE report_id = %s AND user_id = %s",
                (rid, uid),
            )
            cur.execute(
                "DELETE FROM ddiq_contracts WHERE report_id = %s AND user_id = %s",
                (rid, uid),
            )
            cur.execute(
                "DELETE FROM ddiq_project_areas WHERE report_id = %s AND user_id = %s",
                (rid, uid),
            )

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
    pname = req.project_name or "Wind Energy Project"

    # E5: claim the fingerprint atomically at row creation, mirroring the
    # async path. The old code set request_fingerprint in an UPDATE only
    # AFTER the 30-60 min pipeline finished, so two concurrent identical
    # requests both passed the dedup check above and both ran the full
    # pipeline. Worse, since E4 made the fingerprint index UNIQUE, the
    # second request's post-pipeline UPDATE would raise UniqueViolation
    # after an hour of work. Pre-inserting with the fingerprint + a
    # partial-index ON CONFLICT collapses the race to a cheap, immediate
    # claim: the loser does no pipeline work.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ddiq_reports
                       (id, user_id, project_name, document_ids, preset,
                        report_data, status, started_at, progress_step,
                        progress_percent, request_fingerprint)
                   VALUES (%s, %s, %s, %s::uuid[], %s, '{}'::jsonb,
                           'running', NOW(), 'starting', 0.0, %s)
                   ON CONFLICT (request_fingerprint)
                       WHERE request_fingerprint IS NOT NULL
                   DO NOTHING
                   RETURNING id""",
                (rid, str(user.id), pname, req.document_ids, req.preset, fp),
            )
            claimed = cur.fetchone()

    if claimed is None:
        # A concurrent request already holds this fingerprint. Don't run a
        # duplicate pipeline — surface the in-flight (or just-finished)
        # row instead of recomputing.
        other = _find_existing_report(fp, user.id)
        if other and other["status"] == "done":
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT report_data FROM ddiq_reports WHERE id = %s AND user_id = %s",
                        (other["id"], str(user.id)),
                    )
                    row = cur.fetchone()
            if row and row["report_data"]:
                return GenerateReportResponse(
                    report_id=str(other["id"]),
                    report=DDiQReportData(**row["report_data"]),
                    timings={"cached": True},
                )
        raise HTTPException(
            409,
            "An identical report is already being generated; poll "
            "/ddiq/reports for it.",
        )

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

    # Fingerprint was set at creation (above); the pipeline's checkpoint
    # upserts touch report_data by id and leave it intact. Mark done.
    _update_report_progress(rid, status="done", step="done", percent=1.0, user_id=user.id)
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


# ProjectAreaRequest / ProjectAreaResponse moved to ``ddiq.models`` in
# H-5; imported at top of file.


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

# reap_orphans() lives in ``ddiq.db`` (imported above).


@router.on_event("startup")
async def startup():
    init_pool()
    init_db()
    reap_orphans()


@router.on_event("shutdown")
async def shutdown():
    close_pool()