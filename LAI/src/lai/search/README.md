# `lai.search` — search & retrieval

Hybrid retrieval (dense + BM25 + rerank) over the embedded corpus, plus the
retrieval evaluation harness.

| Module | Role |
|---|---|
| `hybrid_search.py` | Dense + BM25 fusion retrieval. |
| `reranker.py` | Qwen3-Reranker-8B cross-encoder reranking. |
| `query_analyzer.py` | Query understanding / rewriting. |
| `repository.py` | Data access for the search index. |
| `routes.py` | FastAPI routes for the search domain. |
| `eval.py` | Retrieval eval harness — Recall@K / MRR across 6 modes. Run: `python -m lai.search.eval --mode hybrid --n 200`. Imported by the benchmark scripts in `scripts/eval/` and by `lai.api.serve_rag`. |

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
