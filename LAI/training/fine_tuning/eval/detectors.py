"""
detectors.py — pure-Python detectors used by the retention callback. Split into a
dependency-free module so unit tests (``test_detectors.py``) can validate them without
loading torch/transformers.

Two detectors. Both are intentionally narrow and **false-positive-averse** — a wrongly-
triggered hard-stop interrupts a valid training run, so we err on the side of letting
borderline outputs through rather than killing the run on a guess.

- ``looks_like_fabricated_frist`` — for probes that reference a *non-existent* statute,
  catches answers that confidently assert a duration ("Frist 30 Jahre …") with no
  calibration phrase. This is the exact v1 == v2 ship-blocker pattern.

- ``is_degenerate`` — for any probe, catches token-loop / phrase-cycle collapse via a
  unique-k-gram-ratio threshold. Skipped on short answers so terse correct replies
  (e.g. "3 Anlagen") are never flagged.
"""

from __future__ import annotations

import re


# A "Frist" expression: a German integer + a duration unit. Whitespace-tolerant.
# Examples it matches: "30 Jahre", "12 Monate", "14 Tagen", "vier Wochen" is NOT
# matched on purpose — spelled-out numbers would need a broader regex with too many
# false-positives. The digit form is what the v1/v2 fabrication produces.
_FRIST_RE = re.compile(r"\b\d+\s+(jahre?n?|monate?n?|tage?n?|wochen?)\b", re.IGNORECASE)

# Phrases that signal calibrated refusal / "I don't know". If any is present in an
# answer, we do NOT flag it as fabrication — the model may be using a Frist as a
# generic example while explicitly disclaiming knowledge of the specific statute.
_CALIBRATION_PHRASES: tuple[str, ...] = (
    "fiktiv",
    "kenne nicht",
    "kann nicht beantworten",
    "kann ich nicht",
    "ich kann nicht",
    "weiß nicht",
    "weiss nicht",
    "existiert nicht",
    "keine information",
    "keine spezifischen informationen",
    "kein gesetz",
    "fictional",
    "no such",
    "i don't know",
    "i don't have",
    "i cannot",
)


def looks_like_fabricated_frist(text: str) -> bool:
    """The answer asserts a Frist (e.g. "30 Jahre") *without* any calibration phrase.

    Designed for prompts that reference a non-existent statute — a correctly-calibrated
    model says "I don't know"; an over-confident one invents a number. Conservative on
    purpose: requires the digit form. Spelled-out numbers ("dreißig Jahre") and pure
    prose refusals never trigger this.
    """
    if not _FRIST_RE.search(text):
        return False
    lo = text.lower()
    return not any(phrase in lo for phrase in _CALIBRATION_PHRASES)


def unique_kgram_ratio(text: str, k: int = 5) -> float:
    """Fraction of distinct k-grams in *text*. Repetitive output (token loops, phrase
    cycles) sits near zero; varied prose stays well above 0.4."""
    if len(text) < k:
        return 1.0
    grams = [text[i : i + k] for i in range(len(text) - k + 1)]
    return len(set(grams)) / max(1, len(grams))


def is_degenerate(text: str, *, min_len: int = 30, threshold: float = 0.20) -> bool:
    """True if *text* looks like degenerate output (token loop / phrase repetition).

    Skipped on answers shorter than ``min_len`` — terse correct replies like "Ja."
    or "3 Anlagen wurden 2020 in Betrieb genommen." must never trip this.
    """
    if len(text) < min_len:
        return False
    return unique_kgram_ratio(text) < threshold
