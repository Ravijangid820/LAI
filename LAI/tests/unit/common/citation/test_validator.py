"""Tests for :mod:`lai.common.citation.validator`."""

from __future__ import annotations

import pytest

from lai.common.citation import (
    CITATION_PATTERN,
    CitationValidationResult,
    extract_citations,
    validate_citations,
)


class TestExtractCitations:
    @pytest.mark.unit
    def test_empty_input(self) -> None:
        assert extract_citations("") == []

    @pytest.mark.unit
    def test_no_citations(self) -> None:
        assert extract_citations("Plain text without any handles.") == []

    @pytest.mark.unit
    def test_single_corpus_citation(self) -> None:
        assert extract_citations("Per [C-1] the rule applies.") == ["C-1"]

    @pytest.mark.unit
    def test_single_matter_citation(self) -> None:
        assert extract_citations("Clause 7 [M-1] requires it.") == ["M-1"]

    @pytest.mark.unit
    def test_mixed_in_order(self) -> None:
        text = "Vgl. [C-3], dann [M-1], dann [C-1]."
        assert extract_citations(text) == ["C-3", "M-1", "C-1"]

    @pytest.mark.unit
    def test_deduplicated(self) -> None:
        text = "[C-1] und nochmals [C-1] sowie [M-2]."
        assert extract_citations(text) == ["C-1", "M-2"]

    @pytest.mark.unit
    def test_brackets_with_wrong_letter_ignored(self) -> None:
        # ``[X-1]`` is not a legal handle — must NOT be extracted.
        assert extract_citations("siehe [X-1] und [C-2]") == ["C-2"]

    @pytest.mark.unit
    def test_brackets_with_non_integer_ignored(self) -> None:
        assert extract_citations("siehe [C-1a] und [M-2]") == ["M-2"]

    @pytest.mark.unit
    def test_double_digit_index(self) -> None:
        assert extract_citations("Block [C-12] und [M-345].") == ["C-12", "M-345"]


class TestValidateCitationsHappyPath:
    @pytest.mark.unit
    def test_empty_text(self) -> None:
        result = validate_citations("", set())
        assert result.text == ""
        assert result.emitted == ()
        assert result.fabricated == ()
        assert result.sentences_flagged == 0

    @pytest.mark.unit
    def test_no_citations_at_all(self) -> None:
        result = validate_citations("Just a plain sentence.", set())
        assert result.text == "Just a plain sentence."
        assert result.emitted == ()
        assert result.fabricated == ()

    @pytest.mark.unit
    def test_all_resolved(self) -> None:
        text = "Per [C-1] the rule applies. Vgl. auch [M-1]."
        result = validate_citations(text, {"C-1", "M-1"})
        assert result.text == text  # unchanged
        assert set(result.emitted) == {"C-1", "M-1"}
        assert result.fabricated == ()
        assert result.sentences_flagged == 0

    @pytest.mark.unit
    def test_allowed_as_frozenset(self) -> None:
        # frozenset must be accepted too — the runtime signature claims
        # ``set | frozenset`` so this guards against accidental ``isinstance(_, set)`` checks.
        result = validate_citations("Per [C-1] the rule.", frozenset({"C-1"}))
        assert result.fabricated == ()


