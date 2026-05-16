"""LAI retrieval kernel.

The only surviving module here is :mod:`lai.search.eval` — the
recall-eval / RAG-retrieval functions (``Corpus``, ``retrieve_dense``,
``retrieve_bm25``, ``rrf_fuse``, ``load_embeddings``) consumed by
``lai.api.serve_rag`` and the eval scripts under ``scripts/eval/``. The
former Postgres-backed hybrid_search / query_analyzer / reranker /
routes / repository modules were removed during the v1 demo restructure;
the equivalent capabilities will return through :mod:`lai.common` and a
new ``lai.retrieval`` package in the v1.1 unification work.
"""
