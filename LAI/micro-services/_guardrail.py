"""Output validation / guardrail layer for DDiQ.

Track A item 5. Post-generation cleanup pass that runs over each
section's rows + each finding *after* the LLM has produced them, but
*before* they are persisted to ``ddiq_reports.report_data`` and shipped
to the UI.

What it fixes (every rule is sourced from observed output in real
``ddiq_reports`` rows on 2026-05-17; no invented patterns):

1. **Defensive "the supplied context does not contain …" paragraphs.**
   The LLM, when asked to extract a fact it doesn't have, sometimes
   writes a four-line apology in the section's ``value`` cell. The
   real-world examples were:

   * DE: ``"Die vorliegenden Kontextausschnitte enthalten keine
     Informationen zu Pachtverträgen ..."``
   * DE: ``"Der vorliegende Kontext enthält keine Angaben zu ..."``
   * DE: ``"Der vorliegende Kontext enthält ausschließlich technische
     Spezifikationen ..."``
   * EN: ``"Based on the supplied context, which consists solely of
     technical specifications ..."``
   * EN: ``"The supplied context is limited to technical data sheets ..."``
   * EN: ``"Unable to verify ..."``

   The guardrail replaces such ``value`` cells with a canonical, short
   "not contained in supplied documents" marker AND escalates the
   row's ``ampel`` to ``yellow`` (a defensive paragraph dressed as
   green is a credibility kill). The original LLM output is preserved
   on the row via a new ``original_value`` attribute so debugging /
   audit can still see what the model said.

2. **"Manual review required (findings extraction failed)" stragglers.**
   Item 2 of Track A (per-finding iteration) retires this for new
   reports; this guardrail catches any leftover that slipped through
   (e.g. a finding emitted by some other path with the same shape).
   Replaced with the same structured "missing" marker.

3. **Hedge phrases.** The classic legal-text hedge set, both DE and
   EN. ``"möglicherweise"``, ``"vermutlich"``, ``"ggf."``, ``"perhaps"``,
   ``"appears to"``, etc. Stripped in-place (replaced with the empty
   string or the bare verb where the grammar requires it). Kept
   narrow — only hedges that materially affect a lawyer's reading.

4. **Mixed-language sections.** A German section with one row in
   English (or vice-versa) is symptomatic of a half-completed
   extraction. The guardrail does NOT rewrite the offending row —
   that would need a per-row LLM call which we deferred in the
   "don't overcomplex" scope decision. Instead, it appends a
   one-line marker to the row's ``note`` field so reviewers can
   spot the mismatch in the UI.

Pure-function design — no I/O, no LLM calls, no DB writes. Caller
applies the result back to the report object.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "MISSING_VALUE_DE",
    "MISSING_VALUE_EN",
    "ScrubReport",
    "detect_defensive_ai",
    "detect_language",
    "scrub_finding_text",
    "scrub_row_value",
    "strip_hedges",
]

Language = Literal["de", "en", "mixed", "unknown"]

# Evidence-chunk indices the analyzer LLM is told to cite (``[#1]``, ``[#3]``,
# ``[#1, 3]``). They belong in the structured ``evidence_chunks`` array, not in
# the prose a partner reads — strip them from rendered values / notes / finding
# text. (See ddiq/rag.py for where the ``[#n]`` numbering is introduced.)
_CHUNK_REF_RE = re.compile(r"\s*\[#\s*\d+(?:\s*,\s*\d+)*\s*\]")

# Canonical replacement strings for defensive-AI paragraphs. Short, clear,
# language-matched so the rest of the report stays consistent.
MISSING_VALUE_DE = "Nicht in den vorgelegten Dokumenten enthalten."
MISSING_VALUE_EN = "Not contained in the supplied documents."

# ── Defensive-AI patterns ────────────────────────────────────────────────
# Each pattern is anchored to observed real output. The set is tiny on
# purpose: every entry is something that has actually appeared in a
# production ddiq_reports row, and the patterns are written to match the
# *opener* of the apology (the part that survives whatever ad-hoc tail
# the model improvises). False positives here would hide real content,
# so caution > recall.
_DEFENSIVE_PATTERNS_DE = (
    re.compile(r"^\s*Die vorliegenden Kontext(ausschnitte|auszüge)?\s+enthalten\s+(keine|ausschließlich)", re.IGNORECASE),
    re.compile(r"^\s*Der vorliegende Kontext\s+enthält\s+(keine|ausschließlich|nur)", re.IGNORECASE),
    re.compile(r"^\s*Im (vorliegenden|gegebenen) Kontext\s+(finden sich|sind|werden)\s+keine", re.IGNORECASE),
    re.compile(r"^\s*Als (KI|künstliche Intelligenz)", re.IGNORECASE),
)
_DEFENSIVE_PATTERNS_EN = (
    re.compile(r"^\s*Based on the supplied context", re.IGNORECASE),
    re.compile(r"^\s*The supplied context (is|consists|contains)", re.IGNORECASE),
    re.compile(r"^\s*(Unable|I am unable|I cannot|I'm unable) to", re.IGNORECASE),
    re.compile(r"^\s*As an AI", re.IGNORECASE),
)

# The exact "extraction failed" string from the legacy generate_findings
# fallback — Track A item 2 retired the source but a guardrail is cheap
# insurance.
_EXTRACTION_FAILED_PATTERN = re.compile(
    r"manual review required.*?\(findings? extraction failed\)",
    re.IGNORECASE,
)

# ── Hedge patterns ───────────────────────────────────────────────────────
# Surgically removed (replaced with empty string). Each pattern is bounded
# by ``\b`` word boundaries to avoid eating into adjacent words.
_HEDGE_PATTERNS_DE = (
    re.compile(r"\bmöglicherweise\b\s*", re.IGNORECASE),
    re.compile(r"\bvermutlich\b\s*", re.IGNORECASE),
    re.compile(r"\bggf\.\s*", re.IGNORECASE),
    re.compile(r"\bgegebenenfalls\b\s*", re.IGNORECASE),
    re.compile(r"\bunter Umständen\b\s*", re.IGNORECASE),
    re.compile(r"\beventuell\b\s*", re.IGNORECASE),
    re.compile(r"\bes scheint, dass\b\s*", re.IGNORECASE),
)
_HEDGE_PATTERNS_EN = (
    re.compile(r"\bperhaps\b\s*", re.IGNORECASE),
    re.compile(r"\bpossibly\b\s*", re.IGNORECASE),
    re.compile(r"\bmight\b\s*", re.IGNORECASE),
    re.compile(r"\bcould potentially\b\s*", re.IGNORECASE),
    re.compile(r"\bappears to\b\s*", re.IGNORECASE),
    re.compile(r"\bseems to\b\s*", re.IGNORECASE),
)

# ── Language detection ───────────────────────────────────────────────────
# Heuristic: count distinctive German chars + common stopwords. Cheap,
# good enough for "is this row a different language from the rest of the
# section" — not a substitute for a real language detector at the section
# level (which would need a library like ``langdetect`` we don't want to
# add for v1).
_GERMAN_CHARS = re.compile(r"[äöüÄÖÜß]")
_GERMAN_STOPWORDS = frozenset({
    "der", "die", "das", "und", "ist", "von", "den", "des", "im", "ein",
    "eine", "auf", "mit", "für", "nach", "bei", "aus", "zu", "zur", "zum",
    "werden", "wird", "können", "wurden", "sind", "wurde", "soll", "muss",
    "nicht", "kein", "keine", "vor", "während",
})
_ENGLISH_STOPWORDS = frozenset({
    "the", "and", "is", "of", "in", "to", "for", "with", "on", "by",
    "from", "an", "are", "was", "were", "as", "this", "that", "be",
    "not", "no", "any", "all", "must", "shall", "may", "can",
})


# ────────────────────────────────────────────────────────────────────────
# Public helpers
# ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScrubReport:
    """Summary of what a single scrub call changed.

    Attributes:
        original: The text as it came out of the LLM. ``""`` when the
            input was already empty.
        cleaned: The text after the guardrail rules ran. Equal to
            ``original`` when no rule fired.
        was_defensive: True iff a defensive-AI pattern matched and the
            value was replaced with the canonical missing marker.
        hedges_stripped: Count of hedge phrases removed.
        language: Detected language of ``original``. Useful for the
            caller to decide whether to attach a mixed-language note.
    """

    original: str
    cleaned: str
    was_defensive: bool
    hedges_stripped: int
    language: Language


def detect_defensive_ai(text: str) -> bool:
    """Return True if ``text`` opens with a known defensive-AI pattern.

    Conservative — only fires on the opener (first ~80 chars). A
    legitimate clause that happens to contain the phrase "Based on the
    supplied context" mid-sentence is left alone.
    """
    if not text or not text.strip():
        return False
    head = text[:200]  # bounded so a multi-paragraph clause stays cheap
    if _EXTRACTION_FAILED_PATTERN.search(head):
        return True
    return any(p.match(head) for p in _DEFENSIVE_PATTERNS_DE) or any(
        p.match(head) for p in _DEFENSIVE_PATTERNS_EN
    )


def strip_hedges(text: str, language: Language) -> tuple[str, int]:
    """Strip hedge phrases from ``text``. Returns ``(cleaned, count)``.

    ``language`` selects the rule set; ``"mixed"`` runs both. Unknown
    language: no-op (don't risk eating into language we can't tag).
    """
    if not text:
        return text, 0
    rules: tuple[re.Pattern[str], ...]
    if language == "de":
        rules = _HEDGE_PATTERNS_DE
    elif language == "en":
        rules = _HEDGE_PATTERNS_EN
    elif language == "mixed":
        rules = _HEDGE_PATTERNS_DE + _HEDGE_PATTERNS_EN
    else:
        return text, 0
    cleaned = text
    count = 0
    for rule in rules:
        new, n = rule.subn("", cleaned)
        if n:
            cleaned = new
            count += n
    # Collapse the double-spaces a removed hedge often leaves behind.
    if count:
        cleaned = re.sub(r"  +", " ", cleaned).strip()
    return cleaned, count


def detect_language(text: str) -> Language:
    """Heuristic language tag for ``text``: ``"de" | "en" | "mixed" | "unknown"``.

    Method: score German vs English signal from (a) presence of
    distinctive German diacritics and (b) stopword overlap. Both
    languages crossing a threshold returns ``"mixed"``; neither crossing
    returns ``"unknown"`` (very short / non-prose / pure-numeric text).
    """
    if not text or not text.strip():
        return "unknown"
    sample = text.lower()
    has_german_chars = bool(_GERMAN_CHARS.search(text))
    tokens = re.findall(r"[a-zäöüß]+", sample)
    if not tokens:
        return "unknown"
    de_hits = sum(1 for t in tokens if t in _GERMAN_STOPWORDS)
    en_hits = sum(1 for t in tokens if t in _ENGLISH_STOPWORDS)
    de_score = de_hits + (3 if has_german_chars else 0)
    en_score = en_hits
    # Thresholds tuned to short-section text (~10-30 words). A bare
    # tokens count under 4 is too short to call confidently.
    if len(tokens) < 4:
        return "de" if has_german_chars else "unknown"
    if de_score >= 2 and en_score >= 2:
        return "mixed"
    if de_score >= 2:
        return "de"
    if en_score >= 2:
        return "en"
    return "unknown"


def scrub_row_value(value: str, target_language: Language = "de") -> ScrubReport:
    """Run the guardrail rules on a section row's ``value``.

    Order of operations:
      1. Detect language.
      2. Detect defensive-AI opener → replace with canonical missing
         marker in ``target_language``; mark ``was_defensive``.
      3. If not defensive: strip hedges in the row's language.
    """
    original = value or ""
    # Strip leaked [#n] evidence-chunk indices first so they never reach the
    # rendered cell; ``original`` is preserved for audit.
    work = _CHUNK_REF_RE.sub("", original)
    if not work.strip():
        return ScrubReport(
            original=original,
            cleaned=work,
            was_defensive=False,
            hedges_stripped=0,
            language="unknown",
        )
    language = detect_language(work)
    if detect_defensive_ai(work):
        return ScrubReport(
            original=original,
            cleaned=(
                MISSING_VALUE_DE if target_language == "de" else MISSING_VALUE_EN
            ),
            was_defensive=True,
            hedges_stripped=0,
            language=language,
        )
    cleaned, n = strip_hedges(work, language)
    return ScrubReport(
        original=original,
        cleaned=cleaned,
        was_defensive=False,
        hedges_stripped=n,
        language=language,
    )


def scrub_finding_text(text: str, target_language: Language = "de") -> ScrubReport:
    """Same as :func:`scrub_row_value` but for a ``Finding.text``.

    The only behaviour difference is that a defensive Finding gets
    replaced with the canonical missing marker rather than dropped —
    the caller still wants a slot in the findings list so the section
    numbering stays stable.
    """
    return scrub_row_value(text, target_language=target_language)


# ────────────────────────────────────────────────────────────────────────
# Convenience: apply to a list-of-rows / list-of-findings
# ────────────────────────────────────────────────────────────────────────


def apply_to_rows(
    rows: list[Any],
    *,
    target_language: Language = "de",
    section_language_hint: Language | None = None,
) -> dict[str, int]:
    """Mutate ``rows`` in place, applying :func:`scrub_row_value` to each.

    Each row is expected to have ``value`` (str) and ``note`` (str)
    attributes (mirrors the existing ``Row`` model in
    ``ddiq_report.py``). For defensive cells, the row's ``ampel`` is
    coerced to ``"yellow"`` if it was ``"green"`` (a defensive paragraph
    dressed as green misleads reviewers). For mixed-language cells, a
    one-line "(Sprache weicht ab — review)" marker is appended to
    ``note``.

    Args:
        rows: List of mutable row objects.
        target_language: Language to use for the canonical missing
            marker on defensive cells.
        section_language_hint: When provided, mixed-language detection
            fires only when the row's language disagrees with this hint.
            Useful to flag the one English row in a German section.

    Returns:
        Counter dict ``{"defensive": n, "hedges": n, "mixed_lang": n}``
        — useful for logging "guardrail tweaked N cells in this report".
    """
    counts = {"defensive": 0, "hedges": 0, "mixed_lang": 0}
    for row in rows:
        raw = getattr(row, "value", None) or ""
        report = scrub_row_value(raw, target_language=target_language)
        if report.cleaned != raw:
            row.value = report.cleaned
        if report.was_defensive:
            counts["defensive"] += 1
            # Demote green-flagged defensive rows; a "(no info)" cell
            # is not a clean status.
            if getattr(row, "ampel", "") == "green":
                row.ampel = "yellow"
        counts["hedges"] += report.hedges_stripped
        # Mixed-language: count for logging only. We no longer surface a
        # "[Sprache: …]" marker in the client deliverable — A8 single-language
        # re-prompting (ddiq_report) fixes wholly-wrong-language cells, and a
        # bracketed debug tag in a partner-facing report reads as a defect.
        if (
            section_language_hint is not None
            and report.language not in ("unknown", section_language_hint)
        ):
            counts["mixed_lang"] += 1
        # Strip any leaked [#n] evidence-chunk indices from the note too.
        _note = getattr(row, "note", None)
        if _note:
            _clean_note = _CHUNK_REF_RE.sub("", _note).strip()
            if _clean_note != _note:
                row.note = _clean_note or None
    return counts


def apply_to_findings(
    findings: list[Any],
    *,
    target_language: Language = "de",
) -> dict[str, int]:
    """Mutate ``findings`` in place, scrubbing each ``text`` field.

    A defensive Finding's text is replaced with the canonical missing
    marker AND its ``severity`` is escalated to ``"yellow"`` if it was
    ``"green"`` — same demotion rule as for rows.

    Returns:
        Counter dict ``{"defensive": n, "hedges": n}``.
    """
    counts = {"defensive": 0, "hedges": 0}
    for f in findings:
        raw = getattr(f, "text", None) or ""
        report = scrub_finding_text(raw, target_language=target_language)
        if report.cleaned != raw:
            f.text = report.cleaned
        if report.was_defensive:
            counts["defensive"] += 1
            if getattr(f, "severity", "") == "green":
                f.severity = "yellow"
        counts["hedges"] += report.hedges_stripped
    return counts
