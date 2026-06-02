"""Parent-text language classifier for val.jsonl quality filtering.

Tuned for the corpus of paragraph-length German legal text (≥100 chars
of body), where the failure modes the spot-check found are:

* **Danish**: auditor's conclusions, "Udtalelse om ledelsesberetningen",
  Resultatopgørelse tables.
* **English**: Document Request Lists, English summaries of German
  permits.
* **Pure tabular metadata**: MaStR rows, balance-sheet numbers — no
  function words, no umlauts.

Conservative by design: prefer ``"unknown"`` to a bad guess. Tests at
``tests/unit/scripts/test_filter_val_german.py`` cover each failure
mode against real spot-check rows.
"""

from __future__ import annotations

import re

__all__ = ["classify_text"]


# ── Language signals ────────────────────────────────────────────────────


_GERMAN_HINT_WORDS = frozenset(
    {
        "der", "die", "das", "den", "dem", "des",
        "und", "oder", "auch", "nicht",
        "ist", "sind", "war", "waren", "wird", "werden", "wurde",
        "ein", "eine", "einen", "einem", "einer", "eines",
        "für", "von", "mit", "auf", "bei", "nach", "über", "unter",
        "sich", "kann", "soll", "muss", "darf", "möchte",
        "im", "am", "vom", "zur", "zum",
        "vertrag", "vertrages", "vertraglich",
        "gesetz", "gesetze", "gesetzlich",
        "genehmigung", "anlage", "anlagen",
    }
)

# Danish letters that almost never appear in German legal text.
_DANISH_LETTERS = re.compile(r"[øåæ]")

# Danish-specific tokens — high-confidence reject signal even alongside
# German-looking text (mixed-language docs exist; we want monolingual).
_DANISH_TOKENS = frozenset(
    {
        "vi", "har", "er", "det", "om", "som", "vores", "vor",
        "ikke", "også", "men", "kan", "skal", "bliver",
        "konklusion", "udført", "udvidet", "gennemgang",
        "årsregnskabet", "regnskabsåret",
        "udtalelse", "ledelsesberetningen", "ledelsen", "ansvarlig",
        "resultatopgørelse", "resultatdisponering",
        "indregning", "balancen", "aktiver", "forpligtelser",
        "kapitalandele", "ejerandel", "kostpris",
    }
)

# English tokens — same rationale; if many appear in legal-section text,
# the gold is an English DRL or summary, not a German legal text.
_ENGLISH_TOKENS = frozenset(
    {
        "the", "and", "of", "in", "to", "is", "are", "was", "were",
        "this", "that", "these", "those",
        "permit", "permits", "license", "licenses", "wind", "farm",
        "agreement", "contract", "consent", "consents",
        "please", "provide", "request", "comment",
        "company", "operation", "operations",
    }
)


def classify_text(text: str) -> str:
    """Return ``"de"`` / ``"non_de"`` / ``"unknown"`` for ``text``."""
    if not text or len(text) < 100:
        return "unknown"
    lower = text.lower()
    toks = re.findall(r"[a-zäöüßøåæ]+", lower)
    if len(toks) < 20:
        return "unknown"

    # Hard reject on Danish letters present in any meaningful density.
    if len(_DANISH_LETTERS.findall(lower)) >= 3:
        return "non_de"

    if sum(1 for t in toks if t in _DANISH_TOKENS) >= 3:
        return "non_de"

    english_hits = sum(1 for t in toks if t in _ENGLISH_TOKENS)
    german_hits = sum(1 for t in toks if t in _GERMAN_HINT_WORDS)
    umlauts = bool(re.search(r"[äöüß]", lower))

    # English-dominated text: more English tokens than German, AND
    # no umlauts. The umlaut check rescues mixed German-with-English-
    # quotes legal commentary.
    if english_hits > german_hits and not umlauts:
        return "non_de"

    # German-positive: needs hint words OR umlauts, plus enough length.
    if german_hits >= 3 or (umlauts and german_hits >= 1):
        return "de"

    return "unknown"
