"""German Bundesland keyword table, bbox table, and chat-time validator.

See :mod:`lai.common.jurisdiction.__init__` for the rationale.

The keyword table and bboxes were originally in
``micro-services/ddiq_report.py``; moving them here lets the chat
backend (which never had access to the DDiQ module) use the same
authoritative table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "BUNDESLAND_BBOX",
    "BUNDESLAND_KEYWORDS",
    "BUNDESLAND_SPECIFIC_RULES",
    "GERMANY_BBOX",
    "JurisdictionWarning",
    "check_jurisdiction",
    "detect_bundesland",
    "point_in_bbox",
]


# ── Bundesland keyword table ────────────────────────────────────────────────
#
# Lowercase keys, lowercase string values. The keyword list per state is
# enriched with major cities and a handful of Landkreis hints that come
# up in DD documents (Cuxhaven for Niedersachsen etc.). Match is
# case-insensitive substring over the input text.

BUNDESLAND_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "niedersachsen": (
        "niedersachsen",
        "hannover",
        "braunschweig",
        "oldenburg",
        "osnabrück",
        "lüneburg",
        "göttingen",
        "wolfsburg",
        "cuxhaven",
        "hude",
        "hatten",
        "lamstedt",
    ),
    "nordrhein-westfalen": (
        "nordrhein-westfalen",
        "nrw",
        "düsseldorf",
        "köln",
        "münster",
        "detmold",
        "arnsberg",
        "dortmund",
        "essen",
    ),
    "schleswig-holstein": (
        "schleswig-holstein",
        "kiel",
        "lübeck",
        "flensburg",
        "husum",
        "dithmarschen",
    ),
    "brandenburg": (
        "brandenburg",
        "potsdam",
        "cottbus",
        "uckermark",
        "prignitz",
    ),
    "mecklenburg-vorpommern": (
        "mecklenburg",
        "vorpommern",
        "rostock",
        "schwerin",
        "stralsund",
        "rügen",
    ),
    "sachsen-anhalt": (
        "sachsen-anhalt",
        "magdeburg",
        "halle",
        "dessau",
        "stendal",
        "altmark",
    ),
    "bayern": (
        "bayern",
        "bavaria",
        "münchen",
        "nürnberg",
        "augsburg",
    ),
    "hessen": (
        "hessen",
        "wiesbaden",
        "frankfurt",
        "kassel",
        "darmstadt",
    ),
    "thüringen": (
        "thüringen",
        "erfurt",
        "jena",
        "weimar",
    ),
    "sachsen": (
        "sachsen",
        "dresden",
        "leipzig",
        "chemnitz",
    ),
    "rheinland-pfalz": (
        "rheinland-pfalz",
        "mainz",
        "koblenz",
        "trier",
    ),
    "baden-württemberg": (
        "baden-württemberg",
        "stuttgart",
        "karlsruhe",
        "freiburg",
    ),
    "saarland": (
        "saarland",
        "saarbrücken",
    ),
    "bremen": (
        "bremen",
        "bremerhaven",
    ),
    "hamburg": ("hamburg",),
    "berlin": ("berlin",),
}


# ── Bundesland bounding boxes ───────────────────────────────────────────────
#
# (lat_min, lat_max, lng_min, lng_max). Approximate but conservative —
# each box covers the Bundesland with comfortable padding so a real
# in-Bundesland point won't be rejected by GPS / rounding noise.

BUNDESLAND_BBOX: Final[dict[str, tuple[float, float, float, float]]] = {
    "baden-württemberg": (47.53, 49.79, 7.51, 10.50),
    "bayern": (47.27, 50.56, 8.98, 13.84),
    "berlin": (52.34, 52.68, 13.09, 13.76),
    "brandenburg": (51.36, 53.56, 11.27, 14.77),
    "bremen": (53.01, 53.61, 8.48, 8.99),
    "hamburg": (53.39, 53.74, 8.42, 10.33),
    "hessen": (49.39, 51.66, 7.77, 10.24),
    "mecklenburg-vorpommern": (53.11, 54.69, 10.59, 14.41),
    "niedersachsen": (51.30, 53.89, 6.65, 11.60),
    "nordrhein-westfalen": (50.32, 52.53, 5.86, 9.46),
    "rheinland-pfalz": (48.97, 50.94, 6.11, 8.51),
    "saarland": (49.11, 49.64, 6.36, 7.40),
    "sachsen": (50.17, 51.69, 11.87, 15.04),
    "sachsen-anhalt": (50.94, 53.04, 10.56, 13.19),
    "schleswig-holstein": (53.36, 55.06, 7.86, 11.31),
    "thüringen": (50.20, 51.65, 9.87, 12.65),
}


# Germany-wide bbox — union of all 16 Bundesländer with a small pad.
# Used as a fallback hard reject when the caller did not supply an
# expected_bundesland: a geocoded result outside this is definitely
# wrong (Nominatim hit France / Austria / a same-named US town).
GERMANY_BBOX: Final[tuple[float, float, float, float]] = (47.27, 55.06, 5.86, 15.04)


def point_in_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    """Return True iff ``(lat, lng)`` is inside the bbox.

    Args:
        lat: Latitude, degrees north (positive for Germany).
        lng: Longitude, degrees east (positive for Germany).
        bbox: ``(lat_min, lat_max, lng_min, lng_max)``.
    """
    lat_min, lat_max, lng_min, lng_max = bbox
    return lat_min <= lat <= lat_max and lng_min <= lng <= lng_max


def detect_bundesland(text: str) -> str | None:
    """Heuristically identify the Bundesland mentioned in ``text``.

    Lowercase-substring match against :data:`BUNDESLAND_KEYWORDS`.
    Returns the canonical lowercase Bundesland key (e.g. ``"bayern"``)
    or ``None`` when no keyword matches. First match wins — the keyword
    list is ordered so the most state-specific tokens
    (``"niedersachsen"`` itself, major Landkreis names) come first.

    The match is intentionally loose. Two-word names ("Sachsen-Anhalt")
    are matched as substrings, so a text containing both "Sachsen" and
    "Sachsen-Anhalt" will currently match whichever appears first in
    the iteration order. For the chat validator's purposes (warn if
    Bayern-specific rules are cited for a non-Bayern matter) this is
    sufficient — the caller usually knows the Bundesland from the
    matter context anyway.
    """
    if not text:
        return None
    loc = text.lower()
    for state, keywords in BUNDESLAND_KEYWORDS.items():
        if any(kw in loc for kw in keywords):
            return state
    return None


# ── Bundesland-specific rules and their detection ───────────────────────────
#
# Each entry:
#   * ``key``: canonical Bundesland key the rule belongs to. When an
#     answer mentions one of the regexes BELOW but the matter is in a
#     DIFFERENT Bundesland, that's a credibility error.
#   * ``label``: human-readable rule name.
#   * ``patterns``: compiled regex list. ``re.IGNORECASE`` already set;
#     callers don't need to handle case.
#
# The dominant case is Bayern's 10H rule (Art. 82 BayBO). Citing 10H
# for a Niedersachsen project was the lawyer's #2 complaint at v0.
# Adding more rules is mechanical — each one prevents a recurring
# false-positive citation.

BUNDESLAND_SPECIFIC_RULES: Final[list[dict]] = [
    {
        "key": "bayern",
        "label": "Bayerns 10H-Regel (Art. 82 BayBO)",
        "patterns": (
            # "10H" / "10 H" / "10xH" / "10×H" / "10x H". The
            # multiplier symbol is optional — Qwen3 and most legal
            # writing use plain "10H" without the explicit ``×``.
            # ``\b`` at both ends so "10Hz" / "0H10" etc. don't match.
            re.compile(r"\b10\s*[xX×]?\s*H\b"),
            re.compile(r"\b10H[\s-]*Reg(el|elung)\b", re.IGNORECASE),
            re.compile(r"\bBayBO\b"),
            re.compile(r"\bArt\.?\s*82\s+BayBO\b", re.IGNORECASE),
            re.compile(r"\bArt\.?\s*82a\s+BayBO\b", re.IGNORECASE),
        ),
    },
    {
        "key": "niedersachsen",
        "label": "Niedersächsische Bauordnung (NBauO)",
        "patterns": (re.compile(r"\bNBauO\b"),),
    },
    {
        "key": "schleswig-holstein",
        "label": "Landesbauordnung Schleswig-Holstein (LBO SH)",
        "patterns": (re.compile(r"\bLBO\s*SH\b", re.IGNORECASE),),
    },
    {
        "key": "nordrhein-westfalen",
        "label": "Bauordnung Nordrhein-Westfalen (BauO NRW)",
        "patterns": (re.compile(r"\bBauO\s*NRW\b", re.IGNORECASE),),
    },
]


@dataclass(frozen=True, slots=True)
class JurisdictionWarning:
    """One jurisdictional inconsistency found in an LLM answer.

    Attributes:
        rule_label: Human-readable rule name (e.g. *"Bayerns 10H-Regel"*).
        rule_bundesland: Which Bundesland the rule belongs to.
        expected_bundesland: Which Bundesland the matter is actually in.
        excerpt: The matching substring from the answer, trimmed to ~80
            chars so the UI can render it next to the warning chip.
    """

    rule_label: str
    rule_bundesland: str
    expected_bundesland: str
    excerpt: str


def check_jurisdiction(
    answer: str,
    expected_bundesland: str | None,
    *,
    max_excerpt_chars: int = 80,
) -> list[JurisdictionWarning]:
    """Find Bundesland-specific legal rules cited in ``answer`` that
    don't belong to ``expected_bundesland``.

    Args:
        answer: The LLM's response text. May be empty.
        expected_bundesland: The Bundesland the matter is in, in the
            same lowercase form :func:`detect_bundesland` returns
            (``"bayern"``, ``"niedersachsen"``, ...). ``None`` disables
            the check — used when the matter has no detected
            Bundesland yet (e.g. brand-new chat).
        max_excerpt_chars: Maximum characters of context to attach to
            each warning. Default 80 — fits in a UI chip subtitle.

    Returns:
        A list of :class:`JurisdictionWarning`. Empty when either
        ``expected_bundesland`` is unknown OR no Bundesland-specific
        rule from a DIFFERENT state appears in the answer.

    Notes:
        * Multiple matches of the same rule produce ONE warning (the
          first occurrence's excerpt).
        * Matching is regex-based on the rule's own patterns; case
          handling is already baked into each rule.
    """
    if not answer or not expected_bundesland:
        return []
    expected = expected_bundesland.lower()
    warnings: list[JurisdictionWarning] = []
    seen_rules: set[str] = set()
    for rule in BUNDESLAND_SPECIFIC_RULES:
        if rule["key"] == expected:
            continue  # rule belongs to this matter's state — not an error
        if rule["label"] in seen_rules:
            continue
        for pattern in rule["patterns"]:
            match = pattern.search(answer)
            if match is None:
                continue
            start = max(0, match.start() - max_excerpt_chars // 2)
            end = min(len(answer), match.end() + max_excerpt_chars // 2)
            excerpt = answer[start:end].strip()
            warnings.append(
                JurisdictionWarning(
                    rule_label=str(rule["label"]),
                    rule_bundesland=str(rule["key"]),
                    expected_bundesland=expected,
                    excerpt=excerpt,
                )
            )
            seen_rules.add(str(rule["label"]))
            break  # one warning per rule
    return warnings
