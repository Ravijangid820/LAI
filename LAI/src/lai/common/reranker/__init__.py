"""Shared reranker client and supporting types.

The runtime reranker service hosted by HuggingFace TEI exposes
``POST /rerank`` with ``{"query": "...", "texts": [...]}`` and returns
``[{"index": int, "score": float}, ...]`` sorted by score descending. This
package wraps that contract in a typed, retried, metric-emitting client
that both :mod:`serve_rag` and the DDiQ engine import (replacing their
duplicate hand-rolled HTTP clients).

Submodules:

- :mod:`~lai.common.reranker.config` — :class:`RerankerConfig` (settings).
- :mod:`~lai.common.reranker.metrics` — :class:`RerankerMetrics` (Prometheus).
- :mod:`~lai.common.reranker.client` — :class:`RerankerClient` (async),
  :class:`SyncRerankerClient` (sync), and :class:`RerankResult` (one
  reranked item).
"""

from __future__ import annotations

from lai.common.reranker.client import RerankerClient, RerankResult, SyncRerankerClient
from lai.common.reranker.config import RerankerConfig
from lai.common.reranker.metrics import RerankerMetrics, default_reranker_metrics

__all__ = [
    "RerankResult",
    "RerankerClient",
    "RerankerConfig",
    "RerankerMetrics",
    "SyncRerankerClient",
    "default_reranker_metrics",
]
