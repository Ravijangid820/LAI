"""German-legal-aware sentence splitter.

Ported from :mod:`lai.pipeline.utils.german_splitter` so :mod:`lai.common`
remains a leaf package (no upward dependency on :mod:`lai.pipeline`).
The implementation is a faithful copy with two improvements:

1. The abbreviation list is exposed as a :class:`frozenset` so callers
   can introspect / extend it through composition without monkey-patching.
2. The numeric-period protection extended to a slightly wider character
   range (``\\d\\.\\d`` continues to be protected) and is documented as a
   separate post-processing step rather than inlined into the abbreviation
   regex.

The algorithm
-------------

1. Find every occurrence of ``<abbrev>.<space>`` in the text and replace
   the period with a placeholder (``\\x01``). This stops the sentence
   regex from splitting after the abbreviation.
2. Apply the same trick to ``\\d.\\d`` (e.g., the ``5.`` in ``§ 35 Abs. 5``).
3. Split on sentence-final punctuation followed by whitespace followed
   by an upper-case letter, an opening bracket, ``§``, or ``[``.
4. Restore the placeholders to literal periods and trim each sentence.

The result is a list of non-empty, ``.strip()``-ed sentences.
"""

from __future__ import annotations

import re

__all__ = [
    "ABBREVIATIONS",
    "SECTION_PATTERNS",
    "find_section_boundaries",
    "split_sentences",
]


# Abbreviations that end with a period but are *not* sentence terminators.
# Verbatim from :mod:`lai.pipeline.utils.german_splitter` with the
# additions kept in a single source of truth for ``lai.common`` callers.
ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # Legal references
        "abs",
        "art",
        "nr",
        "s",
        "rn",
        "rz",
        "hs",
        "var",
        "lit",
        "ziff",
        "anh",
        "anl",
        "bd",
        "begr",
        "erl",
        # Common legal
        "vgl",
        "bzw",
        "bzgl",
        "ggf",
        "gem",
        "sog",
        "u.a",
        "z.b",
        "d.h",
        "i.v.m",
        "i.s.d",
        "i.s.v",
        "a.a.o",
        "a.f",
        "n.f",
        "m.w.n",
        "m.e",
        "h.m",
        "h.l",
        "a.a",
        "e.v",
        "o.g",
        "u.u",
        "i.d.r",
        "i.e.s",
        "i.w.s",
        "a.e",
        "z.t",
        "u.ä",
        # Courts
        "bgh",
        "bverwg",
        "bverfg",
        "bag",
        "bsg",
        "bfh",
        "olg",
        "lg",
        "ag",
        "vg",
        "ovg",
        "fg",
        "lsg",
        "arbg",
        "lag",
        "sg",
        # Legal codes
        "bimschg",
        "baugb",
        "bnatschg",
        "uvpg",
        "baunutzvo",
        "roeiv",
        "bverfgg",
        "vwgo",
        "vwvfg",
        "zpo",
        "stpo",
        "bgb",
        "stgb",
        "hgb",
        "gmbhg",
        "aktg",
        "gwb",
        "eeg",
        "enwg",
        "bimschv",
        "ta",
        "windseeg",
        "lwg",
        "whg",
        "fig",
        # Titles & misc
        "dr",
        "prof",
        "dipl",
        "ing",
        "mr",
        "mrs",
        "jr",
        "sr",
        "ca",
        "etc",
        "st",
        "inkl",
        "excl",
        "max",
        "min",
        "zzgl",
        "abschn",
        "kap",
        "aufl",
        "hrsg",
        "verl",
        # Months
        "jan",
        "feb",
        "mär",
        "apr",
        "mai",
        "jun",
        "jul",
        "aug",
        "sep",
        "okt",
        "nov",
        "dez",
    },
)


_PLACEHOLDER = "\x01"


# Abbreviation regex, sorted long-first so multi-token entries like
# ``i.v.m`` match before any single-token prefix.
_ABBREV_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in sorted(ABBREVIATIONS, key=len, reverse=True)) + r")\.\s",
    re.IGNORECASE,
)


# Legal section / structural boundary patterns (line-anchored on
# multi-line input).
SECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^§§?\s*\d+", re.MULTILINE),
    re.compile(r"^(?:Art(?:ikel)?\.?\s*\d+)", re.MULTILINE),
    re.compile(r"^(?:Abschnitt|Kapitel|Teil|Unterabschnitt)\s+[IVX\d]+", re.MULTILINE),
    re.compile(
        r"^(?:Tenor|Tatbestand|Entscheidungsgründe|Gründe|Leitsatz|Leitsätze)\s*:?\s*$",
        re.MULTILINE,
    ),
)


def split_sentences(text: str) -> list[str]:
    """Split German legal text into sentences, respecting abbreviations.

    Args:
        text: Source text. May be empty.

    Returns:
        A list of trimmed, non-empty sentences. Preserves the original
        order. The output is *not* guaranteed to round-trip via
        ``" ".join(...)`` because adjacent whitespace is normalised.
    """
    if not text:
        return []

    # Build a mutable char list and replace the trailing period of every
    # matched abbreviation with the placeholder. Iterating in reverse
    # keeps earlier indices stable while we rewrite later ones.
    chars = list(text)
    for match in reversed(list(_ABBREV_PATTERN.finditer(text))):
        dot_pos = text.rfind(".", match.start(), match.end())
        if dot_pos >= 0:
            chars[dot_pos] = _PLACEHOLDER

    protected = "".join(chars)
    # Numeric periods (e.g., "5." inside "§ 35 Abs. 5.") also confuse
    # the sentence regex; protect them with the same placeholder.
    protected = re.sub(r"(\d)\.(\d)", rf"\1{_PLACEHOLDER}\2", protected)

    raw_parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ(§\[])", protected)
    sentences = [p.replace(_PLACEHOLDER, ".").strip() for p in raw_parts]
    return [s for s in sentences if s]


def find_section_boundaries(text: str) -> list[tuple[int, str]]:
    """Return sorted ``(offset, marker)`` tuples for structural boundaries.

    Useful when the chunker wants to prefer breaking at section starts
    (``§ 35``, ``Artikel 12``, ``Abschnitt II``, etc.) rather than at an
    arbitrary sentence boundary.

    Args:
        text: Source text. May be empty.

    Returns:
        Tuples of ``(char_offset, matched_marker)`` sorted ascending by
        offset. Duplicate offsets are possible if two patterns match at
        the same position; both are preserved in deterministic order
        (pattern declaration order).
    """
    boundaries: list[tuple[int, str]] = []
    for pattern in SECTION_PATTERNS:
        for match in pattern.finditer(text):
            boundaries.append((match.start(), match.group().strip()))
    boundaries.sort(key=lambda x: x[0])
    return boundaries
