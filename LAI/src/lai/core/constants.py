"""Constants for German legal domain processing.

Contains law codes, regex patterns, intent keywords, and prompt templates
used across the LAI platform. Consolidated from V3 constants.py and V4 constants.py.
"""

import re
from enum import Enum

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DocumentType(str, Enum):
    LEGISLATION = "legislation"
    COURT_DECISION = "court_decision"
    COMMENTARY = "commentary"
    CONTRACT = "contract"
    REGULATION = "regulation"
    PERMIT = "permit"
    REPORT = "report"
    UNKNOWN = "unknown"


class CourtLevel(int, Enum):
    FEDERAL = 1  # BVerfG, BGH, BVerwG, BAG, BSG, BFH
    HIGHER_REGIONAL = 2  # OLG, OVG, VGH, LAG, LSG, FG
    REGIONAL = 3  # LG, VG, ArbG, SG
    LOCAL = 4  # AG


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class QueryIntent(str, Enum):
    CURRENT_LAW = "current_law"
    HISTORICAL_LAW = "historical_law"
    COURT_RULING = "court_ruling"
    LAW_COMPARISON = "law_comparison"
    GENERAL_INFO = "general_info"
    DEFINITION = "definition"
    PROCEDURE = "procedure"
    AMBIGUOUS = "ambiguous"


class FeedbackType(str, Enum):
    WRONG_LAW_CITED = "wrong_law_cited"
    OUTDATED_INFO = "outdated_info"
    HALLUCINATED_CONTENT = "hallucinated_content"
    INCOMPLETE_ANSWER = "incomplete_answer"
    WRONG_TRANSLATION = "wrong_translation"
    IRRELEVANT_RESULT = "irrelevant_result"


# ---------------------------------------------------------------------------
# German law codes
# ---------------------------------------------------------------------------

GERMAN_LAW_CODES: set[str] = {
    # Major codes
    "BGB",
    "StGB",
    "ZPO",
    "StPO",
    "GG",
    "HGB",
    "AO",
    "InsO",
    "VwGO",
    "FGO",
    "GVG",
    "BVerfGG",
    "GewO",
    "BImSchG",
    # Civil law
    "EGBGB",
    "WEG",
    "MietNovG",
    "ErbbauRG",
    "AGG",
    "ProdHaftG",
    # Labor law
    "ArbGG",
    "KSchG",
    "BetrVG",
    "TzBfG",
    "ArbZG",
    "MuSchG",
    "BEEG",
    "EntgFG",
    "AUG",
    "MiLoG",
    # Commercial / corporate
    "GmbHG",
    "AktG",
    "UmwG",
    "GenG",
    "PartGG",
    # Administrative
    "VwVfG",
    "BauGB",
    "PolG",
    "OWiG",
    "AsylG",
    "AufenthG",
    # Social law (SGB books)
    "SGB I",
    "SGB II",
    "SGB III",
    "SGB IV",
    "SGB V",
    "SGB VI",
    "SGB VII",
    "SGB VIII",
    "SGB IX",
    "SGB X",
    "SGB XI",
    "SGB XII",
    "SGB XIV",
    # Tax
    "EStG",
    "UStG",
    "KStG",
    "GewStG",
    "BewG",
    # IP / Media
    "UrhG",
    "MarkenG",
    "PatG",
    "TMG",
    "TTDSG",
    # Data protection
    "BDSG",
    "DSGVO",
    # EU
    "AEUV",
    "EUV",
    # Energy / wind specific
    "EEG",
    "WindSeeG",
    "NABEG",
    "EnWG",
    "LuftVG",
    # Other
    "StVG",
    "StVO",
    "BtMG",
    "WaffG",
    "TierSchG",
    "UWG",
    "BNatSchG",
}

SGB_BOOKS: dict[str, str] = {
    "1": "SGB I",
    "2": "SGB II",
    "3": "SGB III",
    "4": "SGB IV",
    "5": "SGB V",
    "6": "SGB VI",
    "7": "SGB VII",
    "8": "SGB VIII",
    "9": "SGB IX",
    "10": "SGB X",
    "11": "SGB XI",
    "12": "SGB XII",
    "14": "SGB XIV",
    "I": "SGB I",
    "II": "SGB II",
    "III": "SGB III",
    "IV": "SGB IV",
    "V": "SGB V",
    "VI": "SGB VI",
    "VII": "SGB VII",
    "VIII": "SGB VIII",
    "IX": "SGB IX",
    "X": "SGB X",
    "XI": "SGB XI",
    "XII": "SGB XII",
    "XIV": "SGB XIV",
}

