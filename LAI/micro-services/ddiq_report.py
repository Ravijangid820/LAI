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
from uuid import UUID
from datetime import datetime, timezone
import os, re, json, time, uuid, logging, math, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import psycopg2
import psycopg2.extras

# Auth — AUTH_PLAN §4.4: every protected route depends on
# ``get_current_user``. The dep is imported from the microservice's
# shared ``auth_dep`` module so api.py and this router resolve to the
# same TokenIssuer/secret instance.
from auth_dep import get_current_user
from lai.common import audit
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
from _guardrail import apply_to_findings, apply_to_rows, detect_defensive_ai, detect_language

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
    AlkisRetryExhaustedError,
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
    filter_outlier_points, _MAX_WEA_SPREAD_KM,
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
    ParkFacts,
    ProjectFacts,
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
from ddiq import vlm_ocr  # noqa: E402 — scanned-PDF OCR via the vision LLM


# ═══════════════════════════════════════════════════════════════════════════════
# CORE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

# get_conn() lives in ``ddiq.db`` (imported above).


def clean_value(val, fallback: str = "Not specified in documents") -> str:
    s = str(val).strip()
    return fallback if s.lower() in ("null", "none", "n/a", "na", "nil", "undefined", "") else s


# ── Header-fact guards ───────────────────────────────────────────────
# Small, deterministic sanity checks that keep an extraction slip from
# reaching the report header — the first thing a partner reads.

# A name carrying a wind/solar-park token is a project name even if it
# contains a number; without one, a trailing house number or a
# street-suffix marks it as a street address.
_PROJECT_NAME_TOKENS = re.compile(
    r"(?i)\b(windpark|windenergie\w*|wind\s*farm|b[üu]rgerwindpark|solarpark)\b"
)
_ADDRESS_TAIL = re.compile(r"\d{1,4}\s*[a-z]?$")


def _looks_like_address(name: Optional[str]) -> bool:
    """True when ``name`` looks like a street address, not a project name.

    The lightweight metadata-extraction LLM occasionally returns the
    applicant's address line as the projectName — the real
    "Sönke-Nissen-Koog 58" smoke-test bug. A "Windpark…/Windenergie…"
    token always means project name; otherwise a trailing house number
    or a street suffix ("straße"/"str.") marks it as an address.
    """
    if not name:
        return False
    s = str(name).strip()
    if _PROJECT_NAME_TOKENS.search(s):
        return False
    low = s.lower()
    return bool(_ADDRESS_TAIL.search(s)) or "straße" in low or low.endswith("str.")


# When the LLM can't find a value it answers with a phrase like "Nicht im
# Kontext enthalten" — an honest refusal that must never be stored as if
# it were the actual value. Caught one in the wild on KS's first report
# where the project_name row in the DB read "Nicht im Kontext enthalten".
_REFUSAL_PATTERNS = re.compile(
    r"(?i)("
    r"nicht\s+(?:im\s+kontext|in\s+den\s+(?:dokumenten|unterlagen))(?:\s+enthalten)?"
    r"|nicht\s+verf[üu]gbar"
    r"|nicht\s+angegeben"
    r"|nicht\s+spezifiziert"
    r"|nicht\s+ermittelbar"
    r"|nicht\s+bekannt"
    r"|keine\s+angaben?"
    r"|unbekannt"
    r"|not\s+(?:in\s+(?:the\s+)?(?:context|documents)|provided|specified|available|known|mentioned)"
    r"|no\s+information"
    r"|i\s+(?:do\s+not|don['’]?t)\s+know"
    r"|cannot\s+(?:determine|be\s+determined)"
    r"|insufficient\s+(?:context|information)"
    r")"
)


def _looks_like_refusal(value: Optional[str]) -> bool:
    """True when ``value`` is an LLM honest-refusal phrase, not a real value.

    Used to gate header fields where a refusal would look like garbage to
    the reader (e.g. project name displayed in the report title). Content
    fields can keep refusals — they are useful as ampel=null hints.
    """
    if not value:
        return False
    return bool(_REFUSAL_PATTERNS.search(str(value).strip()))


# Onshore WEA rated power tops out near 7 MW in this corpus; an order of
# magnitude above (e.g. 22000 kW reported for an Enercon E-70, which is
# really ~2300 kW) is an extraction error. Letting it through made 8
# turbines sum to a phantom 176 MW total in the Lamstedt smoke test.
_MAX_PLAUSIBLE_WEA_KW = 10000.0  # 10 MW — generous ceiling for onshore WEA


