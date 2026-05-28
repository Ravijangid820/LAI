"""Shared domain models for the LAI platform.

Consolidated from V4 models/ directory. All models used across
packages (retrieval, generation, api) live here.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from lai.core.constants import QueryIntent

# ---------------------------------------------------------------------------
# Legal reference models
# ---------------------------------------------------------------------------


class LegalReferenceType(StrEnum):
    PARAGRAPH = "paragraph"
    ARTICLE = "article"
    LAW_CODE = "law_code"
    COURT_DECISION = "court_decision"


@dataclass
class ExtractedLegalReference:
    ref_type: LegalReferenceType
    raw_text: str
    normalized: str
    law_code: str | None = None
    number: str | None = None
    subsection: str | None = None
    sentence: str | None = None
    confidence: float = 1.0

    @property
    def full_reference(self) -> str:
        parts = []
        if self.ref_type == LegalReferenceType.PARAGRAPH:
            parts.append(f"\u00a7 {self.number}" if self.number else "\u00a7")
        elif self.ref_type == LegalReferenceType.ARTICLE:
            parts.append(f"Art. {self.number}" if self.number else "Art.")
        elif self.ref_type == LegalReferenceType.LAW_CODE:
            return self.law_code or self.normalized
        elif self.ref_type == LegalReferenceType.COURT_DECISION:
            return self.raw_text
        if self.subsection:
            parts.append(f"Abs. {self.subsection}")
        if self.sentence:
            parts.append(f"S. {self.sentence}")
        if self.law_code:
            parts.append(self.law_code)
        return " ".join(parts)


@dataclass
class ExtractedDateReference:
    raw_text: str
    resolved_date: date | None = None
    date_type: str = "point_in_time"
    is_relative: bool = False
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Query models
# ---------------------------------------------------------------------------


@dataclass
class ParsedQuery:
    original_text: str
    normalized_text: str
    intent: QueryIntent = QueryIntent.GENERAL_INFO
    legal_references: list[ExtractedLegalReference] = field(default_factory=list)
    date_references: list[ExtractedDateReference] = field(default_factory=list)
    law_codes: list[str] = field(default_factory=list)
    paragraph_refs: list[str] = field(default_factory=list)
    article_refs: list[str] = field(default_factory=list)
    court_refs: list[str] = field(default_factory=list)
    temporal_context: date | None = None
    requires_clarification: bool = False
    clarification_type: str | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_legal_references(self) -> bool:
        return bool(
            self.legal_references or self.law_codes or self.paragraph_refs or self.article_refs or self.court_refs
        )

    @property
    def has_temporal_constraint(self) -> bool:
        return bool(self.temporal_context or self.date_references)

    @property
    def is_historical_query(self) -> bool:
        return self.intent == QueryIntent.HISTORICAL_LAW or (
            self.has_temporal_constraint and self.temporal_context is not None and self.temporal_context < date.today()
        )

    @property
    def required_entities(self) -> list[str]:
        entities: list[str] = []
        entities.extend(self.law_codes)
        entities.extend(self.paragraph_refs)
        entities.extend(self.article_refs)
        return entities

    def get_search_filters(self) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if self.law_codes:
            filters["law_refs"] = self.law_codes
        if self.temporal_context:
            if self.intent == QueryIntent.HISTORICAL_LAW:
                filters["effective_date_to"] = self.temporal_context
            else:
                filters["is_current_only"] = True
        if self.intent == QueryIntent.COURT_RULING:
            filters["doc_types"] = ["ruling"]
        return filters


# ---------------------------------------------------------------------------
# Retrieval models
# ---------------------------------------------------------------------------


class QualityCheckStatus(StrEnum):
    PASSED = "passed"
    INSUFFICIENT_CHUNKS = "insufficient_chunks"
    LOW_SIMILARITY = "low_similarity"
    MISSING_ENTITIES = "missing_entities"
    EMPTY_RETRIEVAL = "empty_retrieval"


@dataclass
class RankedChunk:
    """A retrieved chunk with scoring information."""

    chunk_id: UUID
    document_id: UUID
    user_id: UUID
    text_clean: str
    text_tagged: str | None = None
    section: str | None = None
    subsection: str | None = None
    chunk_index: int = 0

    # Legal references
    paragraph_refs: list[str] = field(default_factory=list)
    article_refs: list[str] = field(default_factory=list)
    law_refs: list[str] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)

    # Temporal
    effective_date: date | None = None
    is_current: bool = True

    # Document info
    doc_type: str | None = None
    court_level: int | None = None
    decision_date: date | None = None

    # Scores
    dense_score: float | None = None
    sparse_score: float | None = None
    hybrid_score: float | None = None
    rerank_score: float | None = None
    quality_score: float = 1.0  # Feedback-adjusted quality
    final_score: float = 0.0
    final_rank: int = 0

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def all_references(self) -> list[str]:
        refs: list[str] = []
        refs.extend(self.paragraph_refs)
        refs.extend(self.article_refs)
        refs.extend(self.law_refs)
        return refs

    def contains_entity(self, entity: str) -> bool:
        entity_lower = entity.lower()
        for ref_list in (self.law_refs, self.paragraph_refs, self.article_refs):
            if any(entity_lower in ref.lower() for ref in ref_list):
                return True
        return entity_lower in self.text_clean.lower()

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "section": self.section,
            "paragraph_refs": self.paragraph_refs,
            "article_refs": self.article_refs,
            "law_refs": self.law_refs,
            "doc_type": self.doc_type,
            "court_level": self.court_level,
            "final_score": self.final_score,
            "final_rank": self.final_rank,
        }
        if include_text:
            result["text_clean"] = self.text_clean
        return result


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ResponseStatus(StrEnum):
    GENERATED = "generated"
    REFUSED = "refused"
    CLARIFICATION_NEEDED = "clarification_needed"
    PARTIAL = "partial"
    ERROR = "error"


class CitationStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL_MATCH = "partial_match"
    NOT_FOUND = "not_found"


@dataclass
class Citation:
    raw_text: str
    normalized: str
    citation_type: str = "unknown"
    law_code: str | None = None
    section_number: str | None = None
    subsection: str | None = None
    status: CitationStatus = CitationStatus.NOT_FOUND
    matched_chunk_id: UUID | None = None
    confidence: float = 0.0

    @property
    def is_verified(self) -> bool:
        return self.status == CitationStatus.VERIFIED


@dataclass
class VerificationResult:
    citations: list[Citation] = field(default_factory=list)
    verified_count: int = 0
    total_count: int = 0

    @property
    def verification_rate(self) -> float:
        if self.total_count == 0:
            return 1.0
        return self.verified_count / self.total_count

    @property
    def all_verified(self) -> bool:
        return self.total_count == 0 or self.verified_count == self.total_count


@dataclass
class RAGResponse:
    status: ResponseStatus
    response_text: str | None = None
    citations: list[Citation] = field(default_factory=list)
    citations_verified: int = 0
    citations_total: int = 0
    verification_rate: float = 0.0
    refusal_reason: str | None = None
    clarification_prompt: str | None = None
    sources_used: int = 0
    source_chunk_ids: list[UUID] = field(default_factory=list)
    query_text: str = ""
    query_intent: str = ""
    retrieval_quality: str = ""
    generation_model: str = ""
    generation_time_ms: float = 0.0
    total_time_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    request_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    node_timings: dict[str, float] = field(default_factory=dict)
    total_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "response_text": self.response_text,
            "citations": [
                {"text": c.raw_text, "verified": c.is_verified, "law_code": c.law_code} for c in self.citations
            ],
            "citations_verified": self.citations_verified,
            "citations_total": self.citations_total,
            "verification_rate": self.verification_rate,
            "refusal_reason": self.refusal_reason,
            "sources_used": self.sources_used,
            "query_intent": self.query_intent,
            "generation_time_ms": self.generation_time_ms,
            "total_time_ms": self.total_time_ms,
            "timestamp": self.timestamp.isoformat(),
            "request_id": self.request_id,
            "warnings": self.warnings,
            "node_timings": self.node_timings,
            "total_tokens": self.total_tokens,
        }

    def to_api_response(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status.value,
            "request_id": self.request_id,
        }
        if self.status == ResponseStatus.GENERATED:
            result["answer"] = self.response_text
            result["citations"] = [
                {"text": c.raw_text, "verified": c.is_verified, "law_code": c.law_code} for c in self.citations
            ]
            result["sources_used"] = self.sources_used
        elif self.status == ResponseStatus.REFUSED:
            result["message"] = self.refusal_reason
        elif self.status == ResponseStatus.CLARIFICATION_NEEDED:
            result["message"] = self.clarification_prompt
        elif self.status == ResponseStatus.ERROR:
            result["message"] = self.refusal_reason or "An error occurred"
        if self.warnings:
            result["warnings"] = self.warnings
        return result


# ---------------------------------------------------------------------------
# Document models
# ---------------------------------------------------------------------------


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DocumentChunk:
    text_clean: str
    text_tagged: str | None = None
    section: str | None = None
    subsection: str | None = None
    chunk_index: int = 0
    paragraph_refs: list[str] = field(default_factory=list)
    article_refs: list[str] = field(default_factory=list)
    law_refs: list[str] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)
    effective_date: date | None = None
    doc_type: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    title: str | None = None
    elements: list[dict[str, Any]] = field(default_factory=list)
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessingResult:
    document_id: str = ""
    status: DocumentStatus = DocumentStatus.COMPLETED
    chunks_created: int = 0
    total_pages: int = 0
    file_size_bytes: int = 0
    processing_time_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "status": self.status.value,
            "chunks_created": self.chunks_created,
            "total_pages": self.total_pages,
            "processing_time_ms": self.processing_time_ms,
            "errors": self.errors,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Feedback models
# ---------------------------------------------------------------------------


@dataclass
class FeedbackRecord:
    query_text: str
    response_text: str
    feedback_type: str
    correction_text: str | None = None
    chunk_ids: list[str] = field(default_factory=list)
    user_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_text": self.query_text,
            "response_text": self.response_text,
            "feedback_type": self.feedback_type,
            "correction_text": self.correction_text,
            "chunk_ids": self.chunk_ids,
            "timestamp": self.timestamp.isoformat(),
        }


# ---------------------------------------------------------------------------
# Analysis response models
# ---------------------------------------------------------------------------


@dataclass
class AnalysisResponse:
    document_id: str = ""
    analysis_type: str = ""
    summary: str | None = None
    sections: list[dict] = field(default_factory=list)
    extracted_items: list[dict] = field(default_factory=list)
    classification: str | None = None
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    processing_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "analysis_type": self.analysis_type,
            "summary": self.summary,
            "sections": self.sections,
            "extracted_items": self.extracted_items,
            "classification": self.classification,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "processing_time_ms": self.processing_time_ms,
        }


@dataclass
class ClauseDiff:
    clause_number: str = ""
    clause_title: str = ""
    doc1_text: str = ""
    doc2_text: str = ""
    status: str = "matching"
    differences: list[str] = field(default_factory=list)
    risk_level: str = "low"


@dataclass
class ComparisonResponse:
    document_id_1: str = ""
    document_id_2: str = ""
    matching_clauses: list[ClauseDiff] = field(default_factory=list)
    conflicting_clauses: list[ClauseDiff] = field(default_factory=list)
    missing_clauses: list[ClauseDiff] = field(default_factory=list)
    added_clauses: list[ClauseDiff] = field(default_factory=list)
    overall_risk: str = "low"
    summary: str = ""
    processing_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id_1": self.document_id_1,
            "document_id_2": self.document_id_2,
            "matching_clauses": len(self.matching_clauses),
            "conflicting_clauses": [
                {"number": c.clause_number, "title": c.clause_title, "differences": c.differences, "risk": c.risk_level}
                for c in self.conflicting_clauses
            ],
            "overall_risk": self.overall_risk,
            "summary": self.summary,
            "processing_time_ms": self.processing_time_ms,
        }