COURT_NAME_TO_LEVEL: dict[str, int] = {
    "BVerfG": 1,
    "BGH": 1,
    "BVerwG": 1,
    "BAG": 1,
    "BSG": 1,
    "BFH": 1,
    "BPatG": 1,
    "OLG": 2,
    "OVG": 2,
    "VGH": 2,
    "LAG": 2,
    "LSG": 2,
    "FG": 2,
    "LG": 3,
    "VG": 3,
    "ArbG": 3,
    "SG": 3,
    "AG": 4,
}

GERMAN_MONTH_TO_NUM: dict[str, int] = {
    "Januar": 1,
    "Februar": 2,
    "Marz": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}


# ---------------------------------------------------------------------------
# Regex patterns for legal reference extraction
# ---------------------------------------------------------------------------

PARAGRAPH_PATTERN = re.compile(
    r"§§?\s*(\d+[a-z]?)"
    r"(?:\s*(?:bis|[-\u2013])\s*(\d+[a-z]?))?"
    r"(?:\s+Abs\.\s*(\d+))?"
    r"(?:\s+S\.\s*(\d+))?"
    r"(?:\s+Nr\.\s*(\d+))?"
    r"(?:\s+([A-Z\u00c4\u00d6\u00dc][A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df]+(?:\s+[IVX]+)?))?",
    re.UNICODE,
)

ARTICLE_PATTERN = re.compile(
    r"(?:Art\.|Artikel)\s*(\d+[a-z]?)"
    r"(?:\s+Abs\.\s*(\d+))?"
    r"(?:\s+S\.\s*(\d+))?"
    r"(?:\s+([A-Z\u00c4\u00d6\u00dc][A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df]+(?:\s+[IVX]+)?))?",
    re.UNICODE,
)

COURT_DECISION_PATTERN = re.compile(
    r"(BVerfG|BGH|BVerwG|BAG|BSG|BFH|OLG|OVG|VGH|LAG|LSG|LG|VG|ArbG|SG|AG|FG)"
    r"\s+"
    r"(?:[IVX]+\s+)?"
    r"([A-Za-z]+)\s+"
    r"(\d+/\d+)",
    re.UNICODE,
)

DATE_PATTERN_NUMERIC = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
DATE_PATTERN_WRITTEN = re.compile(
    r"(\d{1,2})\.\s*"
    r"(Januar|Februar|M\u00e4rz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)"
    r"\s+(\d{4})",
    re.UNICODE,
)


# ---------------------------------------------------------------------------
# Intent detection keywords
# ---------------------------------------------------------------------------

COMPARISON_KEYWORDS = {
    "vergleich",
    "unterschied",
    "differenz",
    "gegenuberstellung",
    "versus",
    "vs",
    "im vergleich",
    "anders als",
    "abgrenzung",
    "sowohl",
    "einerseits",
    "andererseits",
}

DEFINITION_KEYWORDS = {
    "definition",
    "was ist",
    "was sind",
    "was bedeutet",
    "bedeutung",
    "was versteht man",
    "begriff",
    "legaldefinition",
}

PROCEDURE_KEYWORDS = {
    "verfahren",
    "ablauf",
    "vorgehen",
    "schritt",
    "frist",
    "antrag",
    "klage erheben",
    "rechtsmittel",
    "berufung",
    "revision",
    "beschwerde",
    "widerspruch",
}

HISTORICAL_KEYWORDS = {
    "fruher",
    "damals",
    "alte fassung",
    "a.f.",
    "a. f.",
    "vor der reform",
    "bis zum",
    "gultig bis",
}