def _plausible_rated_kw(v) -> Optional[float]:
    """Rated power in kW if physically plausible for an onshore WEA, else
    ``None`` — so an order-of-magnitude misextraction can't drive the
    reconciled total capacity (the reconciler then falls back to the
    document-stated total)."""
    try:
        n = float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if n is None:
        return None
    return n if 0 < n <= _MAX_PLAUSIBLE_WEA_KW else None

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

    Scanned PDFs (no text layer) are first routed through the vision LLM
    (:mod:`ddiq.vlm_ocr`) because Tesseract misreads turbine type designations
    on noisy scans — it read an Enercon **E-70** as **E-79** on the Lamstedt
    Änderungsgenehmigung. Any VLM failure falls back to the Tesseract path so
    ingestion never crashes on a bad page.
    """
    if vlm_ocr.vlm_ocr_enabled():
        try:
            if not vlm_ocr.pdf_has_text_layer(file_bytes):
                text, pages = vlm_ocr.vlm_ocr_pdf(file_bytes)
                logger.info("extract_pdf_text: VLM OCR transcribed %d page(s)", pages)
                return text, pages
        except Exception as e:  # noqa: BLE001 — degrade to Tesseract, never crash ingestion
            logger.warning("extract_pdf_text: VLM OCR failed (%s); falling back to Tesseract", e)
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


def _org_str(user) -> Optional[str]:
    """Helper — stringified ``org_id`` (membership only post-revert).

    Visibility is back to private-by-default after Phase B revert, but the
    column is still stamped on every new row so the explicit-share flow
    (Step 2 of Path A) and admin/audit queries have org context to use.
    """
    return str(user.org_id) if getattr(user, "org_id", None) else None


def _assert_can_view_documents(doc_ids, user_id) -> None:
    """Raise 404 if ``user_id`` cannot view every document in ``doc_ids``.

    Path A Step 2: viewability is creator OR explicit share. A user can
    include documents shared with them in their own report — that's the
    natural workflow (a colleague shares the source PDFs, the recipient
    runs their own analysis). Returning 404 (not 403) matches the
    no-existence-leak posture used everywhere else.
    """
    if not doc_ids:
        return
    conn = get_conn(); cur = conn.cursor()
    try:
        ph = ",".join(["%s"] * len(doc_ids))
        cur.execute(
            f"""SELECT COUNT(*) FROM ddiq_documents
                WHERE id::text IN ({ph})
                  AND (user_id = %s
                       OR EXISTS (SELECT 1 FROM ddiq_document_shares s
                                  WHERE s.document_id = ddiq_documents.id
                                    AND s.user_id = %s))""",
            (*doc_ids, str(user_id), str(user_id)),
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
    except AlkisRetryExhaustedError as e:
        # WFS is unreachable (transport / 5xx exhausted — e.g. HTTP 530). Do
        # NOT swallow this as an empty result: that hides a systemic outage as
        # "no parcels here", resetting the pipeline's circuit-breaker on every
        # point so it grinds all ~hundreds of grid points (each with its own
        # retries) for hours. Propagate so _step2_collect_parcels' breaker
        # trips, aborts the cadastral lookup, and degrades to estimated parcels.
        logger.warning("ALKIS WFS unreachable for %s: %s — aborting lookup", bundesland, e)
        raise
    except AlkisError as e:
        # Non-systemic: unsupported Bundesland (city-states), a 4xx for this
        # point, or a parse miss = "no data here", not an outage. Swallow and
        # continue to the next point.
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


def _set_section_value(sections, section_id, label, value) -> bool:
    """Set an existing section cell's value (matched by id + label). Returns
    True if a matching row was found and updated. Used by the deterministic
    reconciler to render a canonical ``ProjectFacts`` fact back into the
    overview row (the §5.4 facts-ledger pattern — same as forcing the
    turbine count into the cell), never to create a new row."""
    for sec in sections:
        if sec.id == section_id:
            for row in sec.rows:
                if row.label == label:
                    row.value = value
                    return True
    return False

_WEA_COUNT_RE = re.compile(
    r"(\d+)\s*(?:WEA|WKA|Windenergieanlagen?|Windkraftanlagen?|Anlagen|Turbines?)\b",
    re.IGNORECASE,
)


def parse_wea_count(value):
    """Best-effort turbine count from a free-text cell.

    The cell is often prose ("… insgesamt 10 Anlagen …"), not a bare
    number, so grabbing the first integer is wrong — it can latch onto a
    date ("06.04.2005" → 6) or a parcel number. Prefer an explicit
    turbine-count phrase ("10 Anlagen", "10 WEA", "10 Windenergieanlagen"),
    then fall back to a leading bare integer, else 0.
    """
    if not value:
        return 0
    m = _WEA_COUNT_RE.search(value)
    if m:
        return int(m.group(1))
    m = re.match(r"\s*(\d+)\b", value)
    return int(m.group(1)) if m else 0


# A section cell that explicitly disclaims a determinable figure. When the
# analysis itself says the number can't be established (often because the
# documents cover more than one wind park), the header must NOT then assert a
# confident count/capacity — it should show "unknown", matching the prose.
_UNDETERMINABLE_RE = re.compile(
    r"(?i)(unbekannt|unbestimmbar|nicht\s+eindeutig|keine\s+vollständige|"
    r"nicht\s+ableitbar|nicht\s+bestimmbar|lässt\s+sich\s+nicht|"
    r"nicht\s+eindeutig\s+hervor|unzureichende?\s+Daten)"
)


def _signals_undeterminable(cell: str) -> bool:
    """True when a section cell explicitly says the figure can't be
    determined from the documents."""
    return bool(cell) and bool(_UNDETERMINABLE_RE.search(cell))


# Timeline kinds that represent a genuine deadline/obligation whose expiry is
# an ongoing risk — only these are promoted to RED/YELLOW findings. An
# informational historical milestone (``other``/``sonstiges``,
# ``construction_milestone``/``bauabschnitt``) is not promoted.
_DEADLINE_KIND_RE = re.compile(
    r"(?i)(permit|genehmigung|expir|ablauf|frist|renewal|verläng|lease|pacht|"
    r"objection|widerspruch|bond|bürgschaft|warranty|gewährleist|deadline)"
)

# A finding whose entire text is a bare "not in the documents" stub, with no
# stated subject. A genuine missing-document finding names what's missing and
# is longer than this; a stub is noise that erodes trust.
_CONTENTLESS_FINDING_RE = re.compile(
    r"(?i)^\W*(nicht\s+(in\s+den\s+)?(vorgelegten|vorliegenden)\s+(dokumenten|unterlagen)\s+"
    r"(enthalten|gefunden|vorhanden)|nicht\s+enthalten|keine\s+angaben|"
    r"not\s+(found|in\s+(the\s+)?documents)|n/?a)\W*$"
)


def _is_contentless_finding(f) -> bool:
    """True for a finding that says nothing actionable — empty text, or a bare
    'not in the documents' stub with no stated subject."""
    text = (getattr(f, "text", "") or "").strip()
    if len(text) < 12:
        return True
    return bool(_CONTENTLESS_FINDING_RE.match(text))


_PER_UNIT_MW_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*MW\s*(?:pro|je|/)\s*(?:Einheit|Anlage|WEA|Turbine)",
    re.IGNORECASE,
)
_UNIT_COUNT_RE = re.compile(
    r"(\d+)\s*(?:Einheiten|Einheit|Anlagen|WEA|Windenergieanlagen?|Turbinen)\b",
    re.IGNORECASE,
)
_TOTAL_MW_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*MW\s*(?:gesamt|insgesamt|Gesamtleistung)",
    re.IGNORECASE,
)


def _parse_explicit_park_size(*cells: str) -> tuple[Optional[int], Optional[float]]:
    """Pull an explicit, document-stated park size out of the section cells.

    Recognises the deliberate figures a permit states — e.g. "2 MW pro Einheit
    für 10 Einheiten", "10 Anlagen … je 2 MW", or "20 MW Gesamtleistung" — and
    returns ``(turbine_count, total_mw)``. These are far more trustworthy than
    counting extracted per-WEA rows, which can merge a neighbouring park's
    turbines or duplicates. Either element is ``None`` when not stated.
    """
    text = " ".join(c for c in cells if c)
    if not text:
        return None, None
    cnt_m = _UNIT_COUNT_RE.search(text)
    per_m = _PER_UNIT_MW_RE.search(text)
    tot_m = _TOTAL_MW_RE.search(text)
    count = int(cnt_m.group(1)) if cnt_m else None
    per_mw = float(per_m.group(1).replace(",", ".")) if per_m else None
    if tot_m:
        total = round(float(tot_m.group(1).replace(",", ".")), 3)
    elif count and per_mw:
        total = round(count * per_mw, 3)
    else:
        total = None
    return count, total

def _parse_location_fields(location: str) -> dict:
    """Pull Gemeinde / Landkreis / Bundesland / Gemarkung out of the
    section's Location value.

    The overview "Location" row is produced in a labelled form, e.g.
    ``"Bundesland: Niedersachsen; Landkreis: Cuxhaven; Gemeinde:
    Lamstedt; Gemarkung: Lamstedt"``. Parsing the structured fields lets
    us geocode most-specific-first (Gemeinde + Landkreis + Bundesland)
    instead of throwing the whole noisy string — or, worse, a single
    token — at Nominatim. Returns whatever labels are present; missing
    ones are simply absent.
    """
    fields: dict[str, str] = {}
    for label in ("Gemeinde", "Landkreis", "Bundesland", "Gemarkung"):
        m = re.search(rf"{label}\s*[:=]\s*([^;,\n]+)", location, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val and val.lower() not in _LOC_NULLISH:
                fields[label.lower()] = val
    return fields


def geocode_project_location(sections):
    location = get_section_value(sections, "overview", "Location")
    # Detect the project's Bundesland from the same location string we're
    # about to geocode — chains seamlessly because ``detect_bundesland``
    # returns the exact lowercase keys ``BUNDESLAND_BBOX`` uses. Falls
    # through to ``None`` (no gate) when location doesn't name a state.
    expected_bl = detect_bundesland(location) if location else None
    if location:
        f = _parse_location_fields(location)
        gem, lk, bl, gemk = (f.get("gemeinde"), f.get("landkreis"),
                             f.get("bundesland"), f.get("gemarkung"))
        # Most-specific-first candidate queries. We deliberately do NOT
        # fall back to a bare single token (e.g. just "Niedersachsen"),
        # which previously geocoded to the STATE CENTROID ~80 km from the
        # real site (the Lamstedt→Lüneburg-Heath miss). Every candidate
        # carries at least the municipality or Gemarkung so the gate is
        # meaningful.
        candidates = [
            ", ".join([p for p in (gem, lk, bl) if p] + ["Germany"]) if gem else None,
            ", ".join([p for p in (gem, bl) if p] + ["Germany"]) if gem and bl else None,
            ", ".join([p for p in (gemk, lk, bl) if p] + ["Germany"]) if gemk else None,
            ", ".join([p for p in (lk, bl) if p] + ["Germany"]) if lk and bl else None,
        ]
        # If the location wasn't in the labelled form, fall back to the
        # whole string once (still gated by Bundesland) — but never the
        # destructive per-token split.
        if not any(candidates):
            candidates = [location]
        for q in candidates:
            if not q:
                continue
            coords = geocode_address(q, expected_bundesland=expected_bl)
            if coords:
                return coords
    name = get_section_value(sections, "overview", "Project Name")
    if name:
        clean = re.sub(r"(?i)windpark|windenergie|wind\s*farm", "", name).strip()
        # Only geocode the project NAME when we can gate it to a known
        # Bundesland (from the location text or the name itself). A bare
        # name with no detectable state is the destructive guess that
        # lands in the wrong region — e.g. "Windpark Feldmark" → "Feldmark"
        # → a Castrop-Rauxel district in NRW, ~300 km from a Niedersachsen
        # site. When the document gives no usable location AND the name
        # carries no state, we return no pin rather than fabricate one the
        # document doesn't support.
        name_bl = expected_bl or detect_bundesland(clean)
        if clean and name_bl:
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
         "question": (
             "Project site: Bundesland, Landkreis, Gemeinde, Gemarkung. "
             "Cite the Lageplan or Erläuterungsbericht. "
             "Return ONLY the location where the wind turbines stand or are "
             "planned to stand. Do NOT return a party's registered office "
             "(Sitz, Geschäftsadresse, HRB-Sitz, Hauptsitz, "
             "Postanschrift) of the Pächterin / Verpächter / Eigentümer / "
             "Projektgesellschaft / Käuferin / Verkäuferin — those usually "
             "appear in party preambles (§§ 1–2 of Pacht-/Kaufverträge) "
             "and on company letterheads, NOT on the Lageplan. If the doc "
             "only gives a corporate office and no site address, return "
             "null rather than the office."
         )},
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


# E1 (sections): fan-out for the per-question RAG+LLM calls within a
# section. Each question is independent (its own retrieve + extract), so
# they parallelize cleanly over the thread-safe DB pool + httpx clients;
# vLLM batches the concurrent requests. The §14 re-smoke proved the 37
# SEQUENTIAL section calls (~60-75 min at ~75-110s each) were the wall
# that timed out the report even after the findings phase was fixed.
_SECTION_WORKERS = int(os.getenv("DDIQ_SECTION_WORKERS", "4"))

_SECTION_TITLES = {
    "overview": "Project Overview",
    "land": "Land Security & Ownership",
    "permits": "Permits & Regulatory Conditions",
    "economics": "Economics & Operations",
}


def _analyze_one_question(doc_ids, q) -> AusgabeblattRow:
    """Evidence-aware RAG + extraction for ONE section question → one row.

    Never raises (returns an error row on failure) so a single bad
    question can't kill the section's thread pool.
    """
    # Backwards-compatible: tuples like ("label","question") still work.
    if isinstance(q, tuple):
        label, question, anchor = q[0], q[1], None
    else:
        label, question, anchor = q.get("label"), q.get("question"), q.get("anchor")
    try:
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
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        raw_value = result.get("value")
        val = clean_value(raw_value, "Information not found in documents")
        note_raw = result.get("note"); note = clean_value(note_raw, "") if note_raw else None
        if note == "": note = None
        # Evidence resolution. The LLM is asked to cite chunks in
        # ``evidence_chunks``, but in practice it often returns ``[]`` even
        # when its answer DID come from the reranked context — most of
        # ~13 evidence-less findings per report observed in the 2026-06-06
        # audit traced back to this silent skip. Fallback: when the LLM
        # cited nothing but the row's value is substantive (i.e. NOT the
        # "Information not found" fallback that ``clean_value`` injects
        # for null/empty model output), attach the top-2 reranked chunks
        # as source linkage. The guardrail's defensive-AI scrub still runs
        # downstream, so if the answer turns out to be a wordy "I cannot
        # find this" the fallback evidence rides along with a clearly-
        # missing finding — not ideal but better than orphaned. For truly
        # null/empty model output we leave evidence empty (no source
        # supported the "info missing" verdict).
        cited = result.get("evidence_chunks") or []
        evidence = evidence_from_chunks(reranked, cited)
        if not evidence and reranked and raw_value not in (None, "", "null"):
            # Take the top-2 reranked chunks — the same ones the LLM
            # had in its prompt context. Keep it tight (not all 5) so
            # downstream findings don't get padded with weakly-cited
            # chunks.
            fallback = reranked[: min(2, len(reranked))]
            evidence = evidence_from_chunks(
                reranked,
                list(range(1, len(fallback) + 1)),
            )
        # E10: evidence + anchor are real AusgabeblattRow fields now, so
        # they serialize through model_dump → JSONB + API response.
        return AusgabeblattRow(
            label=label, value=val,
            ampel=result.get("ampel") if result.get("ampel") in ("green", "yellow", "red") else None,
            note=note,
            evidence=evidence,
            anchor=anchor,
        )
    except Exception as e:
        logger.error(f"Section question /{label}: {e}")
        return AusgabeblattRow(label=label, value="Could not extract", ampel="red", note=f"Error: {str(e)[:80]}")


def analyze_section(doc_ids, section_id, max_workers=None, on_question_done=None):
    """Run the section's questions through evidence-aware RAG, concurrently.

    Each row carries the chunks the LLM cited so the frontend can show
    'click to see source'. Rows are returned in question order regardless of
    completion order (rebuilt by index), so the ordering is identical to the
    old ``executor.map`` version. ``max_workers=1`` forces sequential
    (tests / debugging).

    ``on_question_done`` (when given) is invoked in the CALLING thread once
    per completed question — it lets the caller advance the report progress
    bar per question instead of per section, so the bar doesn't sit flat for
    minutes during a single section.
    """
    questions = SECTION_QUESTIONS.get(section_id, [])
    title = _SECTION_TITLES.get(section_id, section_id.title())
    if not questions:
        return AusgabeblattSection(id=section_id, title=title, rows=[])
    workers = max_workers if max_workers is not None else _SECTION_WORKERS
    workers = max(1, min(workers, len(questions)))
    rows_by_idx: dict[int, AusgabeblattRow] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_analyze_one_question, doc_ids, q): i
                for i, q in enumerate(questions)}
        for fut in as_completed(futs):
            rows_by_idx[futs[fut]] = fut.result()
            if on_question_done is not None:
                on_question_done()
    rows = [rows_by_idx[i] for i in range(len(questions))]
    return AusgabeblattSection(id=section_id, title=title, rows=rows)


# extract_timeline / check_cross_doc_consistency / extract_rueckbau_bond
# / check_grundbuch_match — moved to ``ddiq.extractors.*`` (H-5 phase 2).


_LOC_NULLISH = {"", "null", "none", "unknown", "n/a", "na", "not specified"}

# Per-WEA owner values that mean "no real owner extracted" and should be
# back-filled from the canonical project company (A6 facts ledger).
_OWNER_PLACEHOLDERS = {"", "unknown", "see contracts", "not specified", "n/a", "na", "none", "null"}


def _relanguage_text(text: str, target_language: str = "de") -> str:
    """A8: re-render a mixed-language string entirely in the target
    language, preserving every fact, statute reference, number, date and
    proper noun. Best-effort — returns the original on empty input or any
    LLM failure, so a re-language miss never blanks a cell.
    """
    if not text or not text.strip():
        return text
    lang_name = "German" if target_language == "de" else "English"
    system = (
        f"You are a legal editor. Rewrite the user's text ENTIRELY in {lang_name}. "
        f"Preserve every fact, statute reference (e.g. '§6 BImSchG', '§35 Abs. 5 "
        f"BauGB'), number, date, monetary amount and proper noun EXACTLY as given. "
        f"Do not add, remove, soften or comment on any content. Output only the "
        f"rewritten text — no preamble."
    )
    try:
        out = llm_call(system, text, temperature=0.0, max_tokens=1024)
    except Exception:
        return text
    return out.strip() or text


def _needs_relanguage(text: str, target_language: str) -> bool:
    """A8: should this cell be re-rendered into ``target_language``?

    The §14 v3 run surfaced the gap: the original check only caught
    ``"mixed"`` (mid-sentence DE/EN switch), so a finding written WHOLLY
    in the wrong language (e.g. a fully-English finding in a German
    report) slipped through. We now re-language when the heuristic tags
    the text as ``"mixed"`` OR as a concrete language different from the
    target. ``"unknown"`` (too short / numeric) is left alone.
    """
    if not text or not text.strip():
        return False
    lang = detect_language(text)
    return lang == "mixed" or (lang in ("de", "en") and lang != target_language)


def _backfill_wea_owner(weas: list[WEAStatus], project_company: Optional[str]) -> int:
    """A6: fill each WEA's ``owner`` from the canonical project company
    when the per-row value is a placeholder. The smoke test showed
    per-row owners drifting / repeating; referencing the one reconciled
    company keeps every turbine consistent. Returns the number of rows
    back-filled (0 when there's no canonical company to use).

    Pure + in-place — unit-testable without the pipeline.
    """
    if not project_company or project_company.strip().lower() in _OWNER_PLACEHOLDERS:
        return 0
    n = 0
    for w in weas:
        if (w.owner or "").strip().lower() in _OWNER_PLACEHOLDERS:
            w.owner = project_company
            n += 1
    return n


def _wea_geocode_query(w: dict) -> str:
    """A7: build a clean, geocodable location string from a WEA's
    STRUCTURED location fields (gemeinde / landkreis / bundesland) — never
    the freeform paragraph the LLM sometimes returns in ``address``.

    Feeding that paragraph to Nominatim verbatim was the root of the
    Lamstedt→Bremen failure: the geocoder latched onto an unrelated token.
    When no structured fields are present we fall back to ``address`` ONLY
    if it's short enough to plausibly be a place name (≤80 chars, ≤1
    sentence) — a paragraph is dropped rather than geocoded.
    """
    parts = [
        str(w.get(k, "")).strip()
        for k in ("gemeinde", "landkreis", "bundesland")
    ]
    parts = [p for p in parts if p.lower() not in _LOC_NULLISH]
    if parts:
        return ", ".join(parts) + ", Deutschland"
    addr = str(w.get("address", "")).strip()
    if addr and len(addr) <= 80 and addr.count(".") <= 1:
        return addr
    return ""


def _wea_display_address(w: dict) -> str:
    """A7: short human-readable location for the WEA table — Gemeinde +
    Bundesland, not the paragraph. Falls back to a truncated ``address``
    when no structured fields exist."""
    gem = str(w.get("gemeinde", "")).strip()
    bl = str(w.get("bundesland", "")).strip()
    disp = ", ".join(
        p for p in (gem, bl) if p.lower() not in _LOC_NULLISH
    )
    if disp:
        return disp
    addr = str(w.get("address", "")).strip()
    return addr[:80] if addr else ""


_WEA_SPECS_QUERY = (
    "Nabenhöhe Rotordurchmesser Nennleistung Hersteller Typ Typenbezeichnung "
    "Anlagentyp Gesamthöhe Leistungskurve technische Daten Erläuterungsbericht Typenschild"
)


def extract_wea_specs(doc_ids) -> dict:
    """A10: dedicated, narrow pass for the turbine TECHNICAL spec table.

    The all-in-one WEA prompt routinely returns null specs because it is
    simultaneously juggling status + location + ownership; a focused
    query against the Erläuterungsbericht / datasheet recovers them. A
    wind park almost always deploys ONE turbine type, so we extract the
    canonical project-wide spec. Returns ``{}`` on failure (the caller
    keeps whatever the main pass found).
    """
    ctx = rag_context(doc_ids, _WEA_SPECS_QUERY, top_k=6)
    prompt = f"""Extract the wind-turbine TECHNICAL specification from the datasheet /
