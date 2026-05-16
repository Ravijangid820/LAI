"""Strip ``<think>...</think>`` reasoning traces from Qwen3.6-27B output.

Qwen3.x in thinking mode emits a reasoning trace inside ``<think>...</think>``
tags before producing the user-visible answer. ADR 0003 documents the
decision to strip those blocks server-side in :mod:`lai.common.llm` by
default, with a per-call ``keep_thinking`` toggle in the client.

This module is the **pure function** at the bottom of that design. It does
not know about LLM clients, vLLM, or streaming — it transforms one string
into another. That isolation is what makes it safe to property-test with
Hypothesis (see ``tests/unit/common/llm/test_think_strip.py``).

Robustness contract
-------------------

The function handles:

* **No think tags** — input returned unchanged.
* **One or more complete think blocks** — every ``<think>...</think>`` pair
  is removed (non-greedy, so adjacent blocks don't merge).
* **Mid-trace truncation** — an unclosed ``<think>`` followed by no
  ``</think>`` means the model was cut off; everything from the opening
  tag onward is discarded.
* **Empty think blocks** — ``<think></think>`` is removed cleanly.
* **Whitespace** — the result is always ``str.strip``-ed, on both the
  fast path (no tag in input) and the modification path. This is the
  only invariant that makes properties like
  ``strip_think(x + "<think>unclosed") == strip_think(x)`` hold for all
  ``x``. LLM trailing whitespace is almost always stylistic noise; a
  caller that needs the raw text passes ``keep_thinking=True`` to the
  client wrapper instead.

The function intentionally does **not** handle:

* **Nested think tags** — Qwen does not produce them. If they appeared,
  the non-greedy regex would close at the first ``</think>`` and leave the
  outer ``</think>`` in the output, which is the safer failure mode (the
  caller can spot the stray tag).
* **Tag attributes** — Qwen does not emit them; the regex matches the
  literal opening tag only.
* **Case variations** — Qwen emits lowercase ``<think>``; we match
  exactly. Treating ``<Think>`` as a think tag would be a guess.

Performance
-----------

Two compiled module-level patterns; an ``in`` check short-circuits the
common case (no think tag → no regex work, no allocation).
"""

from __future__ import annotations

import re

__all__ = ["strip_think"]


_THINK_BLOCK: re.Pattern[str] = re.compile(r"<think>.*?</think>", re.DOTALL)
"""Matches one complete ``<think>...</think>`` block, non-greedy."""

_UNCLOSED_THINK: re.Pattern[str] = re.compile(r"<think>.*\Z", re.DOTALL)
"""Matches an unclosed opening ``<think>`` and everything to end-of-string.

Applied after :data:`_THINK_BLOCK` to discard truncated reasoning traces.
``\\Z`` (end of string) is used in preference to ``$`` so that no edge case
involving multi-line ``$`` semantics can leave content past the cut.
"""


def strip_think(text: str) -> str:
    """Remove ``<think>...</think>`` blocks from a Qwen3 response.

    Args:
        text: The raw response string returned by the LLM. May or may not
            contain reasoning-trace tags.

    Returns:
        The user-visible answer with reasoning traces removed. The result
        is always ``str.strip``-ed so the function's whitespace handling
        is consistent across the fast path (no tag in input) and the
        modification path (one or more tags removed). LLM trailing
        whitespace is almost always stylistic noise; a caller that needs
        the raw text passes ``keep_thinking=True`` at the client level.
    """
    # Fast path: avoid regex work when there is no thinking trace to strip.
    # `str.strip` is cheap when there's nothing to remove (returns the same
    # object), and consistent whitespace handling outweighs the micro-cost
    # in the case where the model did include leading/trailing whitespace.
    if "<think>" not in text:
        return text.strip()

    stripped = _THINK_BLOCK.sub("", text)
    stripped = _UNCLOSED_THINK.sub("", stripped)
    return stripped.strip()
