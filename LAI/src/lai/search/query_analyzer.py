"""Rule-based query analyzer for German legal queries.

Extracts legal references, dates, and detects query intent
without using an LLM — pure regex for speed and determinism.
"""

import re
from datetime import date, timedelta

from lai.core.constants import GERMAN_LAW_CODES, QueryIntent
from lai.core.logging import get_logger
from lai.core.models import ExtractedDateReference, ExtractedLegalReference, LegalReferenceType, ParsedQuery

logger = get_logger("lai.search.query_analyzer")

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

PARAGRAPH_PATTERN = re.compile(
    r'§§?\s*'
    r'(\d+[a-z]?)'
    r'(?:\s*,\s*\d+[a-z]?)*'
    r'(?:\s*(?:Abs(?:atz)?\.?)\s*(\d+))?'
    r'(?:\s*(?:S(?:atz)?\.?)\s*(\d+))?'
    r'(?:\s*(?:Nr\.?|Nummer)\s*(\d+))?'
    r'(?:\s+([A-Z][A-Za-z]{1,15}))?',
    re.IGNORECASE,
)

ARTICLE_PATTERN = re.compile(
    r'Art(?:ikel)?\.?\s*'
    r'(\d+)'
    r'(?:\s*(?:Abs(?:atz)?\.?)\s*(\d+))?'
    r'(?:\s*(?:S(?:atz)?\.?)\s*(\d+))?'
    r'(?:\s+([A-Z][A-Za-z]{1,15}))?',
    re.IGNORECASE,
)

_LAW_CODES_PATTERN = '|'.join(re.escape(c) for c in sorted(GERMAN_LAW_CODES, key=len, reverse=True))
LAW_CODE_PATTERN = re.compile(rf'\b({_LAW_CODES_PATTERN})\b', re.IGNORECASE)

BGH_AZ_PATTERN = re.compile(r'\b([IVX]+)\s*(ZR|ZB|AR|StR|RiZ|BLw|EnZR|KZR)\s*(\d+)/(\d{2})\b', re.IGNORECASE)
BVERFG_AZ_PATTERN = re.compile(r'\b(\d)\s*Bv([RFLEQGK])\s*(\d+)/(\d{2})\b', re.IGNORECASE)
GENERAL_AZ_PATTERN = re.compile(r'\bAz\.?\s*:?\s*([A-Z0-9\s\-/]+\d+/\d{2,4})\b', re.IGNORECASE)

GERMAN_DATE_PATTERN = re.compile(r'\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b')
WRITTEN_DATE_PATTERN = re.compile(
    r'\b(\d{1,2})?\s*\.?\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|'
    r'September|Oktober|November|Dezember)\s+(\d{4})\b',
    re.IGNORECASE,
)

GERMAN_MONTHS = {
    'januar': 1, 'februar': 2, 'märz': 3, 'april': 4, 'mai': 5, 'juni': 6,
    'juli': 7, 'august': 8, 'september': 9, 'oktober': 10, 'november': 11, 'dezember': 12,
}

# Intent indicator patterns
_INTENT_PATTERNS = {
    QueryIntent.HISTORICAL_LAW: re.compile(
        r'früher|damals|alte\s+fassung|a\.?\s*f\.?|stand\s+\d|galt\s+bis|war\s+gültig|historisch', re.I
    ),
    QueryIntent.COURT_RULING: re.compile(
        r'urteil|beschluss|entscheidung|rechtsprechung|gericht|richter|klage|berufung|revision|aktenzeichen|\baz\.?\b', re.I
    ),
    QueryIntent.LAW_COMPARISON: re.compile(r'unterschied|vergleich|gegenüber|anders\s+als|im\s+gegensatz', re.I),
    QueryIntent.DEFINITION: re.compile(r'was\s+ist|was\s+bedeutet|was\s+versteht\s+man|definition|bedeutung|erklär|beschreib', re.I),
    QueryIntent.PROCEDURE: re.compile(r'wie\s+(?:muss|kann|soll)|verfahren|ablauf|schritt|frist|antrag', re.I),
    QueryIntent.CURRENT_LAW: re.compile(r'aktuell|geltend|derzeitig|momentan|heute|jetzt|gegenwärtig|gültig', re.I),
}


