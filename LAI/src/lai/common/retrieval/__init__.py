"""pgvector-backed corpus retrieval.

Replaces the in-RAM numpy mat-mul retrieval in ``lai.search.eval`` (the
~144 GB ``Corpus.embs`` matrix) with HNSW ANN queries against
``corpus_child_chunks`` in Postgres — the table the Track-B migration
(``scripts/ops/migrate_corpus.py``) loads and indexes.

Submodules:

- :mod:`~lai.common.retrieval.config` — :class:`RetrievalConfig` (settings).
- :mod:`~lai.common.retrieval.metrics` — :class:`RetrievalMetrics` (Prometheus).
- :mod:`~lai.common.retrieval.client` — :class:`RetrievalClient` (sync,
  thread-safe) and :class:`RetrievedChunk` (one returned child chunk).

Consumed by ``serve_rag._do_rag`` (the keystone swap) and, later, the
DDiQ engine + the statutory-grounding path.
"""

from __future__ import annotations

from lai.common.retrieval.client import RetrievalClient, RetrievedChunk
from lai.common.retrieval.config import INDEX_DIM, RetrievalConfig
from lai.common.retrieval.metrics import RetrievalMetrics, default_retrieval_metrics

__all__ = [
    "INDEX_DIM",
    "RetrievalClient",
    "RetrievalConfig",
    "RetrievalMetrics",
    "RetrievedChunk",
    "default_retrieval_metrics",
]
