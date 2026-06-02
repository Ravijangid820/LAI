"""Unit tests for serve_rag._detect_question_language.

Regression for the 2026-06-01 ks/as session-2 audit: "was kannst du
hier im datenraum erkennen?" was detected as English (because "was" is
in _EN_HINT_WORDS — the legitimate past tense of "to be" — while the
distinctive German tokens "kannst" / "du" / "hier" / "kann" weren't
on the _DE_HINT_WORDS side at all). The model then received the
English ANTWORTSPRACHE directive and answered the German question in
English.
"""

from __future__ import annotations

import os

os.environ.setdefault("LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-lang-detect-0123456789abcdef")

import pytest

from lai.api import serve_rag as sr

pytestmark = pytest.mark.unit


# ── German questions that previously misdetected as English ─────────────


@pytest.mark.parametrize(
    "q",
    [
        # The exact 2026-06-01 ks/as session-2 failure
        "was kannst du hier im datenraum erkennen?",
        # Near-paraphrases that fall into the same trap (was/kannst/du)
        "was kannst du mir hier zeigen?",
        "kannst du das hier erklären?",
        "kann ich hier eine Frage stellen?",
        # The session-1 ks failure that already worked but pin the contract
        "gehst du semantisch vor?",
        "Bist du online?",
        "Was kann ich hier tun?",
    ],
)
def test_detects_german_when_dominant_hints_are_de_only_tokens(q: str) -> None:
    assert sr._detect_question_language(q) == "de", f"misdetected: {q!r}"


# ── English questions still detect correctly (no false flip the other way)


@pytest.mark.parametrize(
    "q",
    [
        "how many turbines does the permit cover?",
        "which turbine type is stated?",
        "what is the lease term?",
        "is the contract still valid?",
    ],
)
def test_detects_english_correctly(q: str) -> None:
    assert sr._detect_question_language(q) == "en", f"misdetected: {q!r}"


# ── Ambiguous / no-signal returns None ──────────────────────────────────


@pytest.mark.parametrize("q", ["hi", "ok", "danke"])
def test_returns_none_when_no_signal(q: str) -> None:
    assert sr._detect_question_language(q) is None


# ── The new additions never accidentally tilt an English question ────────


def test_added_de_words_do_not_match_common_english_text() -> None:
    """The 2026-06-02 additions (du, kannst, kann, hier, soll, muss, …)
    must not appear in plain English — otherwise a real English query
    could be falsely flipped to German."""
    common_english = [
        "what can the agent do",
        "where is the document",
        "who signed the lease",
        "this is a test of the system",
    ]
    for q in common_english:
        detected = sr._detect_question_language(q)
        assert detected != "de", (
            f"English query {q!r} was flipped to German — one of the "
            f"new _DE_HINT_WORDS additions is leaking into English"
        )