COMPLEX_QUERY_PATTERNS = [
    re.compile(r"(?:vergleich|unterschied|abgrenzung)", re.IGNORECASE),
    re.compile(r"sowohl\s.*\sals auch", re.IGNORECASE),
    re.compile(r"einerseits\s.*\sandererseits", re.IGNORECASE),
    re.compile(r"\bund\b.*\bund\b", re.IGNORECASE),
    re.compile(r"\u00a7\s*\d+.*\u00a7\s*\d+", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Prompt templates (German-first)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_LEGAL_RAG = """Du bist ein juristischer Assistent fur deutsches Recht. Beantworte die Frage ausschliesslich auf Basis der bereitgestellten Quellen.

REGELN:
1. Verwende NUR Informationen aus den bereitgestellten Quellen.
2. Zitiere alle verwendeten Quellen mit exakten Rechtsverweisen (SS, Art., Gesetz).
3. Wenn die Quellen keine ausreichende Information enthalten, sage dies klar.
4. Verwende formale juristische Sprache.
5. Erfinde NIEMALS Informationen oder Zitate.
6. Strukturiere die Antwort klar mit Absatzen."""

SYSTEM_PROMPT_DEFINITION = """Du bist ein juristischer Assistent. Definiere den angefragten Rechtsbegriff auf Basis der bereitgestellten Quellen.

REGELN:
1. Gib die Legaldefinition an, falls vorhanden.
2. Erklare den Begriff in juristischem Kontext.
3. Zitiere die genaue Fundstelle.
4. Erfinde KEINE Definitionen."""

SYSTEM_PROMPT_COMPARISON = """Du bist ein juristischer Assistent. Vergleiche die angefragten Rechtsnormen auf Basis der bereitgestellten Quellen.

REGELN:
1. Stelle Gemeinsamkeiten und Unterschiede klar heraus.
2. Zitiere fur jeden Punkt die genaue Fundstelle.
3. Verwende eine strukturierte Darstellung.
4. Basiere den Vergleich ausschliesslich auf den Quellen."""

SYSTEM_PROMPT_COURT_RULING = """Du bist ein juristischer Assistent. Beantworte die Frage zu Rechtsprechung auf Basis der bereitgestellten Quellen.

REGELN:
1. Nenne das Gericht, das Aktenzeichen und das Datum der Entscheidung.
2. Gib die Kernaussage der Entscheidung wieder.
3. Zitiere relevante Leitsatze.
4. Erfinde KEINE Entscheidungen oder Aktenzeichen."""

SYSTEM_PROMPT_PROCEDURE = """Du bist ein juristischer Assistent. Erklare das angefragte Verfahren auf Basis der bereitgestellten Quellen.

REGELN:
1. Beschreibe den Ablauf Schritt fur Schritt.
2. Nenne relevante Fristen und Voraussetzungen.
3. Zitiere die genauen Rechtsgrundlagen.
4. Erfinde KEINE Verfahrensschritte."""

SYSTEM_PROMPT_BIMSCHG = """Du bist ein juristischer Assistent spezialisiert auf Immissionsschutzrecht und Genehmigungsverfahren nach BImSchG.

REGELN:
1. Berucksichtige TA Larm, TA Luft und relevante Durchfuhrungsverordnungen.
2. Nenne konkrete Grenzwerte und Messverfahren wenn zutreffend.
3. Zitiere SS-Verweise und Verordnungsnummern exakt.
4. Beachte die Abgrenzung zwischen foermlichem und vereinfachtem Verfahren."""

SYSTEM_PROMPT_CONTRACT = """Du bist ein juristischer Assistent spezialisiert auf Vertragsrecht und Due Diligence.

REGELN:
1. Identifiziere Kundigungsrechte, Fristen und Haftungsklauseln.
2. Bewerte Risiken als niedrig, mittel oder hoch.
3. Zitiere die genauen Vertragsklauseln.
4. Weise auf fehlende oder unubliche Klauseln hin."""

# CRAG grading prompts
GRADING_SYSTEM_PROMPT = """Du bist ein Bewertungsassistent. Bewerte, ob das folgende Dokument relevant fur die Beantwortung der Frage ist. Antworte ausschliesslich mit 'ja' oder 'nein'."""

GRADING_USER_TEMPLATE = """Frage: {query}

Dokument:
{document}

Ist dieses Dokument relevant fur die Beantwortung der Frage? Antworte mit 'ja' oder 'nein'."""

# CRAG query rewriting
REWRITE_SYSTEM_PROMPT = """Du bist ein Suchoptimierungsassistent fur deutsche Rechtstexte. Formuliere die Suchanfrage um, damit bessere Ergebnisse gefunden werden. Gib NUR die umformulierte Anfrage aus, ohne Erklarung."""

REWRITE_USER_TEMPLATE = """Ursprungliche Anfrage: {query}

Formuliere diese Anfrage um, um bessere Suchergebnisse in einer juristischen Datenbank zu erhalten. Fokussiere auf Schlusselbegriffe und Rechtsterminologie."""

# Self-RAG faithfulness check
FAITHFULNESS_SYSTEM_PROMPT = """Prufe, ob die Antwort ausschliesslich auf den bereitgestellten Quellen basiert. Antworte mit 'ja' wenn alle Aussagen durch die Quellen belegt sind, oder 'nein' wenn unbelegte Behauptungen enthalten sind."""

FAITHFULNESS_USER_TEMPLATE = """Quellen:
{sources}

Antwort:
{answer}

Basiert die Antwort ausschliesslich auf den Quellen? Antworte mit 'ja' oder 'nein'."""

# Self-RAG relevance check
RELEVANCE_SYSTEM_PROMPT = """Prufe, ob die Antwort die gestellte Frage beantwortet. Antworte mit 'ja' wenn die Frage beantwortet wird, oder 'nein' wenn die Antwort am Thema vorbeigeht."""

RELEVANCE_USER_TEMPLATE = """Frage: {query}

Antwort:
{answer}

Beantwortet die Antwort die Frage? Antworte mit 'ja' oder 'nein'."""

# Query decomposition
DECOMPOSE_SYSTEM_PROMPT = """Du bist ein juristischer Analyseassistent. Zerlege die komplexe Rechtsfrage in 2-3 einfache, unabhangige Teilfragen. Gib jede Teilfrage auf einer eigenen Zeile aus, nummeriert mit 1., 2., 3."""

DECOMPOSE_USER_TEMPLATE = """Komplexe Frage: {query}

Zerlege diese Frage in 2-3 einfache Teilfragen:"""

# Map intent to system prompt
INTENT_TO_PROMPT: dict[str, str] = {
    QueryIntent.CURRENT_LAW: SYSTEM_PROMPT_LEGAL_RAG,
    QueryIntent.HISTORICAL_LAW: SYSTEM_PROMPT_LEGAL_RAG,
    QueryIntent.COURT_RULING: SYSTEM_PROMPT_COURT_RULING,
    QueryIntent.LAW_COMPARISON: SYSTEM_PROMPT_COMPARISON,
    QueryIntent.GENERAL_INFO: SYSTEM_PROMPT_LEGAL_RAG,
    QueryIntent.DEFINITION: SYSTEM_PROMPT_DEFINITION,
    QueryIntent.PROCEDURE: SYSTEM_PROMPT_PROCEDURE,
    QueryIntent.AMBIGUOUS: SYSTEM_PROMPT_LEGAL_RAG,
}

# Refusal messages (German)
REFUSAL_INSUFFICIENT_CONTEXT = (
    "Diese Information ist nicht im verfugbaren Kontext enthalten. "
    "Bitte formulieren Sie Ihre Anfrage spezifischer oder geben Sie "
    "zusatzliche Details an."
)

REFUSAL_LOW_CONFIDENCE = (
    "Die gefundenen Informationen sind nicht ausreichend zuverlassig, "
    "um eine fundierte Antwort zu geben. Bitte konsultieren Sie "
    "die Originalquellen oder einen Rechtsanwalt."
)

REFUSAL_UNVERIFIED_CITATION = (
    "Die Antwort konnte nicht verifiziert werden, da nicht alle Zitate im Kontext gefunden wurden."
)

# Trusted legal domains for web search fallback
LEGAL_DOMAINS = [
    "gesetze-im-internet.de",
    "dejure.org",
    "juris.de",
    "bundesgerichtshof.de",
    "bundesverfassungsgericht.de",
    "bverwg.de",
    "beck-online.de",
    "bundesanzeiger.de",
    "recht.bund.de",
]
