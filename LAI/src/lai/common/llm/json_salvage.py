"""Repair-and-parse for almost-valid JSON returned by an LLM.

This module is the fallback in the guided-JSON design (ADR 0002): when
``LlmClient.generate_json`` cannot enforce structure via vLLM's
``extra_body.guided_json`` — because the schema is rejected, the deployed
backend doesn't support guided decoding, or the test environment uses a
mock — :func:`salvage_json` tries to make sense of whatever the model
emitted.

Repair scope
------------

We only fix the LLM mistakes we have *actually seen* in production output,
in order of conservatism:

1. **Markdown code-fence wrapping** — ``\\`\\`\\`json ... \\`\\`\\``` or just
   ``\\`\\`\\` ... \\`\\`\\```. Common when the model decides to be helpful.
2. **Trailing prose** — extracts the JSON substring from the first ``{`` or
   ``[`` to the matching close, ignoring anything before/after.
3. **Truncation** — appends missing close braces/brackets when the model
   was cut off mid-response. String contexts are tracked so braces inside
   strings are not counted.
4. **Trailing commas** — strips ``,`` immediately before ``}`` / ``]``.
   Again, string contexts are respected.

We deliberately do **not** attempt:

- Single-quote → double-quote conversion (Python-style JSON-ish strings).
- ``//`` or ``/* */`` comment removal.
- Smart-quote normalisation.
- Wholesale re-shaping when the model returns a list when the schema
  wanted an object, etc.

The Pydantic schema in the caller is responsible for type validation;
:func:`salvage_json` only has to get to *parseable* JSON.

Anything that can't be salvaged conservatively raises
:class:`~lai.common.exceptions.LlmJsonParseError` with the original input
attached for log inspection.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lai.common.exceptions import LlmJsonParseError

__all__ = ["salvage_json"]


# Tuned strict at the boundaries — anything not handled here raises.
_CODE_FENCE_PATTERN: re.Pattern[str] = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL,
)
"""Matches an entire response wrapped in a Markdown code fence.

Captures the fenced content into group 1. Tolerates the optional ``json``
language tag (case-insensitive) and incidental whitespace. Multi-line via
``DOTALL``.
"""


def salvage_json(text: str) -> Any:
    """Parse JSON from LLM output, repairing common malformations.

    Args:
        text: The raw LLM response. May be already-valid JSON, fenced
            JSON, JSON with trailing prose, truncated JSON, or invalid.

    Returns:
        The parsed JSON value. Typed as :data:`~typing.Any` to match
        :func:`json.loads`; the caller is responsible for validating the
        shape (usually via a Pydantic model).

    Raises:
        LlmJsonParseError: If the input is empty/whitespace-only, contains
            no parseable JSON, or salvage produced output that still
            fails :func:`json.loads`.
    """
    if not text or not text.strip():
        raise LlmJsonParseError("empty input", raw_response=text)

    # Fast path: well-formed JSON is unchanged by repair, so we try it first
    # and avoid the salvage cost in the common case.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Repair pipeline. Each step is conservative and may be a no-op for
    # inputs that don't need it.
    candidate = _strip_code_fences(text)
    extracted = _extract_first_json_substring(candidate)
    if extracted is None:
        raise LlmJsonParseError(
            "no JSON object or array found in input",
            raw_response=text,
        )
    candidate = _strip_trailing_commas(extracted)
    candidate = _balance_braces(candidate)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LlmJsonParseError(
            f"could not salvage JSON: {exc.msg} at line {exc.lineno} col {exc.colno}",
            raw_response=text,
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Private repair primitives
#
# Each helper is a string-in, string-out transformation with no side
# effects. They are exposed as module-private (single leading underscore)
# rather than module-public because they only make sense in the pipeline;
# external callers should use ``salvage_json``.
# ─────────────────────────────────────────────────────────────────────────────


def _strip_code_fences(text: str) -> str:
    """Strip a surrounding Markdown code fence if present.

    Returns the inner content if the *entire* input is fenced; otherwise
    returns the input unchanged. We do not strip partial / mid-text
    fences — those are usually prose with an embedded code sample and
    require the JSON extractor to find the real payload.
    """
    match = _CODE_FENCE_PATTERN.match(text)
    if match is None:
        return text
    return match.group(1)


def _extract_first_json_substring(text: str) -> str | None:
    """Extract from the first JSON start character to the matched close.

    Returns the substring from the first ``{`` or ``[`` to the matching
    close brace/bracket, accounting for strings. If the close is missing
    (truncation), returns the substring from the first opener to end of
    string — :func:`_balance_braces` will repair it downstream.

    Returns ``None`` if the input contains no ``{`` and no ``[``.
    """
    start = _find_first_json_opener(text)
    if start < 0:
        return None

    # Walk forward, tracking string context and brace depth.
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    # Truncated: depth never returned to zero. Return what we have; the
    # brace balancer will close it.
    return text[start:]


def _find_first_json_opener(text: str) -> int:
    """Return the index of the first ``{`` or ``[``, or ``-1`` if absent."""
    brace = text.find("{")
    bracket = text.find("[")
    if brace == -1:
        return bracket
    if bracket == -1:
        return brace
    return min(brace, bracket)


def _strip_trailing_commas(text: str) -> str:
    """Remove commas immediately before ``}`` or ``]``.

    String contexts are respected — a comma inside a JSON string is part
    of the value and must be preserved. Whitespace between the comma and
    the close brace is allowed (and removed along with the comma).

    This is a single linear pass; output length is at most input length.
    """
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escape:
            out.append(ch)
            escape = False
            i += 1
            continue
        if in_string:
            out.append(ch)
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            # Look ahead past whitespace; if the next non-space is a
            # closing brace/bracket, drop this comma.
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _balance_braces(text: str) -> str:
    """Append missing close braces/brackets to balance ``text``.

    Walks the string respecting string contexts, accumulates a stack of
    expected close characters, and appends whatever is still on the stack
    at the end. If the walk ends inside an open string, a closing ``"`` is
    appended before the brace closes.

    This is a best-effort repair; it cannot recover a missing *value*
    (e.g., ``{"a": ``), it can only close brackets that the model opened.
    """
    expected: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if in_string:
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            expected.append("}")
        elif ch == "[":
            expected.append("]")
        elif ch in "}]" and expected and expected[-1] == ch:
            expected.pop()

    suffix = ""
    if in_string:
        suffix += '"'
    while expected:
        suffix += expected.pop()
    return text + suffix