class TestValidateCitationsFabricated:
    @pytest.mark.unit
    def test_single_fabricated_stripped(self) -> None:
        # C-99 is not in allowed — must be stripped and the sentence marked.
        result = validate_citations("Per [C-99] the rule applies.", {"C-1"})
        assert result.fabricated == ("C-99",)
        assert "[C-99]" not in result.text
        assert "(unbelegt)" in result.text
        assert result.sentences_flagged == 1

    @pytest.mark.unit
    def test_marker_placed_before_terminator(self) -> None:
        result = validate_citations("Per [C-99] the rule applies.", set())
        # Sentence should still end with a period; marker sits before it.
        assert result.text.rstrip().endswith("(unbelegt).")

    @pytest.mark.unit
    def test_mixed_resolved_and_fabricated(self) -> None:
        # One resolved handle preserved, one fabricated stripped, single marker.
        text = "Both [C-1] and [C-99] are cited here."
        result = validate_citations(text, {"C-1"})
        assert "[C-1]" in result.text
        assert "[C-99]" not in result.text
        assert "(unbelegt)" in result.text
        assert result.sentences_flagged == 1

    @pytest.mark.unit
    def test_multiple_sentences_only_offending_marked(self) -> None:
        text = "Good [C-1] sentence. Bad [C-99] sentence. Good [M-1] sentence."
        result = validate_citations(text, {"C-1", "M-1"})
        # The middle sentence carries the marker; the two good ones don't.
        assert result.text.count("(unbelegt)") == 1
        assert "Good [C-1] sentence." in result.text
        assert "Good [M-1] sentence." in result.text
        assert result.sentences_flagged == 1
        assert result.fabricated == ("C-99",)

    @pytest.mark.unit
    def test_two_fabricated_in_same_sentence_one_marker(self) -> None:
        text = "Both [C-50] and [C-99] are fake here."
        result = validate_citations(text, set())
        assert result.text.count("(unbelegt)") == 1
        assert "[C-50]" not in result.text
        assert "[C-99]" not in result.text
        assert set(result.fabricated) == {"C-50", "C-99"}
        assert result.sentences_flagged == 1

    @pytest.mark.unit
    def test_no_terminator_marker_still_added(self) -> None:
        # Trailing sentence without a final period still gets the marker.
        result = validate_citations("Trailing claim [C-99]", set())
        assert "(unbelegt)" in result.text
        assert "[C-99]" not in result.text
        # No invented terminator.
        assert not result.text.endswith(".")

    @pytest.mark.unit
    def test_question_mark_terminator(self) -> None:
        result = validate_citations("Stimmt das [C-99]?", set())
        assert result.text.rstrip().endswith("(unbelegt)?")

    @pytest.mark.unit
    def test_emitted_includes_resolved_and_fabricated(self) -> None:
        # ``.emitted`` is a telemetry-style record of everything the
        # model said. ``.fabricated`` is the subset that wasn't allowed.
        text = "Refs [C-1] and [C-99]."
        result = validate_citations(text, {"C-1"})
        assert set(result.emitted) == {"C-1", "C-99"}
        assert result.fabricated == ("C-99",)


class TestIdempotence:
    @pytest.mark.unit
    def test_validating_twice_is_stable(self) -> None:
        first = validate_citations("Per [C-99] the rule. Per [C-1] also.", {"C-1"})
        second = validate_citations(first.text, {"C-1"})
        assert second.text == first.text
        # The marker text "(unbelegt)" itself contains no citation handles
        # so the second pass should report zero fabrications.
        assert second.fabricated == ()


class TestWhitespaceHygiene:
    @pytest.mark.unit
    def test_no_double_space_after_strip(self) -> None:
        text = "Claim [C-99] continues."
        result = validate_citations(text, set())
        # The stripped handle was between two spaces — must not leave
        # a double space behind.
        assert "  " not in result.text

    @pytest.mark.unit
    def test_no_orphan_space_before_period(self) -> None:
        text = "Claim ends here [C-99]."
        result = validate_citations(text, set())
        # The stripped handle was directly before the period — no
        # space should be orphaned before the marker.
        assert " ." not in result.text
        # The marker chain should sit cleanly: "...here (unbelegt)."
        assert "here (unbelegt)." in result.text


class TestRegexShape:
    @pytest.mark.unit
    def test_pattern_only_matches_documented_forms(self) -> None:
        # Sanity-check the exposed pattern.
        assert CITATION_PATTERN.fullmatch("[C-1]") is not None
        assert CITATION_PATTERN.fullmatch("[M-12]") is not None
        assert CITATION_PATTERN.fullmatch("[C-1a]") is None
        assert CITATION_PATTERN.fullmatch("[X-1]") is None
        assert CITATION_PATTERN.fullmatch("C-1") is None


class TestResultShape:
    @pytest.mark.unit
    def test_result_is_frozen(self) -> None:
        result = CitationValidationResult(
            text="x", emitted=("C-1",), fabricated=(), sentences_flagged=0,
        )
        with pytest.raises((AttributeError, TypeError)):
            result.text = "y"  # type: ignore[misc]
