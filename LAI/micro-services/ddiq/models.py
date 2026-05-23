"""Pydantic models for the DDiQ report pipeline.

Moved out of the legacy ``ddiq_report`` god-module in H-5. These
types describe the data flowing through the report pipeline:

* **Document & API response shapes** — ``DocumentOut``,
  ``DocumentListResponse``, ``UploadDocResponse``,
  ``ProjectAreaRequest``, ``ProjectAreaResponse``.
* **Ausgabeblatt rows** — the rendered grid the UI shows
  (``AusgabeblattRow``, ``AusgabeblattSection``).
* **Findings + evidence** — every materiality call carries an
  ``Evidence`` list (P0 #1) and an optional ``Quantification`` (P0
  #4) so a lawyer can verify the LLM's claim by jumping to the
  source page. See :class:`Evidence` / :class:`Finding`.
* **Domain entities** — ``WEAStatus`` (per-turbine permit /
  technical state), ``InfraPoint`` (substations, access roads,
  etc.), ``CadastralParcel`` (one row per ALKIS parcel after the
  cadastral pipeline).
* **Cross-cutting checks** — ``TimelineEntry`` (P0 #2),
  ``GrundbuchCheck`` (P1 #6), ``RueckbauBond`` (P1 #9).
* **Top-level report** — ``DDiQReportData`` carries every
  populated field; ``GenerateReportRequest`` / ``GenerateReportResponse``
  bookend the public API.

Defaults: most list-valued fields default to ``[]`` and most
optional scalars default to ``None``. This is load-bearing — the
orchestrator constructs an empty ``DDiQReportData`` at the top of
the pipeline and incrementally populates fields as each phase
completes, so a mid-pipeline crash leaves a usable row instead of
an empty placeholder.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── Document + upload responses ──────────────────────────────────────


class DocumentOut(BaseModel):
    id: str
    name: str
    size: float
    uploadDate: str
    type: str
    status: str
    category: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentOut]
    total: int


class UploadDocResponse(BaseModel):
    id: str
    filename: str
    pages: int
    chunks: int
    status: str
    message: str


# ── Evidence (P0 #1) ─────────────────────────────────────────────────
# Defined here, before AusgabeblattRow, because that row now carries an
# ``evidence`` field (E10). Every Finding / TimelineEntry / Grundbuch /
# Rückbau check also carries Evidence so a lawyer can verify the LLM's
# claim by jumping to the right page of the right document. Without
# this, output is unverifiable.


class Evidence(BaseModel):
    doc_id: Optional[str] = None
    doc_filename: Optional[str] = None
    page: Optional[int] = None        # currently no per-page chunking — left None
    excerpt: str = ""                 # short snippet (≤300 chars) from the chunk
    clause: Optional[str] = None      # e.g. "§4 Abs. 1 BImSchG", "Pachtvertrag §7"


# ── Rendered Ausgabeblatt grid ───────────────────────────────────────


class AusgabeblattRow(BaseModel):
    label: str
    value: str
    ampel: Optional[str] = None
    note: Optional[str] = None
    # E10: the chunks the LLM cited for this row + the statutory anchor
    # from SECTION_QUESTIONS. Previously stashed on ``__dict__`` as
    # ``_evidence`` / ``_anchor`` shadow attrs, which ``model_dump`` does
    # not serialise — so the per-row evidence was silently lost on the
    # JSONB checkpoint and the API response. Declared as real fields now
    # so it survives end-to-end (the UI's "click to see source" per row
    # depends on it). ``exclude=False`` is the default; these ride along
    # in model_dump.
    evidence: list[Evidence] = []
    anchor: Optional[str] = None


class AusgabeblattSection(BaseModel):
    id: str
    title: str
    rows: list[AusgabeblattRow]


# ── Per-turbine status ───────────────────────────────────────────────


class WEAStatus(BaseModel):
    name: str
    ampel: str
    owner: str
    parcel: str
    contract: str
    lat: float
    lng: float
    address: str
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
    # Path B: which wind park this turbine belongs to (e.g. "Windpark Zodel"
    # vs "Windpark Lamstedt"). Set by the extractor; None when the documents
    # don't make the assignment clear. Lets us group + report per park so a
    # data room covering several neighbouring sites doesn't get its facts
    # merged into one false-precise total.
    park: Optional[str] = None


class ParkFacts(BaseModel):
    """Per-windpark breakdown — populated when the documents cover more than
    one wind park (a court judgment that names a neighbouring site, an
    easement bundle, a multi-site Regionalplan). The legacy single-project
    ``projectFacts`` keeps reporting the primary subject; ``parks`` carries
    the full breakdown so the UI can show each park separately."""
    name: str
    projectCompany: Optional[str] = None
    bundesland: Optional[str] = None
    location: Optional[str] = None
    turbineCount: int = 0
    totalCapacityMw: Optional[float] = None
    models: list[str] = []
    statusCounts: dict[str, int] = {}      # {"errichtet": n, "genehmigt": n, …}
    turbineNames: list[str] = []
    # True for the park that matches the report's projectName (the subject).
    isPrimary: bool = False


class InfraPoint(BaseModel):
    name: str
    type: str
    lat: float
    lng: float


class CadastralParcel(BaseModel):
    id: str
    parcelNumber: str
    gemarkung: str
    flur: int
    polygon: list[list[float]]
    status: str
    owner: str
    area: float
    contractRef: Optional[str] = None
    linkedWEA: Optional[str] = None
    notes: Optional[str] = None
    polygonSource: str = "estimated"  # "alkis_wfs", "document", "estimated"
    confidence: float = 0.0
    normalizedId: str = ""


# ── Quantification (P0 #4) ───────────────────────────────────────────
# ``Evidence`` is defined earlier (above the Ausgabeblatt grid) because
# AusgabeblattRow now references it (E10).


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


# ── Timeline (P0 #2) ─────────────────────────────────────────────────


class TimelineEntry(BaseModel):
    """Date-bound milestone or deadline pulled from the documents.
    Surfaces 'permit valid until 2027-06-30, renewal 6 months prior' style
    findings that pure-RAG Q&A misses."""
    kind: str
    # permit_expiry | lease_term_end | renewal_deadline | warranty_end
    # | bond_validity | construction_milestone | objection_window | other
    date: str  # ISO YYYY-MM-DD when known, free-text fallback otherwise
    description: str
    legal_basis: Optional[str] = None       # e.g. "§70 VwGO Widerspruchsfrist"
    evidence: list[Evidence] = []
    days_from_now: Optional[int] = None
    urgency: Optional[str] = None           # expired | urgent | soon | future


# ── Grundbuch consistency (P1 #6) ────────────────────────────────────


class GrundbuchCheck(BaseModel):
    """Per-parcel: does Pachtvertrag-lessor match the registered Eigentümer?

    What encumbrances (Belastungen) are on the title? Without this, a parcel
    can show 'secured' even if the contract is signed by someone with no
    legal title.
    """
    parcel_id: str                          # normalized: gemarkung:flur:parcel_number
    registered_owner: Optional[str] = None
    lessor_name: Optional[str] = None
    owner_match: Optional[bool] = None      # None when undeterminable from documents
    match_confidence: float = 0.0
    encumbrances: list[str] = []
    # "Wegerecht zugunsten Gemeinde", "§24 BauGB Vorkaufsrecht", "Hypothek 250k €"
    evidence: list[Evidence] = []
    note: Optional[str] = None


# ── Rückbaubürgschaft (P1 #9) ────────────────────────────────────────


class RueckbauBond(BaseModel):
    """§35 Abs. 5 BauGB requires a decommissioning bond. Recurring DD red flag.

    Pulled out of the BImSchG-Bescheid Auflagen or a separate
    Bürgschaftsurkunde.
    """
    amount_eur: Optional[float] = None
    provider: Optional[str] = None          # bank, insurer, parent guarantor
    beneficiary: Optional[str] = None       # usually the Standortgemeinde
    valid_until: Optional[str] = None       # ISO date
    instrument_type: Optional[str] = None   # "Bürgschaft" | "Hinterlegung" | "Konzernbürgschaft"
    sufficient: Optional[bool] = None       # vs. expected Rückbaukosten (LLM's read)
    evidence: list[Evidence] = []
    note: Optional[str] = None


# ── Facts ledger (A6) ────────────────────────────────────────────────


class ProjectFacts(BaseModel):
    """Canonical project-level facts, derived ONCE after cross-source
    reconciliation and referenced by every downstream consumer instead
    of being re-derived per row/section.

    The smoke test showed the same project rendering four different
    turbine counts and a paragraph repeated across six WEA rows because
    each consumer extracted its own copy. This object is the single
    source of truth: the reconciler (``_reconcile.py``) settles the
    contested numbers, the identity fields come from the overview
    section, and the report + findings + cross-doc check all quote
    these values. ``None`` / 0 means no source produced a value
    (treated as "unknown", never back-filled with a guess).
    """
    projectName: str
    preparedFor: str
    projectCompany: Optional[str] = None     # Projektgesellschaft / Pächterin
    # None when the document gives no determinable location — the UI then
    # shows "Standort nicht bestimmbar" instead of a fabricated map pin.
    projectCenter: Optional[dict] = None
    bundesland: Optional[str] = None          # lowercase, e.g. "niedersachsen"
    turbineCount: int = 0
    totalCapacityMw: Optional[float] = None   # reconciled; was never stored before A6
    # WEA the extraction evidences as physically built / commissioned
    # (status_code errichtet|abgenommen). Lets the permit-framed "Project
    # Status" cell reflect proven operational status instead of saying
    # "not contained" when only a maintenance contract was provided.
    # (MaStR — §5.3, Phase 2B — will later confirm this externally.)
    commissionedWeaCount: int = 0


# ── Top-level report data + API bookends ─────────────────────────────


class DDiQReportData(BaseModel):
    projectName: str
    preparedBy: str
    preparedFor: str
    date: str
    # None when no location is determinable from the documents — the UI
    # renders no map pin rather than a fabricated/placeholder one.
    projectCenter: Optional[dict] = None
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
    # ── Facts ledger (A6) ───────────────────────────────────────────────
    # Canonical ProjectFacts (model_dump) — the single source of truth for
    # project-level identity + reconciled numbers, surfaced so the UI and
    # downstream consumers quote ONE set of values. ``totalCapacityMw`` in
    # particular was reconciled but never stored before A6.
    projectFacts: Optional[dict] = None
    # ── Per-park breakdown (Path B) ─────────────────────────────────────
    # Populated when the documents name more than one wind park. The legacy
    # ``projectFacts`` keeps reporting the primary subject (the park the
    # report is about); ``parks`` carries the full breakdown so the UI can
    # render each park separately and a lawyer never sees Lamstedt turbines
    # attributed to a Zodel header. Empty/single-entry on a clean room.
    parks: list[ParkFacts] = []
    multiParkDetected: bool = False
    # ── Jurisdiction warnings (H-2) ────────────────────────────────────
    # Populated by the post-guardrail jurisdiction scan in
    # ``_generate_report_core``. Each entry flags a Bundesland-specific
    # rule (e.g. ``"Bayerns 10H-Regel"``) cited in this report when the
    # matter's actual Bundesland is a different state. Empty list when
    # the matter has no detected Bundesland (the validator returns []
    # for ``expected_bundesland=None``) OR no cross-state rule was
    # mentioned. Same shape as serve_rag's ``JurisdictionWarningOut`` so
    # the UI can render both with the same component.
    jurisdictionWarnings: list[dict] = []


class GenerateReportRequest(BaseModel):
    document_ids: list[str]
    preset: str = "full"
    project_name: Optional[str] = None
    prepared_for: Optional[str] = None


class GenerateReportResponse(BaseModel):
    report_id: str
    report: DDiQReportData
    timings: dict


# ── Project-area endpoint shapes ─────────────────────────────────────


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


__all__ = [
    "AusgabeblattRow",
    "AusgabeblattSection",
    "CadastralParcel",
    "DDiQReportData",
    "DocumentListResponse",
    "DocumentOut",
    "Evidence",
    "Finding",
    "GenerateReportRequest",
    "GenerateReportResponse",
    "GrundbuchCheck",
    "InfraPoint",
    "ProjectAreaRequest",
    "ProjectAreaResponse",
    "ProjectFacts",
    "Quantification",
    "RueckbauBond",
    "TimelineEntry",
    "UploadDocResponse",
    "WEAStatus",
]
