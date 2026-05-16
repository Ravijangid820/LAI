"""Tests for ``lai.common.exceptions``.

These cover the construction, hierarchy, attribute preservation, and cause
chaining of every exception in the public surface, plus a meta-test that
``__all__`` matches the exported subclasses.
"""

from __future__ import annotations

import pytest

from lai.common import exceptions
from lai.common.exceptions import (
    LaiCommonError,
    LlmCallError,
    LlmEmptyResponseError,
    LlmError,
    LlmGuidedDecodingError,
    LlmInvalidResponseError,
    LlmJsonParseError,
    LlmRetryExhaustedError,
    LlmSchemaValidationError,
)

# ─────────────────────────────────────────────────────────────────────────────
# Module surface
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_dunder_all_lists_every_exception() -> None:
    """``__all__`` enumerates every public exception in the module."""
    declared = set(exceptions.__all__)
    actual_public = {
        name
        for name, obj in vars(exceptions).items()
        if isinstance(obj, type) and issubclass(obj, LaiCommonError) and not name.startswith("_")
    }
    assert declared == actual_public


@pytest.mark.unit
def test_dunder_all_is_sorted() -> None:
    """``__all__`` is alphabetically sorted (stable diff property)."""
    assert exceptions.__all__ == sorted(exceptions.__all__)


# ─────────────────────────────────────────────────────────────────────────────
# Hierarchy
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "subclass",
    [
        LlmError,
        LlmCallError,
        LlmInvalidResponseError,
        LlmEmptyResponseError,
        LlmJsonParseError,
        LlmSchemaValidationError,
        LlmGuidedDecodingError,
        LlmRetryExhaustedError,
    ],
)
def test_every_exception_inherits_from_root(subclass: type[Exception]) -> None:
    """Callers can catch the whole package with ``except LaiCommonError``."""
    assert issubclass(subclass, LaiCommonError)
    assert issubclass(subclass, Exception)


@pytest.mark.unit
@pytest.mark.parametrize(
    "subclass",
    [
        LlmCallError,
        LlmInvalidResponseError,
        LlmEmptyResponseError,
        LlmJsonParseError,
        LlmSchemaValidationError,
        LlmGuidedDecodingError,
        LlmRetryExhaustedError,
    ],
)
def test_llm_subclasses_inherit_from_llm_error(subclass: type[Exception]) -> None:
    """LLM-specific exceptions can be caught with ``except LlmError``."""
    assert issubclass(subclass, LlmError)


@pytest.mark.unit
def test_invalid_response_specialisations() -> None:
    """``Empty / JsonParse / SchemaValidation`` all extend ``InvalidResponse``."""
    assert issubclass(LlmEmptyResponseError, LlmInvalidResponseError)
    assert issubclass(LlmJsonParseError, LlmInvalidResponseError)
    assert issubclass(LlmSchemaValidationError, LlmInvalidResponseError)


# ─────────────────────────────────────────────────────────────────────────────
# Construction & attribute preservation
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_root_carries_message() -> None:
    err = LaiCommonError("boom")
    assert str(err) == "boom"


@pytest.mark.unit
def test_call_error_records_status_and_url() -> None:
    err = LlmCallError(
        "503 from analyzer",
        status_code=503,
        url="http://lai_analyzer_llm:8000/v1/chat/completions",
    )
    assert str(err) == "503 from analyzer"
    assert err.status_code == 503
    assert err.url == "http://lai_analyzer_llm:8000/v1/chat/completions"


@pytest.mark.unit
def test_call_error_defaults_are_none() -> None:
    err = LlmCallError("transport failed")
    assert err.status_code is None
    assert err.url is None


@pytest.mark.unit
def test_invalid_response_records_raw_response() -> None:
    err = LlmInvalidResponseError("bad content", raw_response="```json\n{")
    assert err.raw_response == "```json\n{"


@pytest.mark.unit
def test_empty_response_inherits_raw_response_field() -> None:
    err = LlmEmptyResponseError("empty content", raw_response="")
    assert isinstance(err, LlmInvalidResponseError)
    assert err.raw_response == ""