Erläuterungsbericht. Wind parks usually deploy ONE turbine type — return that
canonical spec. If genuinely multiple types are present, return the dominant one.

Context:
{ctx}

Return a JSON object (use null for any value not stated — never guess):
{{"manufacturer":"Vestas|Enercon|Nordex|Siemens Gamesa|GE|... or null",
  "model":"E-138 EP3|V162|N163|... (Typenbezeichnung) or null",
  "hub_height_m":<number, Nabenhöhe in metres, or null>,
  "rotor_diameter_m":<number, Rotordurchmesser in metres, or null>,
  "rated_power_kw":<number, Nennleistung in kW, or null>}}"""
    try:
        result = llm_json(EXTRACTION_SYSTEM, prompt)
        return result if isinstance(result, dict) else {}
    except Exception as e:
        logger.warning(f"WEA specs extraction: {e}")
        return {}


def _apply_canonical_specs(weas: list[WEAStatus], specs: dict) -> int:
    """A10: back-fill null per-WEA spec fields from the canonical
    project-wide spec. Only fills a field that is currently ``None`` — a
    per-turbine value the main pass DID extract is never overwritten.
    Pure + in-place; returns the number of individual field-fills.
    """
    if not specs:
        return 0

    def _num(v):
        try:
            return float(v) if v not in (None, "") else None
        except Exception:
            return None

    canon: dict = {
        "hub_height_m": _num(specs.get("hub_height_m")),
        "rotor_diameter_m": _num(specs.get("rotor_diameter_m")),
        "rated_power_kw": _plausible_rated_kw(specs.get("rated_power_kw")),
        "manufacturer": (specs.get("manufacturer") or None),
        "model": (specs.get("model") or None),
    }
    fills = 0
    for w in weas:
        for field, cval in canon.items():
            if cval is not None and getattr(w, field) is None:
                setattr(w, field, cval)
                fills += 1
    return fills


def _drop_geocode_outlier_weas(weas: list) -> list:
    """A4 — keep the turbine COUNT and CAPACITY honest by dropping WEA whose
    geocode is a far outlier from the main cluster.

    An onshore wind park spans a few km; a WEA geocoded >``_MAX_WEA_SPREAD_KM``
    (25 km) from the cluster is extraction noise — e.g. a turbine designation an
    OVG ruling cites from an *unrelated* case, or a badly mis-geocoded name.
    Left in, these inflate ``turbineCount`` and ``totalCapacityMw`` (the live
    Lamstedt run extracted 23 WEA / 46 MW; 15 were >25 km outliers, true park
    ≈8). The cadastral project-area step already drops them via
    :func:`filter_outlier_points`; this applies the SAME filter to the WEA list
    so the count, capacity, area and map all agree on one canonical set.

    Safe by construction: WEA without a geocode are kept (no distance to judge);
    with ≤2 geocoded WEA there's no cluster, so nothing is dropped; and if the
    filter would drop everything, the original list is returned unchanged.
    """
    geocoded_pts = [(w.lat, w.lng) for w in weas if w.lat != 0 or w.lng != 0]
    if len(geocoded_pts) <= 2:
        return weas
    kept = set(filter_outlier_points(geocoded_pts))
    if len(kept) >= len(geocoded_pts):
        return weas  # nothing was an outlier
    out = [
        w for w in weas
        if (w.lat == 0 and w.lng == 0) or (w.lat, w.lng) in kept
    ]
    if not out:
        return weas  # never drop everything
    if len(out) < len(weas):
        logger.info(
            "A4: dropped %d/%d geocode-outlier WEA (>%.0f km from cluster) so "
            "count/capacity match the real park",
            len(weas) - len(out), len(weas), _MAX_WEA_SPREAD_KM,
        )
    return out


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
  "gemeinde":"Gemeinde (municipality) name only, or empty — NOT a sentence",
  "landkreis":"Landkreis (district) name only, or empty",
  "bundesland":"Bundesland (state) name only, or empty",
  "ampel":"green|yellow|red",
  "hub_height_m":<number or null — Nabenhöhe in metres>,
  "rotor_diameter_m":<number or null — Rotordurchmesser in metres>,
  "rated_power_kw":<number or null — Nennleistung in kW>,
  "manufacturer":"Vestas|Enercon|Nordex|Siemens Gamesa|GE|null",
  "model":"E-138 EP3|V126|N163|... (Typenbezeichnung) or null",
  "status_code":"errichtet|genehmigt|geplant|abgenommen|null",
  "permit_ref":"BImSchG-Aktenzeichen or null",
  "warranty_end":"YYYY-MM-DD or null",
  "park":"the wind park this turbine belongs to (e.g. 'Windpark Lamstedt', 'Windpark Zodel'), or null"}}

Rules:
- Location: give Gemeinde / Landkreis / Bundesland as bare NAMES, never a
  sentence or paragraph. These drive geocoding — a paragraph geocodes to
  the wrong place. Leave a field empty if unknown.
- Status: errichtet=physically built, genehmigt=permit issued not yet built,
  geplant=planned only, abgenommen=accepted into operation. Be honest about
  ambiguity — set null rather than guessing.
- If "7 WEA in Hude" create WEA Hude 1 through WEA Hude 7 with shared attrs.
- Hub height matters: 10H rule means a 200m turbine in Bayern needs 2km clearance.
  Pull this from the Erläuterungsbericht / Genehmigungsbescheid wherever possible.
- Use "yellow" ampel for pre-check docs where status is unknown.
- ``park``: when the documents cover more than one wind park (e.g. a court
  judgment that names a neighbouring site, an easement bundle, or a
  Regionalplan referencing several sites), TAG EACH TURBINE WITH ITS PARK so
  the report can list them separately. Use the exact "Windpark X" name as it
  appears in the source. Set ``null`` only when the documents genuinely do
  not state which park a turbine belongs to — never guess."""

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
        # A7: geocode from the structured location fields, not a paragraph.
        geo_query = _wea_geocode_query(w)
        addr = _wea_display_address(w)
        # Gate the WEA geocode like geocode_project_location: only geocode
        # when a Bundesland is known — from the project centre, or
        # detectable in the query itself — so the result can be bbox-
        # checked. An ungatable bare name (e.g. a WEA whose only location
        # is "Feldmark") would otherwise land in the wrong region
        # (Castrop-Rauxel/NRW), so we leave the turbine un-pinned rather
        # than fabricate coordinates the document doesn't support.
        wea_bl = project_bl or detect_bundesland(geo_query)
        coords = geocode_address(geo_query, expected_bundesland=wea_bl) if (geo_query and wea_bl) else None
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
            rated_power_kw=_plausible_rated_kw(w.get("rated_power_kw")),
            manufacturer=w.get("manufacturer") or None,
            model=w.get("model") or None,
            status_code=sc,
            permit_ref=w.get("permit_ref") or None,
            warranty_end=w.get("warranty_end") or None,
            park=(str(w.get("park")).strip() or None) if w.get("park") else None,
            # Carry the geocode-gating bundesland onto the row so the
            # per-park address picker can use it. ``wea_bl`` was just
            # computed above as ``project_bl or detect_bundesland(geo_query)``
            # — same value, lowercased downstream by the picker.
            bundesland=(wea_bl or None),
        ))

    # Scatter duplicate coordinates so the map doesn't stack pins.
    # Skip un-pinned WEAs (lat==lng==0): a no-location turbine has no
    # coordinates, so it must NOT be scattered around (0,0) — that would
    # fabricate a cluster of pins on "Null Island" off the African coast.
    if statuses:
        cg = {}
        for i, s in enumerate(statuses):
            if s.lat == 0 and s.lng == 0:
                continue
            cg.setdefault(f"{s.lat:.6f},{s.lng:.6f}", []).append(i)
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

    # A10: recover specs the kitchen-sink WEA prompt missed. Only fire the
    # extra (focused) LLM pass when at least one row is still missing a
    # spec field — no wasted call when the main extraction was complete.
    if statuses and any(
        s.hub_height_m is None or s.rotor_diameter_m is None or s.rated_power_kw is None
        or s.manufacturer is None or s.model is None
        for s in statuses
    ):
        filled = _apply_canonical_specs(statuses, extract_wea_specs(doc_ids))
        if filled:
            logger.info("A10: back-filled %d WEA spec field(s) from the datasheet pass", filled)
    return statuses


# ── Path B: per-park breakdown ─────────────────────────────────────────────
# When a data room covers more than one wind park (a court judgment naming a
# neighbouring site, an easement bundle, a Regionalplan referencing several
# sites), the legacy "one project = one header" assumption produces a
# false-precise composite — e.g. a "Windpark Zodel" report carrying Lamstedt's
# turbines and applicant. The two helpers below group turbines by their
# ``park`` tag (set by the extractor) so the report can render each park
# separately, and detect a multi-park context for the header-honesty guard.

_PARK_NAME_RE = re.compile(
    r"\bWindpark[s]?\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-/]+(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß\-/]+)?)",
)


def _detect_parks_in_text(*texts: str) -> set[str]:
    """Distinct park names appearing as 'Windpark X' in the given texts.
    Falls back signal when the extractor didn't tag ``park`` on each turbine."""
    found: set[str] = set()
    for t in texts:
        if not t:
            continue
        for m in _PARK_NAME_RE.finditer(t):
            name = m.group(1).strip().rstrip(",.;:")
            if name:
                found.add(f"Windpark {name}")
    return found


