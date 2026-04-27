"""Pydantic schema for Contract Analyzer V2.

The schema is the contract with the LLM (via guided JSON decoding) and
with the API consumer. See docs/analysis/CONTRACT_ANALYZER_V2.md §5.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


ContractType = Literal[
    "Pachtvertrag",
    "Nutzungsvertrag",
    "Wartungsvertrag",
    "Direktvermarktungsvertrag",
    "Einspeisevertrag",
    "PPA",
    "Dienstleistungsvertrag",
    "Kaufvertrag",
    "Sonstiges",
]


Severity = Literal[1, 2, 3, 4, 5]
Disposition = Literal["rectify", "ignore", "negotiate"]
ReconKind = Literal[
    "sum_mismatch",
    "vat_mismatch",
    "escalation_mismatch",
    "rounding",
]
ReconSeverity = Literal["info", "low", "medium", "high"]


class Parcel(BaseModel):
    gemeinde: Optional[str] = None
    gemarkung: Optional[str] = None
    flur: Optional[str] = None
    flurstueck: Optional[str] = Field(
        default=None, description="Cadastral parcel number, e.g. '47/3'"
    )
    groesse_m2: Optional[float] = None
    eigentuemer: Optional[str] = None
    raw_mention: str = Field(description="Original text span the parcel was extracted from")
    page: Optional[int] = None


class FinancialTable(BaseModel):
    title: str
    rows: list[dict] = Field(default_factory=list, description="Normalized rows from Docling")
    stated_total: Optional[float] = None
    computed_total: Optional[float] = None
    discrepancy: Optional[float] = Field(
        default=None, description="stated - computed; null if either side is missing"
    )
    currency: str = "EUR"


class ReconciliationFinding(BaseModel):
    table_title: str
    kind: ReconKind
    stated: float
    computed: float
    delta: float
    severity: ReconSeverity
    note: str


class Issue(BaseModel):
    severity: Severity = Field(description="1 = trivial, 5 = blocking / deal-breaking")
    title: str
    description: str
    affected_clauses: list[str] = Field(default_factory=list, description="Clause IDs")
    rectify_or_ignore: Disposition
    rationale: str = Field(description="Why this disposition; required so judgment is auditable")
    suggested_redline: Optional[str] = None
    legal_basis: list[str] = Field(default_factory=list, description="§/Art references where applicable")
    low_confidence: bool = Field(default=False, description="True when extraction quality may have caused this finding (e.g. missing-clause from a poorly-OCR'd PDF)")


class Clause(BaseModel):
    id: str
    title: str
    text: str
    type: str
    summary: str
    issues: list[Issue] = Field(default_factory=list)


class CrossClauseFinding(BaseModel):
    title: str
    involved_clauses: list[str]
    description: str
    severity: Severity
    rectify_or_ignore: Disposition
    rationale: str


class ContractMetadata(BaseModel):
    parties: list[str] = Field(default_factory=list)
    effective_date: Optional[str] = None
    signing_date: Optional[str] = None
    term: Optional[str] = None
    jurisdiction: Optional[str] = None


class ExtractionQuality(BaseModel):
    """How much the analyzer trusts the input it received.

    For signed/scanned PDFs, OCR can drop large portions of the body silently.
    When that happens, missing-clause findings are over-confident — we
    surface this so reviewers can recognize the noise.
    """
    confidence: Literal["high", "medium", "low"]
    chars_per_page: float
    total_chars: int
    n_pages: int
    reason: str


class ContractAnalysis(BaseModel):
    metadata: ContractMetadata
    contract_type: ContractType
    parcels: list[Parcel] = Field(default_factory=list)
    financial_tables: list[FinancialTable] = Field(default_factory=list)
    reconciliation_findings: list[ReconciliationFinding] = Field(default_factory=list)
    clauses: list[Clause] = Field(default_factory=list)
    cross_clause_findings: list[CrossClauseFinding] = Field(default_factory=list)
    missing_required_clauses: list[Issue] = Field(default_factory=list)
    extraction_quality: Optional[ExtractionQuality] = None
    degraded: bool = False
    model: str
    thinking_tokens: int = 0
    analyzer_version: str = "2.0"