@pytest.mark.unit
def test_schema_validation_records_errors_list() -> None:
    errors: list[dict[str, object]] = [
        {"loc": ("findings", 0, "severity"), "msg": "value is not 'red'"},
    ]
    err = LlmSchemaValidationError(
        "schema mismatch",
        raw_response='{"findings": []}',
        validation_errors=errors,
    )
    assert err.validation_errors == errors


@pytest.mark.unit
def test_schema_validation_defaults_to_empty_errors() -> None:
    err = LlmSchemaValidationError("schema mismatch")
    assert err.validation_errors == []


@pytest.mark.unit
def test_schema_validation_copies_errors_defensively() -> None:
    """Mutating the caller's list must not corrupt the exception."""
    errors: list[dict[str, object]] = [{"loc": ("x",), "msg": "bad"}]
    err = LlmSchemaValidationError("schema mismatch", validation_errors=errors)
    errors.append({"loc": ("y",), "msg": "also bad"})
    assert len(err.validation_errors) == 1


@pytest.mark.unit
def test_guided_decoding_records_schema_excerpt() -> None:
    err = LlmGuidedDecodingError(
        "vLLM rejected schema",
        schema_excerpt='{"type": "object", "properties": {...}}',
    )
    assert err.schema_excerpt == '{"type": "object", "properties": {...}}'


@pytest.mark.unit
def test_retry_exhausted_records_attempts() -> None:
    err = LlmRetryExhaustedError("gave up", attempts=3)
    assert err.attempts == 3


@pytest.mark.unit
def test_retry_exhausted_rejects_zero_attempts() -> None:
    """``attempts=0`` is a contract violation — the loop must have tried once."""
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        LlmRetryExhaustedError("nonsense", attempts=0)


@pytest.mark.unit
def test_retry_exhausted_rejects_negative_attempts() -> None:
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        LlmRetryExhaustedError("nonsense", attempts=-1)


# ─────────────────────────────────────────────────────────────────────────────
# Cause chaining
# ─────────────────────────────────────────────────────────────────────────────


def _raise_json_parse_chained_from_value_error() -> None:
    """Helper: chain ``LlmJsonParseError`` from a ``ValueError``.

    Extracted into a helper so the ``with pytest.raises(...):`` block under
    test contains a single statement (PT012). The chained-cause semantics
    are what the test exercises.
    """
    try:
        raise ValueError("upstream parse failure")
    except ValueError as exc:
        raise LlmJsonParseError("could not salvage") from exc


@pytest.mark.unit
def test_raise_from_preserves_cause() -> None:
    """``raise X from Y`` chains the original cause for stack traces."""
    with pytest.raises(LlmJsonParseError) as exc_info:
        _raise_json_parse_chained_from_value_error()

    cause = exc_info.value.__cause__
    assert isinstance(cause, ValueError)
    assert str(cause) == "upstream parse failure"


@pytest.mark.unit
def test_retry_exhausted_chains_last_cause() -> None:
    """The retry loop's pattern of ``raise RetryExhausted(...) from last_exc``."""
    last_exc = LlmCallError("attempt 3 failed", status_code=503)
    with pytest.raises(LlmRetryExhaustedError) as exc_info:
        raise LlmRetryExhaustedError("exhausted after 3 attempts", attempts=3) from last_exc

    caught = exc_info.value
    assert caught.__cause__ is last_exc
    assert isinstance(caught.__cause__, LlmCallError)
    assert caught.attempts == 3


# ─────────────────────────────────────────────────────────────────────────────
# Pyright/mypy attribute access shape
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_attributes_are_typed_correctly() -> None:
    """Attribute types match the declared signatures — guards against drift."""
    call = LlmCallError("x", status_code=500, url="http://h")
    assert isinstance(call.status_code, int)
    assert isinstance(call.url, str)

    invalid = LlmInvalidResponseError("x", raw_response="r")
    assert isinstance(invalid.raw_response, str)

    schema = LlmSchemaValidationError("x", validation_errors=[{"k": "v"}])
    assert isinstance(schema.validation_errors, list)

    retry = LlmRetryExhaustedError("x", attempts=1)
    assert isinstance(retry.attempts, int)