class QueryAnalyzer:
    """Rule-based query analyzer for German legal queries."""

    def analyze(self, query: str) -> ParsedQuery:
        if not query or not query.strip():
            return ParsedQuery(original_text="", normalized_text="", intent=QueryIntent.AMBIGUOUS)

        normalized = ' '.join(query.split())
        legal_refs = self._extract_legal_references(query)
        date_refs = self._extract_date_references(query)
        law_codes = self._extract_law_codes(query)
        paragraph_refs = self._extract_paragraph_refs(query)
        article_refs = self._extract_article_refs(query)
        court_refs = self._extract_court_refs(query)
        intent = self._detect_intent(query, legal_refs, date_refs)

        parsed = ParsedQuery(
            original_text=query,
            normalized_text=normalized,
            intent=intent,
            legal_references=legal_refs,
            date_references=date_refs,
            law_codes=law_codes,
            paragraph_refs=paragraph_refs,
            article_refs=article_refs,
            court_refs=court_refs,
        )
        logger.debug("Query analyzed: intent=%s, law_codes=%s, paragraphs=%s", intent.value, law_codes, paragraph_refs)
        return parsed

    def _extract_legal_references(self, query: str) -> list[ExtractedLegalReference]:
        refs: list[ExtractedLegalReference] = []
        for match in PARAGRAPH_PATTERN.finditer(query):
            number, subsection, sentence, _, law_code = match.groups()
            refs.append(ExtractedLegalReference(
                ref_type=LegalReferenceType.PARAGRAPH,
                raw_text=match.group(0).strip(),
                normalized=_norm_paragraph(number, subsection, sentence, law_code),
                law_code=law_code.upper() if law_code else None,
                number=number, subsection=subsection, sentence=sentence,
                confidence=0.95 if law_code else 0.8,
            ))
        for match in ARTICLE_PATTERN.finditer(query):
            number, subsection, sentence, law_code = match.groups()
            refs.append(ExtractedLegalReference(
                ref_type=LegalReferenceType.ARTICLE,
                raw_text=match.group(0).strip(),
                normalized=_norm_article(number, subsection, law_code),
                law_code=law_code.upper() if law_code else None,
                number=number, subsection=subsection, sentence=sentence,
                confidence=0.95 if law_code else 0.8,
            ))
        for pattern in [BGH_AZ_PATTERN, BVERFG_AZ_PATTERN, GENERAL_AZ_PATTERN]:
            for match in pattern.finditer(query):
                refs.append(ExtractedLegalReference(
                    ref_type=LegalReferenceType.COURT_DECISION,
                    raw_text=match.group(0).strip(),
                    normalized=match.group(0).strip().upper(),
                    confidence=0.9,
                ))
        return refs

    def _extract_law_codes(self, query: str) -> list[str]:
        return sorted({m.group(1).upper() for m in LAW_CODE_PATTERN.finditer(query)})

    def _extract_paragraph_refs(self, query: str) -> list[str]:
        refs = []
        for m in PARAGRAPH_PATTERN.finditer(query):
            n = _norm_paragraph(m.group(1), m.group(2), m.group(3), m.group(5))
            if n not in refs:
                refs.append(n)
        return refs

    def _extract_article_refs(self, query: str) -> list[str]:
        refs = []
        for m in ARTICLE_PATTERN.finditer(query):
            n = _norm_article(m.group(1), m.group(2), m.group(4))
            if n not in refs:
                refs.append(n)
        return refs

    def _extract_court_refs(self, query: str) -> list[str]:
        refs = []
        for pattern in [BGH_AZ_PATTERN, BVERFG_AZ_PATTERN, GENERAL_AZ_PATTERN]:
            for m in pattern.finditer(query):
                r = m.group(0).strip()
                if r not in refs:
                    refs.append(r)
        return refs

    def _extract_date_references(self, query: str) -> list[ExtractedDateReference]:
        refs: list[ExtractedDateReference] = []
        for m in GERMAN_DATE_PATTERN.finditer(query):
            day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if year < 100:
                year += 2000 if year < 50 else 1900
            try:
                refs.append(ExtractedDateReference(raw_text=m.group(0), resolved_date=date(year, month, day), confidence=0.95))
            except ValueError:
                pass
        for m in WRITTEN_DATE_PATTERN.finditer(query):
            day_str, month_name, year = m.groups()
            month_num = GERMAN_MONTHS.get(month_name.lower())
            if month_num:
                try:
                    refs.append(ExtractedDateReference(
                        raw_text=m.group(0),
                        resolved_date=date(int(year), month_num, int(day_str) if day_str else 1),
                        confidence=0.9,
                    ))
                except ValueError:
                    pass
        return refs

    def _detect_intent(self, query: str, legal_refs: list, date_refs: list) -> QueryIntent:
        q = query.lower()
        if _INTENT_PATTERNS[QueryIntent.HISTORICAL_LAW].search(q):
            return QueryIntent.HISTORICAL_LAW
        if _INTENT_PATTERNS[QueryIntent.COURT_RULING].search(q):
            return QueryIntent.COURT_RULING
        if any(r.ref_type == LegalReferenceType.COURT_DECISION for r in legal_refs):
            return QueryIntent.COURT_RULING
        if _INTENT_PATTERNS[QueryIntent.LAW_COMPARISON].search(q):
            return QueryIntent.LAW_COMPARISON
        if _INTENT_PATTERNS[QueryIntent.DEFINITION].search(q):
            return QueryIntent.DEFINITION
        if _INTENT_PATTERNS[QueryIntent.PROCEDURE].search(q):
            return QueryIntent.PROCEDURE
        if _INTENT_PATTERNS[QueryIntent.CURRENT_LAW].search(q):
            return QueryIntent.CURRENT_LAW
        if legal_refs:
            return QueryIntent.CURRENT_LAW
        return QueryIntent.GENERAL_INFO


def _norm_paragraph(number: str | None, subsection: str | None, sentence: str | None, law_code: str | None) -> str:
    parts = [f"§ {number}"]
    if subsection:
        parts.append(f"Abs. {subsection}")
    if sentence:
        parts.append(f"S. {sentence}")
    if law_code:
        parts.append(law_code.upper())
    return " ".join(parts)


def _norm_article(number: str | None, subsection: str | None, law_code: str | None) -> str:
    parts = [f"Art. {number}"]
    if subsection:
        parts.append(f"Abs. {subsection}")
    if law_code:
        parts.append(law_code.upper())
    return " ".join(parts)
