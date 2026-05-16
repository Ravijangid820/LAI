"""Tests for :mod:`lai.common.chunk.sentences`.

Covers the German-legal abbreviation-aware sentence splitter and the
section-boundary scanner. The expected behaviours are anchored to the
patterns that the live pipeline relies on — see
``src/lai/pipeline/utils/german_splitter.py`` for the historical
implementation these tests freeze.
"""

from __future__ import annotations

import pytest

from lai.common.chunk.sentences import (
    ABBREVIATIONS,
    SECTION_PATTERNS,
    find_section_boundaries,
    split_sentences,
)


class TestSplitSentencesEdgeCases:
    @pytest.mark.unit
    def test_empty_input_returns_empty_list(self) -> None:
        assert split_sentences("") == []

    @pytest.mark.unit
    def test_whitespace_only_returns_empty_list(self) -> None:
        # No periods means no splits, but the single whitespace-only
        # token strips down to an empty string and is dropped.
        assert split_sentences("   \n\t   ") == []

    @pytest.mark.unit
    def test_single_sentence_no_terminator(self) -> None:
        # No period; the regex never fires, so the whole input is a
        # single "sentence."
        assert split_sentences("Eine Aussage ohne Punkt") == ["Eine Aussage ohne Punkt"]


class TestSplitSentencesHappyPath:
    @pytest.mark.unit
    def test_two_simple_sentences(self) -> None:
        text = "Das ist Satz eins. Das ist Satz zwei."
        assert split_sentences(text) == ["Das ist Satz eins.", "Das ist Satz zwei."]

    @pytest.mark.unit
    def test_question_and_exclamation(self) -> None:
        text = "Ist das richtig? Ja, das ist es! Und es geht weiter."
        assert split_sentences(text) == [
            "Ist das richtig?",
            "Ja, das ist es!",
            "Und es geht weiter.",
        ]

    @pytest.mark.unit
    def test_paragraph_anchored_section_starts_split(self) -> None:
        # Sentence-final punctuation followed by ``§`` opens a new sentence.
        text = "Vorbemerkung. § 35 BauGB regelt das Außenbereich."
        result = split_sentences(text)
        assert result == ["Vorbemerkung.", "§ 35 BauGB regelt das Außenbereich."]


class TestAbbreviationProtection:
    @pytest.mark.unit
    def test_abs_does_not_split(self) -> None:
        text = "Nach § 35 Abs. 5 BauGB ist Rückbau Pflicht."
        # ``Abs.`` is in the abbreviation set; the period must not split.
        assert split_sentences(text) == [
            "Nach § 35 Abs. 5 BauGB ist Rückbau Pflicht.",
        ]

    @pytest.mark.unit
    def test_dr_prof_titles_protected(self) -> None:
        text = "Dr. Müller und Prof. Schmidt waren anwesend. Sie referierten."
        assert split_sentences(text) == [
            "Dr. Müller und Prof. Schmidt waren anwesend.",
            "Sie referierten.",
        ]

    @pytest.mark.unit
    def test_court_abbreviations_protected(self) -> None:
        text = "Das BGH-Urteil bestätigt das. Auch das OVG hat zugestimmt."
        # No abbreviation-trailing period here, but if there were one
        # (`v. BGH.` say), it would survive — the abbreviation set is
        # exposed for that reason.
        assert "bgh" in ABBREVIATIONS
        assert "ovg" in ABBREVIATIONS
        assert len(split_sentences(text)) == 2

    @pytest.mark.unit
    def test_numeric_periods_not_split(self) -> None:
        # A decimal like "2.5" must NOT be treated as a sentence boundary.
        text = "Der Wert beträgt 2.5 Meter pro Sekunde. Die Messung war korrekt."
        result = split_sentences(text)
        assert result == [
            "Der Wert beträgt 2.5 Meter pro Sekunde.",
            "Die Messung war korrekt.",
        ]

    @pytest.mark.unit
    def test_known_legal_code_period_is_protected(self) -> None:
        # ``BauGB`` is in the abbreviation set, so a trailing period after
        # it is *not* a sentence terminator. This is the historical
        # behaviour the pipeline relied on and we pin it here.
        text = "Siehe § 35 BauGB. Rn. 158 ist einschlägig."
        result = split_sentences(text)
        assert result == ["Siehe § 35 BauGB. Rn. 158 ist einschlägig."]


class TestSentenceJoiningSafety:
    @pytest.mark.unit
    def test_sentences_are_strip_clean(self) -> None:
        text = "  Eins.   Zwei.   "
        result = split_sentences(text)
        assert result[0] == "Eins."
        assert result[1] == "Zwei."

    @pytest.mark.unit
    def test_split_only_on_uppercase_or_section_marker(self) -> None:
        # A period followed by a lowercase next word is NOT a sentence
        # boundary — the regex requires upper-case / ``§`` / ``[`` after.
        text = "Erster Teil. zweiter teil ohne Großschreibung."
        assert split_sentences(text) == [
            "Erster Teil. zweiter teil ohne Großschreibung.",
        ]


class TestSectionBoundaries:
    @pytest.mark.unit
    def test_paragraph_marker_detected(self) -> None:
        text = "§ 35 BauGB\nEin Außenbereichsvorhaben."
        boundaries = find_section_boundaries(text)
        assert boundaries
        offset, marker = boundaries[0]
        assert offset == 0
        assert "§ 35" in marker

    @pytest.mark.unit
    def test_artikel_marker_detected(self) -> None:
        text = "Artikel 12 Berufsfreiheit"
        boundaries = find_section_boundaries(text)
        assert boundaries[0][1].startswith("Artikel 12")

    @pytest.mark.unit
    def test_structural_marker_detected(self) -> None:
        text = "Tatbestand\nDie Klägerin trägt vor..."
        boundaries = find_section_boundaries(text)
        assert any("Tatbestand" in marker for _, marker in boundaries)

    @pytest.mark.unit
    def test_boundaries_sorted_by_offset(self) -> None:
        text = "Tatbestand\n\n§ 1 X\n\nArtikel 7 Y"
        boundaries = find_section_boundaries(text)
        offsets = [b[0] for b in boundaries]
        assert offsets == sorted(offsets)

    @pytest.mark.unit
    def test_no_match_returns_empty(self) -> None:
        assert find_section_boundaries("ein normaler Absatz ohne Anker") == []

    @pytest.mark.unit
    def test_section_patterns_are_compiled_regexes(self) -> None:
        # Sanity: the public ``SECTION_PATTERNS`` tuple is the exact one
        # the scanner consumes — protecting downstream subclassing from
        # accidental shape changes.
        assert all(hasattr(p, "finditer") for p in SECTION_PATTERNS)
