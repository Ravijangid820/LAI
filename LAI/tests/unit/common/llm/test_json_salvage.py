"""Tests for :func:`lai.common.llm.salvage_json`.

Concrete cases pin down the exact behaviour for the LLM mistakes we have
seen in production. Hypothesis property tests assert invariants over the
salvage pipeline for any well-formed JSON wrapped in plausible noise.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lai.common.exceptions import LlmJsonParseError
from lai.common.llm import salvage_json

# ─────────────────────────────────────────────────────────────────────────────
# Fast path: already-valid JSON
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_valid_object_parses_unchanged() -> None:
    assert salvage_json('{"a": 1, "b": "two"}') == {"a": 1, "b": "two"}


@pytest.mark.unit
def test_valid_array_parses_unchanged() -> None:
    assert salvage_json("[1, 2, 3]") == [1, 2, 3]


@pytest.mark.unit
def test_nested_structures_parse_unchanged() -> None:
    payload = '{"findings": [{"severity": "red", "evidence": [1, 2]}, {}]}'
    assert salvage_json(payload) == {
        "findings": [{"severity": "red", "evidence": [1, 2]}, {}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Empty / no-JSON inputs raise
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_empty_input_raises() -> None:
    with pytest.raises(LlmJsonParseError, match="empty input"):
        salvage_json("")


@pytest.mark.unit
def test_whitespace_only_input_raises() -> None:
    with pytest.raises(LlmJsonParseError, match="empty input"):
        salvage_json("   \n\t  ")


@pytest.mark.unit
def test_prose_with_no_json_raises() -> None:
    with pytest.raises(LlmJsonParseError, match="no JSON object or array"):
        salvage_json("This is just prose with no JSON in it.")


# ─────────────────────────────────────────────────────────────────────────────
# Code-fence stripping
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fenced_with_json_language_tag() -> None:
    text = '```json\n{"a": 1}\n```'
    assert salvage_json(text) == {"a": 1}


@pytest.mark.unit
def test_fenced_without_language_tag() -> None:
    text = '```\n{"a": 1}\n```'
    assert salvage_json(text) == {"a": 1}


@pytest.mark.unit
def test_fenced_with_uppercase_json_tag() -> None:
    text = '```JSON\n{"a": 1}\n```'
    assert salvage_json(text) == {"a": 1}


@pytest.mark.unit
def test_fenced_with_surrounding_whitespace() -> None:
    text = '   ```json\n  {"a": 1}  \n```   '
    assert salvage_json(text) == {"a": 1}


# ─────────────────────────────────────────────────────────────────────────────
# Prose extraction
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_leading_prose_is_stripped() -> None:
    text = 'Sure, here is the JSON you requested:\n{"a": 1}'
    assert salvage_json(text) == {"a": 1}


@pytest.mark.unit
def test_trailing_prose_is_stripped() -> None:
    text = '{"a": 1}\n\nLet me know if you need clarification.'
    assert salvage_json(text) == {"a": 1}


@pytest.mark.unit
def test_both_leading_and_trailing_prose() -> None:
    text = 'Here you go: {"answer": 42} — let me know if that helps!'
    assert salvage_json(text) == {"answer": 42}


# ─────────────────────────────────────────────────────────────────────────────
# Truncation repair
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_truncated_object_is_closed() -> None:
    text = '{"a": 1, "b": [1, 2, 3'
    assert salvage_json(text) == {"a": 1, "b": [1, 2, 3]}


@pytest.mark.unit
def test_truncated_array_is_closed() -> None:
    text = '[{"x": 1}, {"y": 2'
    assert salvage_json(text) == [{"x": 1}, {"y": 2}]


@pytest.mark.unit
def test_truncated_inside_string_is_closed() -> None:
    """Truncated mid-string: we close the string and the surrounding braces."""
    text = '{"a": 1, "b": "unterminated'
    result = salvage_json(text)
    assert result == {"a": 1, "b": "unterminated"}


# ─────────────────────────────────────────────────────────────────────────────
# Trailing-comma repair
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_trailing_comma_in_object() -> None:
    text = '{"a": 1, "b": 2,}'
    assert salvage_json(text) == {"a": 1, "b": 2}


@pytest.mark.unit
def test_trailing_comma_in_array() -> None:
    text = "[1, 2, 3,]"
    assert salvage_json(text) == [1, 2, 3]


@pytest.mark.unit
def test_trailing_comma_with_whitespace_before_close() -> None:
    text = '{"a": 1,\n  }'
    assert salvage_json(text) == {"a": 1}


@pytest.mark.unit
def test_comma_inside_string_value_preserved() -> None:
    """A comma inside a string is part of the value, not a trailing comma."""
    text = '{"sentence": "Hello, world,"}'
    assert salvage_json(text) == {"sentence": "Hello, world,"}


# ─────────────────────────────────────────────────────────────────────────────
# Combined repair (the real-world failure modes)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fenced_with_trailing_comma_is_salvaged() -> None:
    text = '```json\n{"findings": [{"a": 1},]}\n```'
    assert salvage_json(text) == {"findings": [{"a": 1}]}


@pytest.mark.unit
def test_fenced_truncated_is_salvaged() -> None:
    text = '```json\n{"a": 1, "b": [1, 2'
    assert salvage_json(text) == {"a": 1, "b": [1, 2]}


@pytest.mark.unit
def test_prose_then_fenced_then_prose() -> None:
    text = 'Sure! ```json\n{"x": 1}\n``` Hope that helps.'
    # The full fence regex anchors to start/end so it won't match here;
    # the extractor walks the substring inside and finds the JSON.
    assert salvage_json(text) == {"x": 1}


# ─────────────────────────────────────────────────────────────────────────────
# Hopeless inputs raise with context
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_garbled_input_raises_with_raw_response() -> None:
    text = '{"a": @@@}'  # @@@ is not valid JSON in any way
    with pytest.raises(LlmJsonParseError) as exc_info:
        salvage_json(text)
    assert exc_info.value.raw_response == text


@pytest.mark.unit
def test_raw_response_attached_on_empty_input() -> None:
    with pytest.raises(LlmJsonParseError) as exc_info:
        salvage_json("")
    assert exc_info.value.raw_response == ""


# ─────────────────────────────────────────────────────────────────────────────
# Property tests — over arbitrary JSON values
# ─────────────────────────────────────────────────────────────────────────────


# A bounded strategy for JSON-like values. Excluded:
# - Floats: round-trip equality is awkward; not the point of these tests.
# - Top-level scalars: salvage_json requires an opener `{` or `[`.
# - Surrogate code points (Unicode category Cs): `json.dumps` + `json.loads`
#   is not a true round-trip for lone surrogates — `json.loads` combines a
#   high-low surrogate pair into the actual non-BMP character, so the value
#   that comes out is not the value that went in. This is a Python stdlib
#   behaviour, not a salvage_json concern.
_json_keys = st.text(
    alphabet=st.characters(blacklist_characters='"\\', blacklist_categories=("Cs",)),
    min_size=1,
    max_size=10,
)
_json_string_values = st.text(
    alphabet=st.characters(blacklist_characters='"\\', blacklist_categories=("Cs",)),
    max_size=20,
)
_json_scalars: st.SearchStrategy[Any] = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**6), max_value=10**6),
    _json_string_values,
)


def _json_collections() -> st.SearchStrategy[Any]:
    return st.recursive(
        _json_scalars,
        lambda children: st.one_of(
            st.lists(children, max_size=5),
            st.dictionaries(_json_keys, children, max_size=5),
        ),
        max_leaves=10,
    )


_json_objects_or_arrays: st.SearchStrategy[Any] = _json_collections().filter(
    lambda v: isinstance(v, dict | list),
)


@pytest.mark.unit
@given(_json_objects_or_arrays)
def test_valid_json_round_trips(value: Any) -> None:
    """For any valid JSON object/array, ``salvage(dumps(value)) == value``."""
    assert salvage_json(json.dumps(value)) == value


@pytest.mark.unit
@given(_json_objects_or_arrays)
def test_fenced_round_trip(value: Any) -> None:
    """Wrapping in a Markdown fence does not change the parsed result."""
    text = f"```json\n{json.dumps(value)}\n```"
    assert salvage_json(text) == value


@pytest.mark.unit
@given(_json_objects_or_arrays)
def test_prose_round_trip(value: Any) -> None:
    """Surrounding prose is stripped; the parsed result is unchanged."""
    text = f"Here is the JSON: {json.dumps(value)} — done."
    assert salvage_json(text) == value


@pytest.mark.unit
@given(_json_objects_or_arrays)
def test_idempotence_via_dumps(value: Any) -> None:
    """``salvage(dumps(salvage(text))) == salvage(text)`` for repaired text.

    Asserts the salvage output is itself well-formed JSON: dumping and
    re-salvaging is a no-op.
    """
    text = json.dumps(value)
    once = salvage_json(text)
    twice = salvage_json(json.dumps(once))
    assert once == twice
