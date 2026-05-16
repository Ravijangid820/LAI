"""Citation-handle extraction and validation.

The format is fixed by the ``serve_rag`` ``RAG_SYSTEM`` prompt:

- ``[C-n]`` for legal-corpus chunks (``n`` is a positive integer)
- ``[M-n]`` for matter (user-uploaded) document chunks

The validator's contract:

* Every handle that the model emitted but the prompt did not provide is
  **fabricated** — strip it and mark the containing sentence
  ``(unbelegt)``.
* Sentences are split on ``.`` / ``!`` / ``?`` followed by whitespace;
  abbreviation-aware splitting is intentionally NOT used here because
  the model's output is whatever the user asked for (English, German,
  mixed) and a heavyweight legal-German splitter would over-fit. The
  cheap split is good enough for marking suspicious sentences — and the
  marker is the legal-significant signal, not the sentence boundary.
* Idempotent: ``validate_citations(validate_citations(x).text, ...) ==
  validate_citations(x).text``.

The module is intentionally self-contained — no upstream dependencies
beyond :mod:`re`. That lets the validator be imported by ``serve_rag``
(which does not yet otherwise import :mod:`lai.common`) at near-zero
cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CITATION_PATTERN",
    "CitationValidationResult",
    "extract_citations",
    "validate_citations",
]


# Match ``[C-1]``, ``[M-12]``, etc. Strict integer suffix — we deliberately
# do NOT accept things like ``[C-1a]`` or ``[X-1]`` so the validator
# never collapses an unexpected ID format into a "valid" one.
CITATION_PATTERN: re.Pattern[str] = re.compile(r"\[([CM])-(\d+)\]")


# Sentence-terminator regex used to mark sentences after strip. A
# sentence here is loosely "text up to the next ``.``/``!``/``?``
# followed by whitespace or end-of-string". Quotes inside the sentence
# are not given special handling — the validator is a trust signal, not
# a syntactic parser.
_SENTENCE_BOUNDARY: re.Pattern[str] = re.compile(r"(?<=[.!?])(?=\s|$)")


# Marker appended (with one leading space) to sentences whose only
# citation(s) were stripped as fabricated. The closing tag-style string
# lets a downstream renderer recognise and style it (an amber chip in
# the UI per the demo plan).
_UNVERIFIED_MARKER = " (unbelegt)"


@dataclass(frozen=True, slots=True)
class CitationValidationResult:
    """Outcome of a single :func:`validate_citations` call.

    Attributes:
        text: The validated text — same as input when all citations
            resolved; with fabricated handles stripped and ``(unbelegt)``
            markers inserted otherwise.
        emitted: Every distinct citation handle that appeared in the
            input text, in first-seen order. Useful for telemetry —
            ``len(emitted) - len(fabricated)`` is the count of
            *resolved* citations.
        fabricated: Citation handles that did not match any allowed
            handle, in first-seen order.
        sentences_flagged: Number of sentences that had at least one
            fabricated handle stripped (and so received the
            ``(unbelegt)`` marker). May be smaller than
            ``len(fabricated)`` when a single sentence has multiple
            fabricated handles.
    """

    text: str
    emitted: tuple[str, ...]
    fabricated: tuple[str, ...]
    sentences_flagged: int


def extract_citations(text: str) -> list[str]:
    """Return every citation handle in ``text``, in first-seen order, deduplicated.

    Args:
        text: The LLM's answer string (possibly empty).

    Returns:
        A list of handles like ``["C-1", "M-1", "C-3"]``. The brackets
        are stripped; the order is the order of first occurrence so
        callers can build telemetry charts ordered by emission.
    """
    seen: set[str] = set()
    out: list[str] = []
    for match in CITATION_PATTERN.finditer(text):
        handle = f"{match.group(1)}-{match.group(2)}"
        if handle not in seen:
            seen.add(handle)
            out.append(handle)
    return out


def validate_citations(text: str, allowed: set[str] | frozenset[str]) -> CitationValidationResult:
    """Strip fabricated citations and mark their sentences ``(unbelegt)``.

    Args:
        text: The LLM's answer string. May be empty.
        allowed: The set of citation handles the prompt actually
            carried, e.g. ``{"M-1", "C-1", "C-2"}``. Anything not in
            this set is treated as fabricated.

    Returns:
        A :class:`CitationValidationResult`. The ``.text`` field is
        safe to surface to the user; the other fields are telemetry.

    Notes:
        * The function never raises on malformed input — empty text
          returns an empty result, and a sentence with only fabricated
          handles still gets a single marker (not one per handle).
        * Resolved handles are preserved verbatim. A sentence with a
          mix of resolved and fabricated handles has the fabricated
          ones removed and the ``(unbelegt)`` marker appended.
        * The marker is appended *inside* the sentence terminator: a
          sentence ending ``... [C-3].`` with C-3 fabricated becomes
          ``... (unbelegt).`` after validation.
    """
    if not text:
        return CitationValidationResult(
            text="",
            emitted=(),
            fabricated=(),
            sentences_flagged=0,
        )

    # First pass: enumerate emitted handles for telemetry. Done before
    # any rewriting so the input is unambiguous.
    emitted_list: list[str] = []
    emitted_seen: set[str] = set()
    fabricated_list: list[str] = []
    fabricated_seen: set[str] = set()
    for match in CITATION_PATTERN.finditer(text):
        handle = f"{match.group(1)}-{match.group(2)}"
        if handle not in emitted_seen:
            emitted_seen.add(handle)
            emitted_list.append(handle)
        if handle not in allowed and handle not in fabricated_seen:
            fabricated_seen.add(handle)
            fabricated_list.append(handle)

    if not fabricated_seen:
        return CitationValidationResult(
            text=text,
            emitted=tuple(emitted_list),
            fabricated=(),
            sentences_flagged=0,
        )

    # Second pass: rewrite sentence-by-sentence. Splitting on the
    # boundary regex preserves the terminator inside the preceding
    # sentence, so concatenating the parts back together is loss-less.
    sentences = _split_sentences(text)
    rewritten: list[str] = []
    flagged_count = 0
    for sentence in sentences:
        new_sentence, flagged = _rewrite_sentence(sentence, fabricated_seen)
        rewritten.append(new_sentence)
        if flagged:
            flagged_count += 1

    return CitationValidationResult(
        text="".join(rewritten),
        emitted=tuple(emitted_list),
        fabricated=tuple(fabricated_list),
        sentences_flagged=flagged_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────────


def _split_sentences(text: str) -> list[str]:
    """Split into sentence-sized chunks while preserving every character.

    Unlike ``str.split``, this keeps trailing whitespace attached to the
    sentence that owns it, so ``"".join(parts) == text`` exactly.
    """
    parts = _SENTENCE_BOUNDARY.split(text)
    # The regex split on a zero-width match can produce empty strings
    # at the edges; drop them so the rewriter doesn't waste cycles on
    # them.
    return [p for p in parts if p]


def _rewrite_sentence(sentence: str, fabricated: set[str]) -> tuple[str, bool]:
    """Remove fabricated handles from one sentence; append the marker if needed.

    Returns the rewritten sentence and a boolean indicating whether at
    least one fabricated handle was removed.
    """
    removed_any = False

    def _sub(match: re.Match[str]) -> str:
        nonlocal removed_any
        handle = f"{match.group(1)}-{match.group(2)}"
        if handle in fabricated:
            removed_any = True
            return ""
        return match.group(0)

    rewritten = CITATION_PATTERN.sub(_sub, sentence)
    if not removed_any:
        return sentence, False

    # Clean up the whitespace artefact of a stripped handle: a removed
    # ``[C-99]`` between two spaces leaves ``"...claim  ."`` style
    # output. Collapse runs of spaces only — newlines are preserved so
    # multi-paragraph answers don't lose their layout.
    rewritten = re.sub(r"[ \t]{2,}", " ", rewritten)
    # Remove a stray space before sentence-final punctuation:
    # ``"...claim ."`` → ``"...claim."``.
    rewritten = re.sub(r" +([.!?])", r"\1", rewritten)
    # Append the marker. Place it *before* the trailing sentence
    # terminator so the sentence still ends with proper punctuation:
    # ``"X."`` → ``"X (unbelegt)."``. Sentences without a terminator
    # (the last sentence of a paragraph with no ``.``) just get the
    # marker tacked on.
    match = re.search(r"([.!?])(\s*)$", rewritten)
    if match is not None:
        head = rewritten[: match.start()]
        terminator = match.group(1)
        trailing_ws = match.group(2)
        return f"{head.rstrip()}{_UNVERIFIED_MARKER}{terminator}{trailing_ws}", True
    return f"{rewritten.rstrip()}{_UNVERIFIED_MARKER}", True