def _build_park_facts(weas: list, project_name: str, expected_bundesland: Optional[str] = None) -> list:
    """Group turbines by their ``park`` tag and compute per-park aggregates.

    Returns a (possibly empty) list of :class:`ParkFacts`, sorted with the
    primary park (best name-match against ``project_name``) first. Turbines
    without a park tag are skipped — the goal is *honest grouping*, not
    forcing every row into a bucket. Single-park rooms typically return a
    one-element list (or [] when the extractor didn't tag park, which is
    fine — the legacy projectFacts still describes that park).
    """
    if not weas:
        return []
    grouped: dict[str, list] = {}
    untagged: list = []
    for w in weas:
        key = (w.park or "").strip()
        if key:
            grouped.setdefault(key, []).append(w)
        else:
            untagged.append(w)
    # All-untagged → legacy reconciler owns this (single-park clean case).
    # When SOME turbines are tagged and others aren't, we surface the untagged
    # ones as a synthetic "nicht zugeordnet" group so they (a) trigger the
    # multi-park guard and (b) appear in the contextual notes — instead of
    # silently joining the subject park's totals (the failure mode that left
    # 10 Lamstedt L-series turbines invisible on a Windpark-Zodel report).
    if not grouped:
        return []

    primary_lc = (project_name or "").strip().lower()
    _PLACEHOLDER_OWNERS = {"unknown", "see contracts", "not specified",
                           "not specified in documents", ""}
    parks: list = []
    for name, ts in grouped.items():
        kws = [w.rated_power_kw for w in ts if w.rated_power_kw]
        total_mw = round(sum(kws) / 1000.0, 3) if kws else None
        models = sorted({w.model for w in ts if w.model})
        status_counts: dict[str, int] = {}
        for w in ts:
            if w.status_code:
                status_counts[w.status_code] = status_counts.get(w.status_code, 0) + 1
        owners = [w.owner for w in ts
                  if w.owner and w.owner.strip().lower() not in _PLACEHOLDER_OWNERS]
        company = owners[0] if owners else None

        # Pick the park's address + bundesland with a bundesland-consistency
        # gate instead of "first WEA wins". Failure mode this guards against:
        # the WEAs of a Niedersachsen park get tagged by an LLM that sees
        # references to a different German wind region elsewhere in the
        # documents (e.g. a court ruling that cites Reußenköge / Schleswig-
        # Holstein as comparable case law), so the FIRST WEA's address ends
        # up being SH while the rest are NI. The previous ``next((w.address
        # ...))`` returned the SH outlier and the park header read
        # "Reußenköge, Schleswig-Holstein" for a Niedersachsen wind park —
        # directly contradicting the bundesland badge and the geocoded
        # projectCenter.
        wea_bundeslands = [
            (w.bundesland or "").strip().lower()
            for w in ts
            if (w.bundesland or "").strip()
        ]
        # The park's own bundesland: prefer the most-common across its
        # WEAs (defends against a single odd-one-out). Fall back to the
        # project's expected bundesland when no WEA carries one.
        park_bl: Optional[str] = None
        if wea_bundeslands:
            from collections import Counter
            park_bl = Counter(wea_bundeslands).most_common(1)[0][0]
        if not park_bl:
            park_bl = (expected_bundesland or "").strip().lower() or None

        # Address: when we have a park bundesland, take the first WEA
        # whose bundesland matches — the others are extraction noise.
        # Only fall back to the unfiltered first-WEA address when we
        # have no bundesland at all to gate on.
        def _matches_bl(w) -> bool:
            wb = (getattr(w, "bundesland", "") or "").strip().lower()
            return bool(park_bl) and wb == park_bl
        location: Optional[str] = None
        if park_bl:
            location = next(
                (w.address for w in ts if w.address and _matches_bl(w)),
                None,
            )
        if location is None:
            location = next((w.address for w in ts if w.address), None)

        nl = name.lower()
        is_primary = bool(primary_lc) and (nl in primary_lc or primary_lc in nl)
        parks.append(ParkFacts(
            name=name,
            projectCompany=company,
            bundesland=park_bl,
            location=location,
            turbineCount=len(ts),
            totalCapacityMw=total_mw,
            models=models,
            statusCounts=status_counts,
            turbineNames=[w.name for w in ts],
            isPrimary=is_primary,
        ))
    # Synthetic group for the untagged-but-extracted turbines, so they
    # trigger the multi-park guard and surface in the contextual notes (a
    # lawyer reading 'WEA L 1…L 16' under "(Park nicht zugeordnet)" will
    # immediately recognise the Lamstedt-series contamination on a
    # Windpark-Zodel report). Never primary by definition.
    if untagged:
        kws = [w.rated_power_kw for w in untagged if w.rated_power_kw]
        un_total_mw = round(sum(kws) / 1000.0, 3) if kws else None
        un_models = sorted({w.model for w in untagged if w.model})
        un_status: dict[str, int] = {}
        for w in untagged:
            if w.status_code:
                un_status[w.status_code] = un_status.get(w.status_code, 0) + 1
        un_owners = [
            w.owner for w in untagged
            if w.owner and w.owner.strip().lower() not in _PLACEHOLDER_OWNERS
        ]
        parks.append(ParkFacts(
            name="(Park nicht zugeordnet)",
            projectCompany=un_owners[0] if un_owners else None,
            location=None,
            turbineCount=len(untagged),
            totalCapacityMw=un_total_mw,
            models=un_models,
            statusCounts=un_status,
            turbineNames=[w.name for w in untagged],
            isPrimary=False,
        ))

    # Primary first, then by turbine count desc.
    parks.sort(key=lambda p: (not p.isPrimary, -p.turbineCount, p.name))
    # A single extracted park IS the primary (there's no ambiguity to flag).
    # For 2+ parks with NO name match, refuse to mark a "best-effort" primary —
    # auto-largest would carry the false-confidence we just fixed. The caller's
    # multi-park guard then degrades the top header to honest "unknown".
    if len(parks) == 1:
        parks[0].isPrimary = True
    return parks


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
            try:
                ap_list = alkis_query_parcels(wea.lat, wea.lng, bundesland, 150)
            except AlkisError as e:
                # WFS unreachable (e.g. HTTP 530, propagated by the wrapper) —
                # abandon the cadastral layer rather than failing the whole
                # report. Layers 2 (regex) + 3 (LLM) + estimated polygons below
                # still produce parcels; they're tagged non-ALKIS provenance.
                logger.warning(
                    "ALKIS WFS unavailable in build_parcels (%s) — skipping "
                    "cadastral layer, degrading to regex/LLM/estimated: %s",
                    bundesland, e,
                )
                break
            for ap in ap_list:
                if ap["parcelNumber"] in seen: continue
                seen.add(ap["parcelNumber"])
                # A3 — honest provenance. The parcel RECORD comes from
                # ALKIS, but the GEOMETRY may not: if ALKIS returned a real
                # polygon we tag it ``alkis_wfs`` with high confidence;
                # when it didn't, we draw an estimated box and must tag THAT
                # ``estimated`` — never let a synthetic polygon render as
                # cadastral truth. Likewise ``area`` is the real ALKIS area
                # or 0.0 (unknown) — never the old fabricated default.
                real_polygon = ap.get("polygon")
                real_area_m2 = ap.get("area_m2")
                area_ha = round(real_area_m2 / 10000, 2) if real_area_m2 else 0.0
                if real_polygon:
                    poly, poly_source, conf = real_polygon, "alkis_wfs", 0.95
                else:
                    poly = make_parcel_polygon(wea.lat, wea.lng, area_ha or 2.5)
                    poly_source, conf = "estimated", 0.3
                parcels.append(CadastralParcel(id=f"p{len(parcels)+1}", parcelNumber=ap["parcelNumber"],
                    gemarkung=ap.get("gemarkung") or "Unknown", flur=ap.get("flur",0), polygon=poly,
                    status={"green":"secured","yellow":"negotiation","red":"open"}.get(wea.ampel,"open"),
                    owner=wea.owner, area=area_ha, linkedWEA=wea.name,
                    polygonSource=poly_source, confidence=conf,
                    notes=f"Source: {ap.get('source','ALKIS WFS')}"))
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
        # A3 — parcel parsed from document text: the geometry is an
        # estimated box (or empty), and we have no cadastral area, so
        # ``area`` is 0.0 (unknown) rather than the old hash-of-the-
        # parcel-number fabrication ``round(2.0+(hash(pnum)%20)/10,1)``.
        parcels.append(CadastralParcel(id=f"p{len(parcels)+1}", parcelNumber=pnum,
            gemarkung=str(ref.get("gemarkung","")) or "Unknown", flur=int(ref.get("flur",0)) if ref.get("flur") else 0,
            polygon=poly, status=status, owner=owner, area=0.0,
            polygonSource="estimated", confidence=0.4 if linked else 0.2,
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
                    # A3 — LLM-suggested parcel: estimated geometry, no
                    # cadastral area (0.0 = unknown), lowest confidence.
                    parcels.append(CadastralParcel(id=f"p{len(parcels)+1}", parcelNumber=pnum,
                        gemarkung=str(ref.get("gemarkung","")) or "Unknown", flur=int(ref.get("flur",0)) if ref.get("flur") else 0,
                        polygon=poly, status="buffer", owner="", area=0.0,
                        polygonSource="estimated", confidence=0.15, notes="Source: LLM"))
        except Exception as e: logger.warning(f"LLM parcel: {e}")

    # A3 — count by real provenance (polygonSource), not by string-matching
    # the free-text notes. Only ``alkis_wfs`` polygons are real cadastral
    # geometry; everything else is an estimated box.
    alkis_n = sum(1 for p in parcels if p.polygonSource == "alkis_wfs")
    estimated_n = sum(1 for p in parcels if p.polygonSource == "estimated")
    logger.info(
        "Parcels: %d total (alkis_wfs:%d, estimated:%d)",
        len(parcels), alkis_n, estimated_n,
    )
    return parcels


# _findings_prompt_for_issue / _finding_from_llm_obj /
# _placeholder_finding_for_issue / generate_findings — moved to
# ``ddiq.extractors.findings`` (H-5 phase 2).


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/documents", response_model=DocumentListResponse)
def list_documents(user: CurrentUser = Depends(get_current_user)):
    """List the caller's own documents (Phase B-revert: private-by-default).

    Each user sees only what they uploaded. Explicit sharing (Step 2 of
    Path A) will widen this with a UNION over rows shared with them."""
    # Path A Step 2: list the caller's OWN documents PLUS documents
    # explicitly shared with them. (Write paths — upload, delete — stay
    # owner-only and aren't affected by shares.)
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """SELECT id, filename, size_bytes, upload_date, status, category
           FROM ddiq_documents
           WHERE user_id = %s
              OR EXISTS (SELECT 1 FROM ddiq_document_shares s
                         WHERE s.document_id = ddiq_documents.id
                           AND s.user_id = %s)
           ORDER BY upload_date DESC""",
        (str(user.id), str(user.id)),
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
    # user_id (created_by + visibility, post-revert) comes from the JWT
    # (AUTH_PLAN G3) — never from the body. org_id is stamped alongside
    # for membership/audit but doesn't gate reads anymore.
    org_id = _org_str(user)
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
        "INSERT INTO ddiq_documents (id,user_id,org_id,filename,size_bytes,status,category,full_text,chunk_count,session_id) "
        "VALUES (%s,%s,%s,%s,%s,'analyzed',%s,%s,%s,%s)",
        (did, str(user.id), org_id, file.filename, len(fb), category, full_text, len(chunks), session_id),
    )
    for c, e in zip(chunks, embs):
        cur.execute("INSERT INTO ddiq_doc_chunks (doc_id,chunk_idx,text,embedding) VALUES (%s,%s,%s,%s::vector)",
            (did, c["idx"], c["text"], "["+",".join(str(x) for x in e)+"]"))
    conn.commit(); cur.close(); conn.close()
    return UploadDocResponse(id=did, filename=file.filename, pages=pages, chunks=len(chunks), status="analyzed",
        message=f"{file.filename}: {pages} pages, {len(chunks)} chunks")


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    """Hard-delete an uploaded document and its chunks. Idempotent — returns
    404 if the id is unknown OR belongs to a different user.

    ``ddiq_doc_chunks.doc_id`` has ON DELETE CASCADE, so removing the
    document row drops its chunks automatically. Phase B-revert: scoped
    on the creator — cross-user delete is structurally impossible.
    """
    uid = str(user.id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ddiq_documents WHERE id = %s AND user_id = %s",
                (doc_id, uid),
            )
            if not cur.fetchone():
                raise HTTPException(404, "Document not found")
            cur.execute(
                "DELETE FROM ddiq_documents WHERE id = %s AND user_id = %s",
                (doc_id, uid),
            )
    return {"deleted": True, "document_id": doc_id}

# ─── Request dedup ────────────────────────────────────────────────────────
# A 30-60 min pipeline run is too expensive to repeat for the same input.
# We fingerprint (sorted doc_ids, preset, project_name) and look up
# ddiq_reports.request_fingerprint before queuing or running anything.
# - status='done'  → return the cached row, no work done.
# - status in ('queued','running') and recent → return that row's id so the
#   caller polls the in-flight job instead of starting a duplicate.

_INFLIGHT_TTL = "2 hours"  # reuse window for queued/running rows


def _estimate_report_minutes(doc_ids: list, preset: str) -> int:
    """Heuristic estimate for how long a DDiQ report will take, in minutes.

    Primary signal: median wall-clock of recent COMPLETED reports with a
    similar ``doc_count`` (±2). That's a far better estimate than any
    static formula because it absorbs the realities of the live GPU,
    network connectors, and the current preset's prompt budget — all of
    which drift over time.

    Fallback: simple ``2 + 2 * n_docs`` heuristic when no historical
    samples exist (fresh install, never-before-seen doc_count).

    Used by ``/report/generate/async`` to populate the toast estimate
    shown to the user on submit. Never raises — best-effort.
    """
    n = len(doc_ids or [])
    if n == 0:
        return 2
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXTRACT(EPOCH FROM (finished_at - started_at)) / 60.0
                    FROM ddiq_reports
                    WHERE status = 'done'
                      AND finished_at IS NOT NULL
                      AND started_at IS NOT NULL
                      AND COALESCE(array_length(document_ids, 1), 0)
                          BETWEEN %s AND %s
                    ORDER BY finished_at DESC
                    LIMIT 30
                    """,
                    (max(1, n - 2), n + 2),
                )
                samples = [float(r[0]) for r in cur.fetchall()
                           if r[0] is not None and float(r[0]) > 0]
        if samples:
            samples.sort()
            median = samples[len(samples) // 2]
            return max(1, int(round(median)))
    except Exception as e:  # noqa: BLE001 — best-effort estimate
        logger.warning(f"estimate: median lookup failed: {e}")
    # Heuristic fallback: ~2 min overhead + ~2 min per document.
    return max(2, 2 + 2 * n)


def _compute_fingerprint(doc_ids, preset, project_name, user_id) -> str:
    """Cache key for report-generation requests.

    Phase B-revert: scoped by ``user_id`` (private-by-default). Two users
    requesting the same documents+preset produce DIFFERENT fingerprints
    — each user's report is their own.
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

    Phase B-revert: the fingerprint already includes the user_id, but the
    WHERE clause filters explicitly — defense in depth: if a future
    change drops user_id from the fingerprint, the SQL still refuses to
    leak across users.
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

    Phase B note: ``user_id`` is accepted for caller-compat but no longer
    used as a scope guard — that was per-user defense-in-depth which made
    sense for single-user ownership but is wrong for firm-shared reports
    (a teammate must also be able to nudge progress on the SAME row).
    The ``report_id`` is a UUID — uniqueness alone makes a cross-row
    update vanishingly improbable, and the row's ``org_id`` (set at
    INSERT in ``_persist_report_jsonb``) is what gates visibility.
    """
    del user_id  # accepted but unused — see docstring
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
                          report: "DDiQReportData", user_id, org_id=None) -> None:
    """Best-effort UPSERT of just the report_data JSONB. Used as a checkpoint
    after each major pipeline phase — if a later phase crashes, the row
    still has the partial report from the last successful checkpoint
    instead of the empty '{}' placeholder.

    Cheap (one round-trip per phase) and idempotent: re-running the same
    pipeline overwrites the row in place. Auxiliary table writes
    (ddiq_contracts, ddiq_classified_parcels, ddiq_project_areas) are
    deliberately NOT done here — those are write-once-at-end to avoid
    duplicate rows from re-running.

    Phase B: ``user_id`` (created_by) AND ``org_id`` (firm-tenant
    visibility) are both set on initial INSERT; the ON CONFLICT branch
    leaves them untouched so a stray re-call cannot reassign ownership.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ddiq_reports (id, user_id, org_id, project_name, document_ids, preset, report_data)
                       VALUES (%s, %s, %s, %s, %s::uuid[], %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                           project_name = EXCLUDED.project_name,
                           document_ids = EXCLUDED.document_ids,
                           preset = EXCLUDED.preset,
                           report_data = EXCLUDED.report_data""",
                    (rid, str(user_id), str(org_id) if org_id else None, project_name, doc_ids, preset, json.dumps(report.model_dump())),
                )
    except Exception as e:
        # Checkpoint failure shouldn't kill the pipeline — the next checkpoint
        # (or the final UPSERT) will catch up.
        logger.warning(f"checkpoint persist for {rid} failed: {e}")


def _notify_report_complete(rid: str, user_id, *, success: bool, error: str = "") -> None:
    """Email the requesting user that their report finished (or didn't).

    Called from the worker on the success / failure branches of
    ``_run_report_generation_job``. The user can close the tab the moment
    they submit and still get notified — that's the whole point of the
    "we'll email you" UX on bulk reports. Best-effort: a Brevo hiccup, a
    missing email config, a DB read failure must NEVER crash the worker
    (the report itself is already done/failed and persisted).
    """
    try:
        # Look up the recipient + project name from the DB (synchronously,
        # the worker is sync). One round-trip; ~ms.
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT u.email, u.full_name, u.status AS user_status,
                              r.project_name, r.started_at, r.finished_at
                       FROM ddiq_reports r
                       JOIN users u ON u.id = r.user_id
                       WHERE r.id = %s""",
                    (rid,),
                )
                row = cur.fetchone()
        if row is None or (row.get("user_status") or "") != "active":
            return  # user gone / disabled — nothing to do
        elapsed_minutes = 0
        if row["started_at"] and row["finished_at"]:
            elapsed_minutes = max(
                1,
                int(round((row["finished_at"] - row["started_at"]).total_seconds() / 60.0)),
            )
        # Deferred import: the worker doesn't otherwise pull lai.api.email
        # at module load. Constructing EmailConfig() lazily lets a missing
        # LAI_EMAIL_* env (dev / CI) surface as a one-line warning instead
        # of a worker boot failure.
        try:
            from lai.api.email import (  # type: ignore[import-not-found]
                EmailConfig,
                send_report_failed_email,
                send_report_ready_email,
            )
            config = EmailConfig()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "report notify: email not configured — skipping (%s)", exc,
            )
            return
        import asyncio
        if success:
            asyncio.run(send_report_ready_email(
                config,
                recipient_email=row["email"],
                recipient_name=row["full_name"] or row["email"],
                report_id=rid,
                project_name=row["project_name"] or "Wind Energy Project",
                elapsed_minutes=elapsed_minutes,
            ))
        else:
            asyncio.run(send_report_failed_email(
                config,
                recipient_email=row["email"],
                recipient_name=row["full_name"] or row["email"],
                report_id=rid,
                project_name=row["project_name"] or "Wind Energy Project",
                error=error or "An unexpected error occurred during generation.",
            ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("report notify: send failed for %s: %s", rid, exc)


def _run_report_generation_job(rid: str, req: "GenerateReportRequest", user_id, org_id=None) -> None:
    """Runs the same pipeline as the sync /report/generate, but writes
    progress + final report into the existing ddiq_reports row instead
    of returning. Errors are recorded in ``status='failed'`` + ``error``.

    Phase B-revert: ``user_id`` is the visibility scope (private-by-
    default). ``org_id`` is accepted (worker contract back-compat) and
    still stamped on writes for membership/audit, but doesn't gate reads.

    Notification: on the terminal status transition (done OR failed) we
    fire a one-shot email to the user who initiated the report so they
    can close the tab the moment they submit and still know when it's
    ready. See :func:`_notify_report_complete`.
    """
    _t0 = time.time()
    try:
        _update_report_progress(rid, status="running", step="starting", percent=0.0)
        report, _T = _generate_report_core(
            rid, req, user_id, org_id,
            progress=lambda step, pct: _update_report_progress(rid, step=step, percent=pct),
        )
        _update_report_progress(rid, status="done", step="done", percent=1.0)
        _notify_report_complete(rid, user_id, success=True)
        audit.record_sync(
            action="report", outcome="success", user_id=user_id, org_id=org_id,
            session_id=rid, latency_ms=round((time.time() - _t0) * 1000),
            detail={"doc_count": len(req.document_ids)},
        )
    except HTTPException as e:
        detail = f"HTTP {e.status_code}: {e.detail}"
        _update_report_progress(rid, status="failed", error=detail)
        _notify_report_complete(rid, user_id, success=False, error=detail)
        audit.record_sync(
            action="report", outcome="failed", user_id=user_id, org_id=org_id,
            session_id=rid, latency_ms=round((time.time() - _t0) * 1000),
            detail={"error": detail},
        )
    except Exception as e:
        logger.exception(f"report {rid} failed")
        msg = str(e)[:500]
        _update_report_progress(rid, status="failed", error=msg)
        _notify_report_complete(rid, user_id, success=False, error=msg)
        audit.record_sync(
            action="report", outcome="failed", user_id=user_id, org_id=org_id,
            session_id=rid, latency_ms=round((time.time() - _t0) * 1000),
            detail={"error": msg},
        )


@router.post("/report/generate/async")
def generate_report_async(req: GenerateReportRequest, user: CurrentUser = Depends(get_current_user)):
    """Non-blocking variant of /report/generate. Returns
    {report_id, status} immediately and runs the pipeline in a
    background thread. Poll /report/{id}/status (or /report/{id}) for
    progress and the final result."""
    if not req.document_ids:
        raise HTTPException(400, "No documents selected")
    uid = str(user.id)
    org_id = _org_str(user)  # stamped on the row; doesn't gate reads post-revert
    _assert_can_view_documents(req.document_ids, user.id)

    fp = _compute_fingerprint(req.document_ids, req.preset, req.project_name, uid)
    existing = _find_existing_report(fp, uid)
    if existing:
        # Cache hit — same user, same docs, same preset, same project name.
        # No new work; no email will be sent (none was queued). ``estimated_minutes``
        # is 0 to suppress the "we'll email you" toast on the SPA.
        return {
            "report_id": str(existing["id"]),
            "status": existing["status"],
            "poll_url": f"/ddiq/report/{existing['id']}/status",
            "cached": True,
            "estimated_minutes": 0,
        }

    # Pre-create the row so the caller has a real report_id to poll. Phase
    # B-revert: ``user_id`` is the visibility key; ``org_id`` is still
    # stamped (membership/audit + the explicit-share flow in Step 2).
    rid = str(uuid.uuid4())
    pname = req.project_name or "Wind Energy Project"
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Recycle any failed row with the same fingerprint. The dedup
            # helper above intentionally ignores failed rows so a retry
            # actually retries instead of returning the stale failure —
            # but the row IS still in the table and holds the unique
            # request_fingerprint, so a fresh INSERT would 500 on the
            # constraint. Clear the carcass (and its FK-less sub-table
            # rows, mirroring DELETE /report/{id}) before inserting.
            # Bounded by user_id so we never touch another user's row.
            cur.execute(
                "SELECT id FROM ddiq_reports "
                "WHERE request_fingerprint = %s AND user_id = %s AND status = 'failed'",
                (fp, uid),
            )
            stale_ids = [r[0] for r in cur.fetchall()]
            for stale_id in stale_ids:
                cur.execute("DELETE FROM ddiq_classified_parcels WHERE report_id = %s", (stale_id,))
                cur.execute("DELETE FROM ddiq_contracts WHERE report_id = %s", (stale_id,))
                cur.execute("DELETE FROM ddiq_project_areas WHERE report_id = %s", (stale_id,))
                cur.execute("DELETE FROM ddiq_reports WHERE id = %s AND user_id = %s",
                            (stale_id, uid))
            cur.execute(
                """INSERT INTO ddiq_reports (id, user_id, org_id, project_name, document_ids, preset,
                                              report_data, status, started_at, progress_step, progress_percent,
                                              request_fingerprint)
                   VALUES (%s, %s, %s, %s, %s::uuid[], %s, '{}'::jsonb, 'queued', NULL, 'queued', 0.0, %s)""",
                (rid, uid, org_id, pname, req.document_ids, req.preset, fp),
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
        rid, req.model_dump(mode="json"), str(user.id), org_id,
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
                    (async_result.id, rid, uid),
                )
    except Exception:
        pass  # column missing or transient DB issue — non-blocking

    return {
        "report_id": rid,
        "status": "queued",
        "poll_url": f"/ddiq/report/{rid}/status",
        "cached": False,
        # Heuristic from the recent-runs median (or a fallback formula).
        # The SPA shows a "we'll email you when this finishes — ~N minutes"
        # toast on submit so the user can close the tab safely.
        "estimated_minutes": _estimate_report_minutes(req.document_ids, req.preset),
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
                   FROM ddiq_reports
                   WHERE id = %s
                     AND (user_id = %s
                          OR EXISTS (SELECT 1 FROM ddiq_report_shares s
                                     WHERE s.report_id = ddiq_reports.id
                                       AND s.user_id = %s))""",
                (report_id, str(user.id), str(user.id)),
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


def _generate_report_core(rid: str, req: "GenerateReportRequest", user_id, org_id=None, progress=None) -> "tuple[DDiQReportData, dict]":
    """Inner pipeline used by both the sync handler and the async worker.
    ``progress(step, percent)`` is invoked at each major step when given.

    Phase B-revert: ``user_id`` is the visibility scope (private-by-
    default). ``org_id`` is accepted for back-compat and still stamped
    on every write (ddiq_reports, ddiq_project_areas, ddiq_contracts,
    ddiq_classified_parcels) so explicit sharing in Step 2 of Path A
    has the membership context to work with.
    """
    _assert_can_view_documents(req.document_ids, user_id)

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
        f"""SELECT id, filename FROM ddiq_documents
            WHERE id::text IN ({','.join(['%s']*len(req.document_ids))})
              AND (user_id = %s
                   OR EXISTS (SELECT 1 FROM ddiq_document_shares s
                              WHERE s.document_id = ddiq_documents.id
                                AND s.user_id = %s))""",
        (*req.document_ids, str(user_id), str(user_id)),
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
    # we can't rely on dict.get's default arg here. Also reject a refusal
    # phrase ("Nicht im Kontext enthalten") leaking through as the name —
    # caught one in KS's first report.
    _meta_pname = meta.get("projectName")
    if _looks_like_refusal(_meta_pname) or _looks_like_address(_meta_pname):
        _meta_pname = None
    pname = req.project_name or _meta_pname or "Wind Energy Project"
    _meta_pfor = meta.get("preparedFor")
    if _looks_like_refusal(_meta_pfor):
        _meta_pfor = None
    pfor = req.prepared_for or _meta_pfor or "Client"
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
        projectCenter=None,  # set after geocoding; stays None if no location determinable
        analyzedDocuments=doc_names,
        documentMap=document_map,
    )
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    t = time.time()
    # Sections are the bulk (~80% of wall time). Tick the bar PER QUESTION as
    # each one completes (not once per section) so it advances smoothly
    # 0.07 → 0.55 instead of sitting flat at 7% for the whole first section
    # and looking hung — the "stuck at 7%" report. The step label tracks the
    # current section; the percent is interpolated across every question in
    # all four sections. ``_tick_question`` runs in this (the calling) thread,
    # so the shared counter needs no lock.
    _section_ids = ["overview", "land", "permits", "economics"]
    _SEC_START, _SEC_END = 0.07, 0.55
    _total_q = sum(len(SECTION_QUESTIONS.get(s, [])) for s in _section_ids) or 1
    _q_done = [0]
    _cur_section = [_section_ids[0]]

    def _tick_question():
        _q_done[0] += 1
        progress(f"sections:{_cur_section[0]}",
                 round(_SEC_START + (_SEC_END - _SEC_START) * _q_done[0] / _total_q, 3))

    sections = []
    for _sid in _section_ids:
        _cur_section[0] = _sid
        progress(f"sections:{_sid}",
                 round(_SEC_START + (_SEC_END - _SEC_START) * _q_done[0] / _total_q, 3))
        sections.append(analyze_section(req.document_ids, _sid, on_question_done=_tick_question))
    T["sections_s"] = round(time.time()-t, 2)
    progress("sections_done", 0.55)
    report.sections = sections

    # ── Reconcile the project NAME against the section evidence ─────────
    # ``pname`` was set early (above) from a lightweight metadata call over
    # only three chunks, which sometimes returns the applicant's address
    # line ("Sönke-Nissen-Koog 58") instead of the project's name. The
    # overview "Project Name" cell is a stronger, evidence-anchored source
    # produced by the full section analysis. When the caller gave no
    # explicit name, prefer the section cell, then the early metadata
    # value — skipping any address-shaped or placeholder candidate.
    if not req.project_name:
        _section_pname = clean_value(get_section_value(sections, "overview", "Project Name"), "")

        def _usable_name(n: Optional[str]) -> bool:
            return bool(n) and n not in ("Wind Energy Project", "Not specified in documents") \
                and not _looks_like_address(n) \
                and not _looks_like_refusal(n)

        if _usable_name(_section_pname):
            new_pname = _section_pname
        elif _usable_name(meta.get("projectName")):
            new_pname = meta.get("projectName")
        else:
            new_pname = pname if _usable_name(pname) else "Wind Energy Project"
        if new_pname != pname:
            logger.info("projectName reconciled: %r → %r", pname, new_pname)
            pname = new_pname
    report.projectName = pname
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    t = time.time()
    progress("geocoding", 0.55)
    pc = geocode_project_location(sections)
    ploc = get_section_value(sections, "overview", "Location")
    logger.info(f"Center: {pc}, Location: {ploc}")
    T["geocode_s"] = round(time.time()-t, 2)

    progress("wea_extraction", 0.58)
    t = time.time(); weas = extract_wea_statuses(req.document_ids, full_text, sections, pc); T["wea_s"] = round(time.time()-t, 2)
    # A4: drop geocode-outlier WEA before they reach the count/capacity
    # reconciler (and the map), so one canonical turbine set is used everywhere.
    weas = _drop_geocode_outlier_weas(weas)
    report.weaStatuses = weas
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    progress("infrastructure", 0.70)
    t = time.time(); infra = extract_infrastructure(req.document_ids, sections, pc); T["infra_s"] = round(time.time()-t, 2)
    report.infrastructure = infra
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    # ── 13-Step Cadastral Pipeline ────────────────────────────────────────
    progress("cadastral", 0.78)
    t = time.time()
    pipeline = CadastralPipeline(
        alkis_query_fn=alkis_query_parcels,
        rag_context_fn=rag_context,
        llm_json_fn=llm_json,
        detect_bundesland_fn=detect_bundesland,
    )
    # Map the pipeline's internal 0..1 completion into the report band so the
    # bar advances through the (multi-minute) 13-step run instead of freezing
    # at 78%. Leaves 0.84→0.85 for the fast post-pipeline reconciliation.
    def _cad_progress(frac):
        progress("cadastral", round(0.78 + (0.84 - 0.78) * frac, 3))
    pipeline_result = pipeline.run(
        doc_ids=req.document_ids,
        full_text=full_text,
        wea_statuses=weas,
        project_area_polygon=None,  # Will auto-generate from WEA hull
        location=ploc,
        project_center=pc,
        progress=_cad_progress,
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
    # projectCenter: average of the REAL WEA coordinates; else the
    # geocoded project centre; else None. The old code fell back to a
    # hard-coded (53, 9) placeholder, which (combined with un-pinned WEAs)
    # rendered a confident-but-fake pin — either at (53, 9) or, after the
    # geocode-gate fix above left WEAs un-pinned, at (0, 0) Null Island.
    # When the document gives no location anywhere, the honest output is
    # NO centre → the UI shows "Standort nicht bestimmbar", not a pin.
    lats0 = [w.lat for w in weas if w.lat != 0]; lngs0 = [w.lng for w in weas if w.lng != 0]
    if lats0 and lngs0:
        center0 = {"lat": sum(lats0)/len(lats0), "lng": sum(lngs0)/len(lngs0)}
    elif pc:
        center0 = {"lat": pc[0], "lng": pc[1]}
    else:
        center0 = None
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
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    # ── Cross-source reconciliation ──────────────────────────────────────
    # Compute canonical values for fields that multiple upstream sources
    # disagree about (the "four-conflicting-turbine-counts" failure mode
    # from the smoke test). Each downstream consumer uses the reconciled
    # value, so a single report can no longer show different numbers in
    # different sections. Precedence: cadastral > llm > regex > fallback.
    # See ``_reconcile.py`` for the rationale.

    # Header capacity + turbine count. Precedence, in order of trust:
    #   1. an explicit document-stated park size ("2 MW pro Einheit für 10
    #      Einheiten" → 10 / 20 MW) — a deliberate figure in the permit;
    #   2. honest "unknown" when the cell itself disclaims a determinable
    #      number (often because the documents cover two wind parks) — we must
    #      NOT then assert the raw per-WEA row count, which merges the
    #      neighbouring park's turbines and duplicates (the "23 turbines /
    #      46 MW" smoke-test bug);
    #   3. only as a last resort, the extracted per-WEA figures.
    cap_str = get_section_value(sections, "overview", "Total Capacity")
    woa_str = get_section_value(sections, "overview", "Number of WEA")
    explicit_count, explicit_total_mw = _parse_explicit_park_size(cap_str, woa_str)

    wea_capacities_kw = [w.rated_power_kw for w in weas if w.rated_power_kw]
    sum_total_mw: Optional[float] = (
        round(sum(wea_capacities_kw) / 1000.0, 3) if wea_capacities_kw else None
    )
    llm_wea_count: Optional[int] = len(weas) if weas else None

    # ── total capacity ──
    if explicit_total_mw is not None:
        total_mw = explicit_total_mw
        logger.info("total_capacity_mw: %.1f from explicit document figure", total_mw)
    elif _signals_undeterminable(cap_str):
        total_mw = None
        logger.warning("total_capacity_mw: cell disclaims a figure → header 'unknown' "
                       "(per-WEA sum was %s)", sum_total_mw)
    else:
        total_mw = sum_total_mw
    total_mw: Optional[float] = total_mw

    # ── turbine count ──
    doc_count = parse_wea_count(woa_str) or None
    if explicit_count is not None:
        turbine_count = explicit_count
        logger.info("turbine_count: %d from explicit document figure", turbine_count)
    elif _signals_undeterminable(woa_str) and not doc_count:
        turbine_count = 0  # 0 == unknown (ProjectFacts contract)
        logger.warning("turbine_count: cell disclaims a figure → header 'unknown' "
                       "(extracted %s rows)", llm_wea_count)
    elif doc_count and llm_wea_count and abs(llm_wea_count - doc_count) > max(2, round(0.25 * doc_count)):
        turbine_count = doc_count  # explicit doc total beats a divergent row count
        logger.warning("turbine_count: extracted rows (%d) diverge from document count "
                       "(%d); trusting the document", llm_wea_count, doc_count)
    else:
        turbine_count = llm_wea_count or doc_count or 0
    report.turbineCount = int(turbine_count or 0)

    # Capture the count divergence so the report can SURFACE it as a note,
    # not just trust-doc-silently. Lawyer sees header=11 but parks[primary]=7
    # otherwise reads as a contradiction; with a note attached, it reads as
    # "doc says 11, we have structural data on 7 — go look for the other 4".
    count_divergence: Optional[tuple[int, int]] = (
        (doc_count, llm_wea_count)
        if (doc_count and llm_wea_count and
            abs(llm_wea_count - doc_count) > max(2, round(0.25 * doc_count)))
        else None
    )

    # bundesland: keyword scan on the location string vs. bbox derivation
    # from the geocoded project_center. The bbox derivation is grounded
    # in real coordinates from Nominatim, so it gets the "cadastral"
    # precedence; the keyword scan is the regex layer.
    # bl_from_keyword: derive ONLY from an explicit structured
    # "Bundesland: X" field, not a keyword scan of the free Location
    # narrative. Scanning the narrative false-positives on defensive
    # "keine Angaben zum Bundesland, Landkreis, Gemeinde …" text that
    # names a state as an example — which is how a no-location contract
    # wrongly got bundesland=niedersachsen. Grounded coordinates
    # (bl_from_coords) stay the primary, higher-precedence source.
    _loc_bl = _parse_location_fields(ploc).get("bundesland") if ploc else None
    bl_from_keyword: Optional[str] = detect_bundesland(_loc_bl) if _loc_bl else None
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

    # ── A6: facts ledger ──────────────────────────────────────────────
    # Bundle the canonical identity + reconciled numbers into ONE
    # ProjectFacts object that the UI and downstream consumers quote, so
    # the report can't show divergent names / counts / capacities. Note
    # ``total_mw`` was reconciled above but, pre-A6, was never stored on
    # the report — only passed to findings — so the UI had no canonical
    # capacity to render.
    project_company = clean_value(
        get_section_value(sections, "overview", "Project Company"), ""
    ) or None
    # Operational evidence as a canonical fact: WEA the extraction proves are
    # physically built / commissioned (status_code errichtet|abgenommen).
    commissioned_wea = sum(
        1 for w in report.weaStatuses
        if getattr(w, "status_code", None) in ("errichtet", "abgenommen")
    )

    # ── Path B: per-park breakdown + multi-park header honesty ─────────
    # Group turbines by their ``park`` tag (set by the extractor). When the
    # documents cover more than one wind park, also catch it via section/
    # cell text as a fallback signal — and either reassign the top-level
    # header to the SUBJECT park (primary, by projectName match) only, or
    # degrade it to honest "unknown" rather than asserting a false-precise
    # composite that merges two parks' specs. Single-park rooms are
    # unaffected: park_facts_list is empty or one entry, multi_park is
    # False, the existing reconciler stands.
    park_facts_list = _build_park_facts(
        report.weaStatuses, pname,
        expected_bundesland=report.bundesland,
    )
    text_parks = _detect_parks_in_text(
        get_section_value(sections, "overview", "Project Name"),
        get_section_value(sections, "overview", "Number of WEA"),
        get_section_value(sections, "overview", "Location"),
        woa_str, cap_str,
    )
    distinct_parks = {p.name for p in park_facts_list} | text_parks
    multi_park = len(distinct_parks) >= 2
    report.parks = park_facts_list
    report.multiParkDetected = multi_park
    multi_park_notes: dict[str, str] = {}
    if multi_park:
        logger.warning(
            "Path B: multi-park context — distinct parks: %s; per-WEA tagged: %s",
            sorted(distinct_parks), [p.name for p in park_facts_list],
        )
        primary = next((p for p in park_facts_list if p.isPrimary), None)
        if primary and primary.turbineCount > 0:
            # Header speaks for the primary subject park ONLY (not a mix). If
            # the primary park's company isn't determinable, the header must
            # NOT silently keep the section-extracted value (which can come
            # from the OTHER park) — degrade to None alongside capacity.
            report.turbineCount = primary.turbineCount
            total_mw = primary.totalCapacityMw
            project_company = primary.projectCompany
            logger.info(
                "Path B: header retargeted to primary park '%s' (count=%d, mw=%s, company=%s)",
                primary.name, primary.turbineCount, primary.totalCapacityMw, project_company,
            )
        else:
            # Multi-park context but no clean primary — honest unknown beats
            # a confident wrong number. Clear projectCompany AND bundesland too
            # (the section extractor's bundesland can come from either park,
            # so it's as contaminated as the count/capacity/company).
            report.turbineCount = 0
            total_mw = None
            project_company = None
            report.bundesland = None
            primary = None
            logger.warning(
                "Path B: multi-park with no isolatable primary — header set to unknown",
            )
        # Contextual notes: an empty value reads "we don't know" — bad
        # impression. For every field we degraded, attach a German,
        # lawyer-facing note that explains WHY it's unknown for the subject
        # AND surfaces what the documents say for the OTHER parks in the
        # room, clearly attributed and flagged as "nicht Gegenstand dieses
        # Berichts". Transparency, not silence.
        subject = primary.name if primary else pname
        other_parks = [p for p in park_facts_list if p is not primary]
        # Parks named in section text but NOT extracted into ParkFacts — when
        # only the subject was extracted, we still want the lawyer to know
        # which OTHER parks are mentioned in the room (e.g. a court judgment
        # referencing a neighbouring site), so the unknown carries context
        # rather than reading as ignorance.
        text_only_parks = sorted(
            n for n in distinct_parks
            if n not in {p.name for p in park_facts_list}
        )

        def _peers_with(attr: str, fmt) -> list[str]:
            out = []
            for op in other_parks:
                v = getattr(op, attr, None)
                if v:
                    out.append(fmt(op, v))
            return out

        def _peer_tail(peers: list[str]) -> str:
            """Tail of the note that surfaces what the documents say about
            the OTHER parks in the room — specific figures when we have them,
            else at least the names so the unknown isn't context-less."""
            if peers:
                return (
                    " Andere im Datenraum genannte Parks: "
                    + "; ".join(peers)
                    + " — nicht Gegenstand dieses Berichts."
                )
            if text_only_parks:
                return (
                    " Die Dokumente erwähnen weitere Parks im Datenraum ("
                    + ", ".join(text_only_parks)
                    + ") — deren Daten sind nicht Gegenstand dieses Berichts."
                )
            return ""

        if total_mw is None:
            peers = _peers_with(
                "totalCapacityMw", lambda op, v: f"{op.name}: ~{v} MW"
            )
            multi_park_notes["totalCapacityMw"] = (
                f"Für „{subject}“ in den vorliegenden Dokumenten nicht angegeben."
                + _peer_tail(peers)
            )
        if not project_company:
            peers = _peers_with(
                "projectCompany", lambda op, v: f"{op.name}: {v}"
            )
            multi_park_notes["projectCompany"] = (
                f"Projektgesellschaft für „{subject}“ in den vorliegenden "
                f"Dokumenten nicht angegeben."
                + _peer_tail(peers)
            )
        if report.turbineCount == 0:
            peers = _peers_with(
                "turbineCount", lambda op, v: f"{op.name}: {v} WEA"
            )
            multi_park_notes["turbineCount"] = (
                f"Turbinenzahl für „{subject}“ aus den Dokumenten nicht "
                f"eindeutig ableitbar."
                + _peer_tail(peers)
            )

    # Single-park or multi-park, an extraction-vs-document count divergence
    # is a real signal lawyers care about. Don't let it ride silently as a
    # parks[].turbineCount-vs-header.turbineCount contradiction with no
    # explanation — surface the gap so a probing reviewer ("you say 11, the
    # park breakdown says 7?") gets the honest answer ("11 mentioned, 7 with
    # extracted coordinates"). Skip when the multi-park branch already wrote
    # a turbineCount note (its degrade-to-unknown narrative trumps this).
    if count_divergence and "turbineCount" not in multi_park_notes:
        dc, lc = count_divergence
        multi_park_notes["turbineCount"] = (
            f"Im Dokument genannt: {dc} WEA. Strukturiert extrahiert (mit "
            f"Koordinaten / IDs): {lc} WEA. Die übrigen {dc - lc} sind nur "
            f"im Fließtext erwähnt und konnten nicht in den WEA-Status "
            f"übernommen werden."
        )

    facts = ProjectFacts(
        projectName=pname,
        preparedFor=pfor,
        projectCompany=project_company,
        projectCenter=center0,
        bundesland=report.bundesland,
        turbineCount=report.turbineCount,
        totalCapacityMw=total_mw,
        commissionedWeaCount=commissioned_wea,
        notes=multi_park_notes or None,
    )
    report.projectFacts = facts.model_dump()

    # Render the "Project Status" overview cell FROM the canonical fact (§5.4
    # facts-ledger / A4 "force the fact into the overview row"). The cell's
    # question is permit-framed, so answered in isolation it can say "nicht
    # enthalten" even when the documents (e.g. a maintenance contract with
    # serial numbers + commissioning dates) prove operation. When the ledger
    # knows WEA are commissioned but the cell is empty/defensive, synthesize an
    # honest operational status while keeping the permit caveat — no guess.
    if commissioned_wea > 0:
        _status_cell = clean_value(
            get_section_value(sections, "overview", "Project Status"), ""
        )
        if not _status_cell.strip() or detect_defensive_ai(_status_cell):
            _synth = (
                f"Operativ belegt: {commissioned_wea} WEA als errichtet / in "
                f"Betrieb genommen nachgewiesen (aus den vorgelegten Unterlagen, "
                f"z. B. Seriennummern / Inbetriebnahmedaten). Der formale "
                f"BImSchG-Genehmigungsstatus (§ 6 / § 15) ist aus den vorgelegten "
                f"Unterlagen nicht verifizierbar."
            )
            if _set_section_value(sections, "overview", "Project Status", _synth):
                logger.info(
                    "A6: Project Status reconciled from %d commissioned WEA",
                    commissioned_wea,
                )

    # Reference the canonical company for any WEA whose own owner came
    # back as a placeholder — one source of truth, not a per-row guess.
    backfilled = _backfill_wea_owner(report.weaStatuses, project_company)
    if backfilled:
        logger.info("A6: back-filled owner on %d/%d WEA row(s) from project company",
                    backfilled, len(report.weaStatuses))

    # Checkpoint the reconciled facts NOW, before the expensive findings
    # pass. Without this, turbineCount / bundesland / projectFacts (just
    # computed above) would only be persisted by the post-findings
    # checkpoint — so a findings-phase failure (e.g. the §14 re-smoke,
    # which timed out at the Celery hard limit during findings) loses the
    # canonical facts from the saved row even though the pipeline derived
    # them. Persisting here means a usable report survives a findings
    # crash with its reconciled numbers intact.
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    progress("findings", 0.85)
    t = time.time()
    findings = generate_findings(req.document_ids, sections, total_capacity_mw=total_mw)
    T["findings_s"] = round(time.time()-t, 2)
    report.findings = findings  # may be augmented with deadline/rueckbau/grundbuch findings later
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    # E1b: the four "extras" passes — timeline (P0 #2), cross-document
    # consistency (P0 #3), Rückbaubürgschaft (P1 #9), Grundbuch (P1 #6) —
    # are mutually independent: each consumes only the already-computed
    # sections / weas / parcels. The §14 runs showed them running
    # sequentially as part of the tail that hit the time limit. Run them
    # concurrently (each owns its try/except + safe-default return, so a
    # failure can't crash the pool), then persist once.
    progress("extras", 0.88)
    t = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut_timeline = ex.submit(extract_timeline, req.document_ids, full_text)
        fut_crossdoc = ex.submit(check_cross_doc_consistency, sections, weas, parcels, total_mw)
        fut_rueckbau = ex.submit(extract_rueckbau_bond, req.document_ids)
        fut_grundbuch = ex.submit(check_grundbuch_match, req.document_ids, parcels)
        timeline = fut_timeline.result()
        cross_doc_findings = fut_crossdoc.result()
        rueckbau = fut_rueckbau.result()
        grundbuch_checks = fut_grundbuch.result()
    T["extras_s"] = round(time.time()-t, 2)
    report.timeline = timeline
    report.crossDocFindings = cross_doc_findings
    report.rueckbauBond = rueckbau
    report.grundbuchChecks = grundbuch_checks

    # Path B widened multiPark detection: re-trip ``multiParkDetected`` if
    # the cross-doc analyzer named a wind park the WEA-tag scan didn't
    # already know about. Failure mode this guards against: extraction
    # tags all 7 turbines as Lamstedt, so ``distinct_parks`` had only
    # one entry → multi_park=False. But the cross-doc analyzer (which
    # runs over MORE text and catches discrepancies) caught 8 red
    # findings flagging the Lamstedt/Zodel contamination — and that
    # signal was being thrown away because ``multiParkDetected`` had
    # already been frozen earlier. Now any "Windpark X" mention in a
    # cross-doc finding that ISN'T the already-known set trips the flag,
    # so the UI's multi-park warning + the contextual-notes degrade
    # actually fire when they should.
    if not report.multiParkDetected:
        xdoc_text = " ".join(
            (f.text or "")
            for f in cross_doc_findings
            if getattr(f, "text", None)
        )
        if xdoc_text:
            xdoc_parks = _detect_parks_in_text(xdoc_text)
            already_known = {p.name for p in report.parks} | {pname or ""}
            new_parks = {
                p for p in xdoc_parks
                if p not in already_known and p.lower() not in (pname or "").lower()
            }
            if new_parks:
                report.multiParkDetected = True
                # Cross-doc-only parks (e.g. a neighbouring Windpark whose
                # documents are scan-based with broken OCR) get NO WEA tags,
                # so _build_park_facts produced no ParkFacts for them. The
                # FINDINGS pipeline picks them up just fine (they appear in
                # crossDocFindings tagged with the park name), but parks[]
                # was silent — so the UI/lawyer saw "multiParkDetected: true"
                # with only the subject in parks[], and no place to attach
                # the other park's findings. Append name-only stubs so each
                # cross-doc-named park has a row in parks[], even if the
                # structural fields stay default (we don't fabricate counts).
                for park_name in sorted(new_parks):
                    report.parks.append(ParkFacts(
                        name=park_name,
                        isPrimary=False,
                    ))
                # projectFacts.notes was already written at the earlier
                # persistence point (single-park assumption). Patch it now
                # to acknowledge the multi-park context the late signal
                # revealed, so the UI footer note reads honestly. Keep any
                # prior notes (e.g. the turbineCount divergence note).
                xdoc_park_list = ", ".join(sorted(new_parks))
                if isinstance(report.projectFacts, dict):
                    pf_notes = dict(report.projectFacts.get("notes") or {})
                    pf_notes.setdefault("multiParkDetected", (
                        f"Weitere Parks aus Querverweis-Befunden: "
                        f"{xdoc_park_list}. Strukturierte WEA-Daten dieser "
                        f"Parks konnten nicht aus den vorgelegten Dokumenten "
                        f"extrahiert werden (z. B. scannbasierte Unterlagen "
                        f"mit unzureichendem OCR). Befunde zu diesen Parks "
                        f"finden sich im Abschnitt „Dokumentübergreifende "
                        f"Befunde“ (crossDocFindings)."
                    ))
                    report.projectFacts["notes"] = pf_notes
                logger.info(
                    "multiParkDetected re-tripped by cross-doc signal: %s "
                    "(added %d stub ParkFacts)",
                    sorted(new_parks), len(new_parks),
                )
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

    # Promote material timeline events (urgent / expired) into Findings so
    # the lawyer's findings list reflects deadline pressure, not just
    # section issues. ONLY genuine deadline/obligation kinds qualify — an
    # informational historical milestone ("Sonstiges: date of the original
    # 2005 permit", "Bauabschnitt: …") is not a risk and must not surface as
    # a RED finding; doing so over-inflates the red count and erodes trust in
    # the rubric. Those entries still appear in the timeline view.
    deadline_findings: list[Finding] = []
    for te in timeline:
        if te.urgency in ("expired", "urgent") and _DEADLINE_KIND_RE.search(te.kind or ""):
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
    # Drop content-less placeholder findings — a row whose entire text is a
    # bare "Nicht in den vorgelegten Dokumenten enthalten" with no stated
    # subject says nothing a lawyer can act on and reads as a stub. A real
    # "missing document" finding names WHAT is missing (those are kept).
    _kept = [f for f in all_findings if not _is_contentless_finding(f)]
    if len(_kept) < len(all_findings):
        logger.info("findings: dropped %d content-less placeholder finding(s)",
                    len(all_findings) - len(_kept))
    all_findings = _kept
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

    # ── A8: single-language enforcement (re-prompt) ───────────────────
    # The guardrail above only FLAGS off-language cells. Here we actually
    # fix them: any row value/note or finding text that is "mixed"
    # (mid-sentence DE/EN switch) OR wholly in the wrong language is
    # re-rendered into the target by a focused LLM call (see
    # _needs_relanguage — the §14 v3 run showed wholly-English findings
    # were slipping past the old "mixed"-only check). One call per flagged
    # cell — they're rare, so the cost is bounded — and best-effort, so a
    # re-language failure leaves the original text rather than blanking
    # it. Runs after the guardrail so it operates on the cleaned text.
    relang_n = 0
    for sec in report.sections:
        for row in sec.rows:
            if _needs_relanguage(row.value, section_lang_hint):
                new = _relanguage_text(row.value, section_lang_hint)
                if new != row.value:
                    row.value = new
                    relang_n += 1
            if row.note and _needs_relanguage(row.note, section_lang_hint):
                new = _relanguage_text(row.note, section_lang_hint)
                if new != row.note:
                    row.note = new
                    relang_n += 1
    for f in list(report.findings) + list(report.crossDocFindings):
        if _needs_relanguage(f.text, section_lang_hint):
            new = _relanguage_text(f.text, section_lang_hint)
            if new != f.text:
                f.text = new
                relang_n += 1
    if relang_n:
        logger.info("A8: re-rendered %d off-language cell(s) into '%s'", relang_n, section_lang_hint)

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
    _persist_report_jsonb(rid, pname, req.document_ids, req.preset, report, user_id, org_id)

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
            # Phase B: scope by report_id (unique). The aux-row creator was
            # this same pipeline call; an extra user_id check would block
            # legitimate re-runs by a different firm member.
            cur.execute(
                "DELETE FROM ddiq_classified_parcels WHERE report_id = %s",
                (rid,),
            )
            cur.execute(
                "DELETE FROM ddiq_contracts WHERE report_id = %s",
                (rid,),
            )
            cur.execute(
                "DELETE FROM ddiq_project_areas WHERE report_id = %s",
                (rid,),
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
    uid = str(user.id)
    org_id = _org_str(user)  # stamped on row; doesn't gate reads post-revert
    _assert_can_view_documents(req.document_ids, user.id)

    fp = _compute_fingerprint(req.document_ids, req.preset, req.project_name, uid)
    existing = _find_existing_report(fp, uid)
    if existing and existing["status"] == "done":
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT report_data FROM ddiq_reports WHERE id = %s AND user_id = %s",
                    (existing["id"], uid),
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
            # Recycle any failed row with the same fingerprint, same as the
            # async path does. Without this the ON CONFLICT below silently
            # claims nothing → caller hits the 409 race-loser branch even
            # though there's no race. Real symptom: user clicks Generate
            # after a failed run on the same docs+preset and gets a 409
            # (sync path) or 500 (async path) instead of a fresh retry.
            cur.execute(
                "SELECT id FROM ddiq_reports "
                "WHERE request_fingerprint = %s AND user_id = %s AND status = 'failed'",
                (fp, str(user.id)),
            )
            stale_ids = [r[0] for r in cur.fetchall()]
            for stale_id in stale_ids:
                cur.execute("DELETE FROM ddiq_classified_parcels WHERE report_id = %s", (stale_id,))
                cur.execute("DELETE FROM ddiq_contracts WHERE report_id = %s", (stale_id,))
                cur.execute("DELETE FROM ddiq_project_areas WHERE report_id = %s", (stale_id,))
                cur.execute("DELETE FROM ddiq_reports WHERE id = %s AND user_id = %s",
                            (stale_id, str(user.id)))
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
        other = _find_existing_report(fp, uid)
        if other and other["status"] == "done":
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT report_data FROM ddiq_reports WHERE id = %s AND user_id = %s",
                        (other["id"], uid),
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
        report, T = _generate_report_core(rid, req, user.id, org_id)
    except HTTPException as e:
        try:
            _update_report_progress(
                rid, status="failed", error=f"HTTP {e.status_code}: {e.detail}",
            )
        except Exception:
            pass  # row may not exist yet if the crash was pre-first-persist
        raise
    except Exception as e:
        logger.exception(f"sync report {rid} failed")
        try:
            _update_report_progress(rid, status="failed", error=str(e)[:500])
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
                          -- Total risk findings = per-doc + cross-doc.
                          -- The dashboard "Risk Findings" tile sums this; without
                          -- the crossDocFindings half it under-counts and disagrees
                          -- with the RiskOverview detail view (which concats both
                          -- arrays). Symptom observed 2026-06-06: dashboard 113
                          -- vs. detail 146 — gap = 33 cross-doc findings.
                          COALESCE(jsonb_array_length(report_data->'findings'), 0)
                          + COALESCE(jsonb_array_length(report_data->'crossDocFindings'), 0)
                          AS finding_count
                   FROM ddiq_reports
                   WHERE user_id = %s
                      OR EXISTS (SELECT 1 FROM ddiq_report_shares s
                                 WHERE s.report_id = ddiq_reports.id
                                   AND s.user_id = %s)
                   ORDER BY created_at DESC NULLS LAST
                   LIMIT %s""",
                (str(user.id), str(user.id), limit),
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
        """SELECT * FROM ddiq_reports
           WHERE id = %s
             AND (user_id = %s
                  OR EXISTS (SELECT 1 FROM ddiq_report_shares s
                             WHERE s.report_id = ddiq_reports.id
                               AND s.user_id = %s))""",
        (report_id, str(user.id), str(user.id)),
    )
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: raise HTTPException(404, "Not found")
    return {"report_id": str(row["id"]), "created_at": row["created_at"].isoformat(),
            "project_name": row["project_name"], "report": row["report_data"]}


@router.post("/report/{report_id}/export", status_code=204)
def record_report_export(
    report_id: str,
    fmt: str = "docx",
    user: CurrentUser = Depends(get_current_user),
) -> None:
    """Record that the caller exported a report. The DOCX/PDF/etc. is built
    client-side (no server-side render), so the FE pings this purely for the
    append-only audit trail. Verifies the report is visible to the caller
    (owner or share) → 404 otherwise; the audit write itself is best-effort.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        """SELECT 1 FROM ddiq_reports
           WHERE id = %s
             AND (user_id = %s
                  OR EXISTS (SELECT 1 FROM ddiq_report_shares s
                             WHERE s.report_id = ddiq_reports.id
                               AND s.user_id = %s))""",
        (report_id, str(user.id), str(user.id)),
    )
    visible = cur.fetchone(); cur.close(); conn.close()
    if not visible:
        raise HTTPException(404, "Not found")
    audit.record_sync(
        action="export",
        user_id=str(user.id),
        org_id=user.org_id,
        session_id=report_id,
        detail={"format": fmt},
    )


class RenameReportRequest(BaseModel):
    """Body for PATCH /ddiq/report/{id} — rename a report's project_name."""
    project_name: str


@router.patch("/report/{report_id}")
def rename_report(report_id: str, body: RenameReportRequest,
                  user: CurrentUser = Depends(get_current_user)):
    """User-driven rename of a DDiQ report's project_name.

    The auto-extracted name can be wrong on thin inputs (teaser PDFs,
    NDAs) — this lets the user fix it without re-running the pipeline.
    Also updates the projectName field inside report_data so the JSON
    payload stays consistent with the column.

    Auth: only the owner can rename. Cross-user → 404 (never leak existence).
    """
    name = body.project_name.strip()
    if not name:
        raise HTTPException(400, "project_name must not be empty")
    if len(name) > 200:
        raise HTTPException(400, "project_name too long (max 200 chars)")
    uid = str(user.id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ddiq_reports WHERE id = %s AND user_id = %s",
                (report_id, uid),
            )
            if not cur.fetchone():
                raise HTTPException(404, "Report not found")
            # Keep the JSONB report_data.projectName in lockstep with the
            # column. jsonb_set handles missing keys idempotently.
            cur.execute(
                """UPDATE ddiq_reports
                   SET project_name = %s,
                       report_data = jsonb_set(report_data, '{projectName}', to_jsonb(%s::text), true)
                   WHERE id = %s AND user_id = %s""",
                (name, name, report_id, uid),
            )
    return {"report_id": report_id, "project_name": name}


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
    celery_task_id: Optional[str] = None
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Phase B-revert: visibility is per-user. The report must
            # belong to the caller; 404 (not 403) on cross-user so we
            # never leak existence. Grab celery_task_id while we're
            # there — if the report is still being generated we need to
            # revoke the worker task too, otherwise the deleted row's
            # background LLM calls keep hammering vllm for ~hour and
            # starve every other user's chat (real incident 2026-05-24:
            # an orphaned task held vllm at 8 running / 54 waiting for
            # 33+ minutes after the row was deleted).
            cur.execute(
                "SELECT celery_task_id FROM ddiq_reports WHERE id = %s AND user_id = %s",
                (report_id, uid),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Report not found")
            celery_task_id = row[0]
            # Sub-table cleanup: scope by report_id (unique). The row was
            # already confirmed to belong to the caller above.
            cur.execute(
                "DELETE FROM ddiq_classified_parcels WHERE report_id = %s",
                (report_id,),
            )
            cur.execute(
                "DELETE FROM ddiq_contracts WHERE report_id = %s",
                (report_id,),
            )
            cur.execute(
                "DELETE FROM ddiq_project_areas WHERE report_id = %s",
                (report_id,),
            )
            cur.execute(
                "DELETE FROM ddiq_reports WHERE id = %s AND user_id = %s",
                (report_id, uid),
            )
    # Best-effort Celery revoke AFTER the DB commit so a Celery outage
    # never blocks the user's delete. ``terminate=True`` kills the worker
    # process running this task; Celery auto-respawns the worker so the
    # ddiq queue keeps draining other tasks.
    if celery_task_id:
        try:
            from worker import app as _celery_app  # type: ignore[import-not-found]
            _celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
            logger.info("revoked celery task %s for deleted report %s",
                        celery_task_id, report_id)
        except Exception as e:  # noqa: BLE001 — best effort; DB row already gone
            logger.warning("celery revoke failed for task %s (report %s): %s",
                           celery_task_id, report_id, e)
    return {"deleted": True, "report_id": report_id}


@router.get("/report/{report_id}/geojson")
def get_report_geojson(report_id: str, user: CurrentUser = Depends(get_current_user)):
    """Return GeoJSON FeatureCollection for GIS import (QGIS, ArcGIS, MapBox, etc.)."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """SELECT report_data FROM ddiq_reports
           WHERE id = %s
             AND (user_id = %s
                  OR EXISTS (SELECT 1 FROM ddiq_report_shares s
                             WHERE s.report_id = ddiq_reports.id
                               AND s.user_id = %s))""",
        (report_id, str(user.id), str(user.id)),
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
        """SELECT report_data FROM ddiq_reports
           WHERE id = %s
             AND (user_id = %s
                  OR EXISTS (SELECT 1 FROM ddiq_report_shares s
                             WHERE s.report_id = ddiq_reports.id
                               AND s.user_id = %s))""",
        (report_id, str(user.id), str(user.id)),
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


# ═══════════════════════════════════════════════════════════════════════════════
# SHARING (Path A Step 2 — explicit per-resource view-only sharing)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Symmetric to ``session_shares`` in serve_rag. Two resource types: DDiQ
# reports and DDiQ documents (the source PDFs). A share grants READ
# access to a colleague in the same firm; write paths (delete, re-share)
# stay owner-only. The pg_trgm member typeahead for the share dialog
# lives in ``share_router.py`` on serve_rag — the SPA calls it across
# ports the same way it calls /admin/users/search.

class _ShareUserRow(BaseModel):
    user_id: UUID
    email: str
    full_name: str
    granted_at: datetime


class _AddShareBody(BaseModel):
    user_id: UUID


def _resource_owner(table: str, resource_id: str) -> Optional[str]:
    """Return the ``user_id`` (str) of the resource's creator, or None
    if no such row. Used by the share-management gates."""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(f"SELECT user_id FROM {table} WHERE id = %s", (resource_id,))
        r = cur.fetchone()
    finally:
        cur.close(); conn.close()
    return str(r[0]) if r else None


def _can_manage_resource_shares(table: str, resource_id: str, user: CurrentUser) -> bool:
    """Owner OR super-admin gate for the share-management endpoints."""
    if user.is_super_admin:
        return True
    owner = _resource_owner(table, resource_id)
    return owner is not None and owner == str(user.id)


def _list_resource_shares(share_table: str, resource_col: str, resource_id: str) -> list[dict]:
    """Enriched share list — joins to ``users`` so the FE has names/emails
    without a second round-trip per row."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            f"""SELECT u.id AS user_id, u.email, u.full_name, s.created_at AS granted_at
                FROM {share_table} s
                JOIN users u ON u.id = s.user_id
                WHERE s.{resource_col} = %s
                ORDER BY s.created_at DESC, s.id DESC""",
            (resource_id,),
        )
        return list(cur.fetchall())
    finally:
        cur.close(); conn.close()


def _add_resource_share(
    share_table: str, resource_col: str,
    resource_id: str, target_user_id: str, granted_by: str,
) -> None:
    """Idempotent insert. Caller has already validated authorisation +
    same-org constraint."""
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            f"""INSERT INTO {share_table} ({resource_col}, user_id, granted_by)
                VALUES (%s, %s, %s)
                ON CONFLICT ({resource_col}, user_id) DO UPDATE SET
                    granted_by = EXCLUDED.granted_by""",
            (resource_id, target_user_id, granted_by),
        )
        conn.commit()
    finally:
        cur.close(); conn.close()


def _revoke_resource_share(
    share_table: str, resource_col: str, resource_id: str, target_user_id: str,
) -> bool:
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            f"DELETE FROM {share_table} WHERE {resource_col} = %s AND user_id = %s",
            (resource_id, target_user_id),
        )
        deleted = cur.rowcount > 0
        conn.commit()
    finally:
        cur.close(); conn.close()
    return deleted


def _assert_same_org(target_user_id: UUID, caller: CurrentUser) -> dict:
    """Look up target user and confirm same org. Returns the user row
    (id, email, full_name) for response enrichment. Raises 404/403 on
    miss / cross-firm."""
    conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute(
            "SELECT id, email, full_name, org_id FROM users WHERE id = %s",
            (str(target_user_id),),
        )
        row = cur.fetchone()
    finally:
        cur.close(); conn.close()
    if row is None:
        raise HTTPException(404, "user not found")
    if caller.org_id is None or row["org_id"] != caller.org_id:
        raise HTTPException(403, "user is not in your organisation")
    return row


# ─── /ddiq/reports/{id}/shares ─────────────────────────────────────────────

@router.get("/reports/{report_id}/shares", response_model=list[_ShareUserRow])
def list_report_shares(report_id: str, user: CurrentUser = Depends(get_current_user)):
    if not _can_manage_resource_shares("ddiq_reports", report_id, user):
        raise HTTPException(404, "report not found")
    return _list_resource_shares("ddiq_report_shares", "report_id", report_id)


@router.post("/reports/{report_id}/shares", response_model=_ShareUserRow, status_code=201)
def add_report_share(
    report_id: str, body: _AddShareBody,
    user: CurrentUser = Depends(get_current_user),
):
    if not _can_manage_resource_shares("ddiq_reports", report_id, user):
        raise HTTPException(404, "report not found")
    if str(body.user_id) == str(user.id):
        # Idempotent self-share; the owner already has access.
        conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("SELECT id, email, full_name FROM users WHERE id = %s", (str(user.id),))
            me = cur.fetchone()
        finally:
            cur.close(); conn.close()
        return _ShareUserRow(
            user_id=me["id"], email=me["email"], full_name=me["full_name"],
            granted_at=datetime.now(timezone.utc),
        )
    target = _assert_same_org(body.user_id, user)
    grantor = _resource_owner("ddiq_reports", report_id) if user.is_super_admin else str(user.id)
    assert grantor is not None  # noqa: S101 — authz already confirmed
    _add_resource_share("ddiq_report_shares", "report_id", report_id, str(body.user_id), grantor)
    logger.info("ddiq.report.share.add report=%s target=%s by=%s",
                report_id, body.user_id, user.id)
    # Read back the row so the response carries the real created_at.
    rows = _list_resource_shares("ddiq_report_shares", "report_id", report_id)
    for r in rows:
        if str(r["user_id"]) == str(body.user_id):
            return r
    raise HTTPException(500, "share persisted but not readable")


@router.delete("/reports/{report_id}/shares/{user_id}", status_code=204)
def revoke_report_share(
    report_id: str, user_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    if not _can_manage_resource_shares("ddiq_reports", report_id, user):
        raise HTTPException(404, "report not found")
    if not _revoke_resource_share("ddiq_report_shares", "report_id", report_id, str(user_id)):
        raise HTTPException(404, "share not found")
    logger.info("ddiq.report.share.revoke report=%s target=%s by=%s",
                report_id, user_id, user.id)


# ─── /ddiq/documents/{id}/shares ───────────────────────────────────────────

@router.get("/documents/{document_id}/shares", response_model=list[_ShareUserRow])
def list_document_shares(document_id: str, user: CurrentUser = Depends(get_current_user)):
    if not _can_manage_resource_shares("ddiq_documents", document_id, user):
        raise HTTPException(404, "document not found")
    return _list_resource_shares("ddiq_document_shares", "document_id", document_id)


@router.post("/documents/{document_id}/shares", response_model=_ShareUserRow, status_code=201)
def add_document_share(
    document_id: str, body: _AddShareBody,
    user: CurrentUser = Depends(get_current_user),
):
    if not _can_manage_resource_shares("ddiq_documents", document_id, user):
        raise HTTPException(404, "document not found")
    if str(body.user_id) == str(user.id):
        conn = get_conn(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute("SELECT id, email, full_name FROM users WHERE id = %s", (str(user.id),))
            me = cur.fetchone()
        finally:
            cur.close(); conn.close()
        return _ShareUserRow(
            user_id=me["id"], email=me["email"], full_name=me["full_name"],
            granted_at=datetime.now(timezone.utc),
        )
    target = _assert_same_org(body.user_id, user)
    grantor = _resource_owner("ddiq_documents", document_id) if user.is_super_admin else str(user.id)
    assert grantor is not None  # noqa: S101
    _add_resource_share("ddiq_document_shares", "document_id", document_id, str(body.user_id), grantor)
    logger.info("ddiq.document.share.add document=%s target=%s by=%s",
                document_id, body.user_id, user.id)
    rows = _list_resource_shares("ddiq_document_shares", "document_id", document_id)
    for r in rows:
        if str(r["user_id"]) == str(body.user_id):
            return r
    raise HTTPException(500, "share persisted but not readable")


@router.delete("/documents/{document_id}/shares/{user_id}", status_code=204)
def revoke_document_share(
    document_id: str, user_id: UUID,
    user: CurrentUser = Depends(get_current_user),
):
    if not _can_manage_resource_shares("ddiq_documents", document_id, user):
        raise HTTPException(404, "document not found")
    if not _revoke_resource_share("ddiq_document_shares", "document_id", document_id, str(user_id)):
        raise HTTPException(404, "share not found")
    logger.info("ddiq.document.share.revoke document=%s target=%s by=%s",
                document_id, user_id, user.id)


@router.on_event("startup")
async def startup():
    init_pool()
    init_db()
    reap_orphans()


@router.on_event("shutdown")
async def shutdown():
    close_pool()