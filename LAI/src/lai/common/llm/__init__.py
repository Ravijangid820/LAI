"""LLM-client primitives for ``lai.common``.

This subpackage hosts the shared LLM client and its pure-function helpers
(``think_strip``, ``json_salvage``). Modules are added incrementally — see
``LAI/docs/adr/0001`` … ``0003`` for the foundational design decisions.
"""

from __future__ import annotations

from lai.common.llm.client import ChatMessage, LlmClient, SyncLlmClient
from lai.common.llm.config import LlmConfig
from lai.common.llm.json_salvage import salvage_json
from lai.common.llm.metrics import LlmMetrics, default_metrics
from lai.common.llm.think_strip import strip_think

__all__ = [
    "ChatMessage",
    "LlmClient",
    "LlmConfig",
    "LlmMetrics",
    "SyncLlmClient",
    "default_metrics",
    "salvage_json",
    "strip_think",
]
