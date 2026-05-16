"""Tests for :func:`lai.common.llm.strip_think`.

Two layers: concrete example cases that exercise every documented behaviour
of the function, and property-based tests (Hypothesis) that randomly
generate inputs and verify invariants.

The property tests are the load-bearing ones — they catch edge cases that
hand-written cases miss (e.g. adjacent think blocks with no whitespace
between them, single-character bodies, unicode in the trace).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lai.common.llm import strip_think

# ─────────────────────────────────────────────────────────────────────────────
# Concrete cases — one assertion per documented behaviour
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_no_think_tag_returns_input_unchanged() -> None:
    text = "The Rückbauverpflichtung is governed by § 35 Abs. 5 BauGB."
    assert strip_think(text) == text


@pytest.mark.unit
def test_empty_input_returns_empty() -> None:
    assert strip_think("") == ""


@pytest.mark.unit
def test_only_think_block_returns_empty_string() -> None:
    """A response that is *only* a think block leaves no answer."""
    assert strip_think("<think>internal reasoning</think>") == ""


@pytest.mark.unit
def test_think_then_answer_strips_block_and_surrounding_whitespace() -> None:
    text = "<think>Let me check § 35 BauGB...</think>\n\nThe answer is X."
    assert strip_think(text) == "The answer is X."


@pytest.mark.unit
def test_multiple_think_blocks_all_removed() -> None:
    text = "<think>step 1</think>" "First conclusion. " "<think>step 2</think>" "Second conclusion."
    assert strip_think(text) == "First conclusion. Second conclusion."


@pytest.mark.unit
def test_empty_think_block_is_removed_cleanly() -> None:
    assert strip_think("<think></think>answer") == "answer"


@pytest.mark.unit
def test_unclosed_think_block_truncates_to_what_came_before() -> None:
    """The model was cut off mid-reasoning — everything from ``<think>`` is dropped."""
    text = "preamble\n<think>thinking that never finished..."
    assert strip_think(text) == "preamble"


@pytest.mark.unit
def test_unclosed_think_block_with_nothing_before_returns_empty() -> None:
    assert strip_think("<think>only an unclosed trace") == ""


@pytest.mark.unit
def test_complete_block_followed_by_unclosed_block() -> None:
    """The first block is removed; the second (unclosed) takes everything after."""
    text = "<think>complete</think>middle<think>truncated..."
    assert strip_think(text) == "middle"


@pytest.mark.unit
def test_multiline_think_body_is_handled_via_dotall() -> None:
    text = "<think>line one\nline two\nline three</think>\nanswer"
    assert strip_think(text) == "answer"


@pytest.mark.unit
def test_think_block_with_special_characters_in_body() -> None:
    text = '<think>special chars: \\/*\n§ 35 "Abs. 5" </think>answer'
    assert strip_think(text) == "answer"


@pytest.mark.unit
def test_leading_whitespace_preserved_when_no_think_tag() -> None:
    """If we did not strip anything, caller's whitespace is preserved."""
    text = "   leading whitespace is the caller's data"
    assert strip_think(text) == text


@pytest.mark.unit
def test_trailing_whitespace_stripped_when_block_was_removed() -> None:
    """Once we modify the text, both sides are ``strip``-ed for consistency."""
    text = "<think>x</think>answer with trailing\n"
    assert strip_think(text) == "answer with trailing"


@pytest.mark.unit
def test_case_sensitive_does_not_strip_uppercase_tag() -> None:
    """We deliberately match only the lowercase tag Qwen actually emits."""
    text = "<THINK>not a Qwen trace</THINK>answer"
    assert strip_think(text) == text


@pytest.mark.unit
def test_bare_closing_tag_without_opening_is_left_alone() -> None:
    """Corrupted output: ``</think>`` without ``<think>``. We do not strip it."""
    text = "stray </think> tag"
    assert strip_think(text) == text


# ─────────────────────────────────────────────────────────────────────────────
# Property tests — Hypothesis-generated invariants
# ─────────────────────────────────────────────────────────────────────────────


# Bodies for think blocks: any text *not* containing the close tag, so the
# generated block remains well-formed.
_think_body = st.text(
    alphabet=st.characters(blacklist_characters="<>"),
    max_size=80,
)
"""Free-form text that cannot contain ``<`` or ``>``, so generated think
block bodies stay well-formed (no embedded fake tags)."""

# Answers: any printable text not containing the literal substring "<think>"
# so we do not accidentally make the answer look like an unstripped trace.
_answer_text = st.text(
    alphabet=st.characters(blacklist_characters="<>"),
    max_size=80,
)


@st.composite
def _text_with_think_blocks(draw: st.DrawFn) -> str:
    """Generate a string that interleaves N think blocks with N+1 answer pieces."""
    n_blocks = draw(st.integers(min_value=0, max_value=4))
    answers = [draw(_answer_text) for _ in range(n_blocks + 1)]
    bodies = [draw(_think_body) for _ in range(n_blocks)]
    out: list[str] = [answers[0]]
    for body, ans in zip(bodies, answers[1:], strict=True):
        out.append(f"<think>{body}</think>")
        out.append(ans)
    return "".join(out)


@pytest.mark.unit
@given(_text_with_think_blocks())
def test_output_contains_no_open_think_tag(text: str) -> None:
    """After stripping, ``<think>`` never appears in the output."""
    assert "<think>" not in strip_think(text)


@pytest.mark.unit
@given(_text_with_think_blocks())
def test_output_contains_no_close_think_tag(text: str) -> None:
    """After stripping, ``</think>`` never appears in the output.

    The generator only produces well-formed blocks, so a stray closing tag
    cannot leak through. (The concrete case `test_bare_closing_tag…`
    covers the non-well-formed input we deliberately leave alone.)
    """
    assert "</think>" not in strip_think(text)


@pytest.mark.unit
@given(_text_with_think_blocks())
def test_strip_think_is_idempotent(text: str) -> None:
    """Running the function twice yields the same result as running it once."""
    once = strip_think(text)
    twice = strip_think(once)
    assert once == twice


@pytest.mark.unit
@given(st.text(alphabet=st.characters(blacklist_characters="<>"), max_size=200))
def test_no_think_substring_means_input_returned_unchanged(text: str) -> None:
    """If the input contains no ``<think>`` substring, the function is the identity."""
    assert strip_think(text) == text


@pytest.mark.unit
@given(_text_with_think_blocks())
def test_output_has_no_surrounding_whitespace_after_strip(text: str) -> None:
    """When at least one think block was removed, both sides are trimmed.

    The exception is the "no-tag" fast path (separately tested above).
    """
    out = strip_think(text)
    if "<think>" in text and out:
        # `out` non-empty implies the strip happened; first and last chars
        # are not whitespace.
        assert not out[0].isspace()
        assert not out[-1].isspace()


@pytest.mark.unit
@given(_text_with_think_blocks())
def test_unclosed_think_truncation_invariant(text: str) -> None:
    """Appending an unclosed ``<think>`` and arbitrary tail must drop the tail."""
    truncated = text + "<think>residual reasoning"
    out = strip_think(truncated)
    # The output equals the output of stripping the well-formed prefix —
    # nothing from after the unclosed `<think>` survives.
    expected = strip_think(text)
    assert out == expected
