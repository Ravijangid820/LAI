"""LLM-client primitives for ``lai.common``.

This subpackage hosts the shared LLM client and its pure-function helpers
(``think_strip``, ``json_salvage``). Modules are added incrementally — see
``LAI/docs/adr/0001`` … ``0003`` for the foundational design decisions.
"""

from __future__ import annotations

from lai.common.llm.think_strip import strip_think

__all__ = ["strip_think"]
