"""Citation verifier for LLM responses.

Verifies that all legal citations in LLM output exist in the
retrieved context. Principle: unverified citation = refuse response.
"""

import re
from dataclasses import dataclass, field

from lai.core.logging import get_logger

logger = get_logger("lai.generation.citation_verifier")

CITATION_PATTERNS = {
    "paragraph": [
        re.compile(r'§§?\s*\d+[a-z]?(?:\s*(?:bis|-)\s*\d+[a-z]?)?(?:\s*(?:Abs\.|Absatz)\s*\d+)?(?:\s*(?:S\.|Satz|Nr\.|Nummer)\s*\d+)?', re.I),
    ],
    "article": [
        re.compile(r'Art(?:ikel)?\.?\s*\d+[a-z]?(?:\s*(?:Abs\.|Absatz)\s*\d+)?', re.I),
    ],
    "law_code": [
        re.compile(r'\b(?:BGB|StGB|StPO|ZPO|GG|VwGO|AO|HGB|InsO|UrhG|UWG|BDSG|DSGVO|AGG|KSchG|BetrVG|SGB\s*[IVXLC]+|BauGB|BImSchG|EEG|WindSeeG|NABEG|EnWG|BNatSchG)\b', re.I),
    ],
    "court_decision": [
        re.compile(r'(?:Az\.?|Aktenzeichen)\s*:?\s*[IVXLC]*\s*[A-Z]{1,3}\s*\d+/\d{2,4}', re.I),
        re.compile(r'(?:BGH|BVerfG|BFH|BAG|BSG|BVerwG|OLG|LG|AG)\s*,?\s*(?:Urteil|Beschluss)?\s*(?:vom|v\.)\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}', re.I),
    ],
}


@dataclass
class CitationMatch:
    text: str
    status: str  # "verified", "partial", "not_found"
    source_chunk_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class VerificationResult:
    total: int
    verified: int
    not_found: int
    citations: list[CitationMatch]
    passed: bool
    rate: float
    unverified: list[str]


class CitationVerifier:
    """Verifies citations in LLM responses against retrieved context."""

    def __init__(self, strict: bool = True) -> None:
        self._strict = strict
        logger.info("CitationVerifier initialized: strict=%s", strict)

    def verify(self, response_text: str, chunks: list[dict]) -> VerificationResult:
        """Verify all citations in response against context chunks."""
        citations = self._extract_citations(response_text)

        if not citations:
            return VerificationResult(total=0, verified=0, not_found=0, citations=[], passed=True, rate=1.0, unverified=[])

        context = self._build_context(chunks)
        matches = []
        verified = 0
        not_found = 0
        unverified = []

        for citation in citations:
            normalized = self._normalize(citation)
            if normalized in context or citation.lower() in context:
                source_ids = self._find_sources(citation, chunks)
                matches.append(CitationMatch(text=citation, status="verified", source_chunk_ids=source_ids, confidence=1.0))
                verified += 1
            elif self._partial_match(citation, context):
                matches.append(CitationMatch(text=citation, status="partial", confidence=0.6))
                verified += 1  # Count partial as verified
            else:
                matches.append(CitationMatch(text=citation, status="not_found"))
                not_found += 1
                unverified.append(citation)

        rate = verified / len(citations) if citations else 1.0
        passed = (not_found == 0) if self._strict else (rate >= 0.8)

        logger.info("Citation verification: %d/%d verified (rate=%.0f%%, passed=%s)", verified, len(citations), rate * 100, passed)
        return VerificationResult(total=len(citations), verified=verified, not_found=not_found, citations=matches, passed=passed, rate=rate, unverified=unverified)

    def _extract_citations(self, text: str) -> list[str]:
        seen = set()
        citations = []
        for patterns in CITATION_PATTERNS.values():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    normalized = self._normalize(match.group())
                    if normalized and normalized not in seen:
                        citations.append(match.group().strip())
                        seen.add(normalized)
        return citations

    def _normalize(self, citation: str) -> str:
        return " ".join(citation.split()).lower()

    def _build_context(self, chunks: list[dict]) -> str:
        parts = []
        for c in chunks:
            parts.append(c.get("text_clean", ""))
            parts.extend(c.get("paragraph_refs", []))
            parts.extend(c.get("article_refs", []))
            parts.extend(c.get("law_refs", []))
        return " ".join(parts).lower()

    def _partial_match(self, citation: str, context: str) -> bool:
        components = []
        para = re.search(r'§\s*(\d+[a-z]?)', citation, re.I)
        if para:
            components.append(f"§ {para.group(1)}")
        art = re.search(r'art\.?\s*(\d+[a-z]?)', citation, re.I)
        if art:
            components.append(f"art. {art.group(1)}")
        for code in ["bgb", "stgb", "zpo", "gg", "bimschg", "eeg"]:
            if code in citation.lower():
                components.append(code)
        if not components:
            return False
        found = sum(1 for c in components if c in context)
        return found / len(components) >= 0.5

    def _find_sources(self, citation: str, chunks: list[dict]) -> list[str]:
        citation_lower = citation.lower()
        ids = []
        for c in chunks:
            if citation_lower in c.get("text_clean", "").lower():
                ids.append(str(c.get("chunk_id", "")))
        return ids


_verifier: CitationVerifier | None = None


def get_citation_verifier() -> CitationVerifier:
    global _verifier
    if _verifier is None:
        _verifier = CitationVerifier()
    return _verifier
