"""Shared embedding client and supporting types.

The runtime embedding service is a vLLM container serving
``Qwen/Qwen3-Embedding-8B`` on the OpenAI-compatible ``POST /v1/embeddings``
endpoint. This package wraps that contract in a typed, retried,
metric-emitting client that both :mod:`serve_rag`, the DDiQ engine, and
the new ``lai.common.retrieval`` package import (replacing their duplicate
hand-rolled HTTP clients in ``src/lai/search/eval.py``,
``src/lai/documents/embedder.py``, and ``micro-services/ddiq_report.py``).

Submodules:

- :mod:`~lai.common.embedding.config` — :class:`EmbeddingConfig` (settings).
- :mod:`~lai.common.embedding.metrics` — :class:`EmbeddingMetrics` (Prometheus).
- :mod:`~lai.common.embedding.client` — :class:`EmbeddingClient` (async),
  :class:`SyncEmbeddingClient` (sync), and :class:`EmbeddingResult` (one
  embedded input).
"""

from __future__ import annotations

from lai.common.embedding.client import EmbeddingClient, EmbeddingResult, SyncEmbeddingClient
from lai.common.embedding.config import EmbeddingConfig
from lai.common.embedding.metrics import EmbeddingMetrics, default_embedding_metrics

__all__ = [
    "EmbeddingClient",
    "EmbeddingConfig",
    "EmbeddingMetrics",
    "EmbeddingResult",
    "SyncEmbeddingClient",
    "default_embedding_metrics",
]
