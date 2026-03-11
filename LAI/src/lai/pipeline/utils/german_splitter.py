"""German legal sentence splitter with abbreviation awareness."""

import re
from typing import List, Tuple

from lai.core.logging import get_logger

logger = get_logger("lai.pipeline.utils.german_splitter")

# Abbreviations that end with a period but are NOT sentence endings
_ABBREVS = {
    # Legal references
    "abs", "art", "nr", "s", "rn", "rz", "hs", "var", "lit", "ziff",
    "anh", "anl", "bd", "begr", "erl",
    # Common legal
    "vgl", "bzw", "bzgl", "ggf", "gem", "sog", "u.a", "z.b", "d.h",
    "i.v.m", "i.s.d", "i.s.v", "a.a.o", "a.f", "n.f", "m.w.n",
    "m.e", "h.m", "h.l", "a.a", "e.v", "o.g", "u.u", "i.d.r",
    "i.e.s", "i.w.s", "a.e", "z.t", "u.ä",
    # Courts
    "bgh", "bverwg", "bverfg", "bag", "bsg", "bfh", "olg", "lg",
    "ag", "vg", "ovg", "fg", "lsg", "arbg", "lag", "sg",
    # Legal codes
    "bimschg", "baugb", "bnatschg", "uvpg", "baunutzvo", "roeiv",
    "bverfgg", "vwgo", "vwvfg", "zpo", "stpo", "bgb", "stgb",
    "hgb", "gmbhg", "aktg", "gwb", "eeg", "enwg", "bimschv",
    "ta", "windseeg", "lwg", "whg", "fig",
    # Titles & misc
    "dr", "prof", "dipl", "ing", "mr", "mrs", "jr", "sr",
    "ca", "etc", "st", "inkl", "excl", "max", "min", "zzgl",
    "abschn", "kap", "aufl", "hrsg", "verl",
    # Months
    "jan", "feb", "mär", "apr", "mai", "jun", "jul", "aug",
    "sep", "okt", "nov", "dez",
}

_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in sorted(_ABBREVS, key=len, reverse=True)) + r")\.\s",
    re.IGNORECASE,
)

# Legal section boundary patterns
SECTION_PATTERNS = [
    re.compile(r"^§§?\s*\d+", re.MULTILINE),
    re.compile(r"^(?:Art(?:ikel)?\.?\s*\d+)", re.MULTILINE),
    re.compile(r"^(?:Abschnitt|Kapitel|Teil|Unterabschnitt)\s+[IVX\d]+", re.MULTILINE),
    re.compile(r"^(?:Tenor|Tatbestand|Entscheidungsgründe|Gründe|Leitsatz|Leitsätze)\s*:?\s*$", re.MULTILINE),
]


def split_sentences(text: str) -> List[str]:
    """Split German legal text into sentences, respecting abbreviations."""
    if not text:
        return []

    placeholder = "\x01"
    chars = list(text)

    for match in reversed(list(_ABBREV_PATTERN.finditer(text))):
        dot_pos = text.rfind(".", match.start(), match.end())
        if dot_pos >= 0:
            chars[dot_pos] = placeholder

    protected = "".join(chars)
    protected = re.sub(r"(\d)\.(\d)", rf"\1{placeholder}\2", protected)

    raw = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ(§\[])", protected)
    sentences = [s.replace(placeholder, ".").strip() for s in raw]
    return [s for s in sentences if s]


def find_section_boundaries(text: str) -> List[Tuple[int, str]]:
    """Find legal section boundaries. Returns (char_position, title) tuples."""
    boundaries = []
    for pattern in SECTION_PATTERNS:
        for match in pattern.finditer(text):
            boundaries.append((match.start(), match.group().strip()))
    boundaries.sort(key=lambda x: x[0])
    if boundaries:
        logger.debug(f"Found {len(boundaries)} section boundaries in {len(text)} chars")
    return boundaries
