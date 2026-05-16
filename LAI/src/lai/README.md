# `lai` — package map

The LAI backend is a single installable Python package (`pip install -e .` from
`LAI/`). Every module imports as `from lai.<package>...` — no `sys.path` hacks.

Each subpackage is one **domain**. A developer assigned to a domain works inside
that package's directory; ownership is declared in [`.github/CODEOWNERS`](../../../.github/CODEOWNERS).

| Package | Domain | What lives here |
|---|---|---|
| [`common/`](common/) | Shared primitives | Production-grade `LlmClient`, `EmbeddingClient`, `RerankerClient`, `PdfExtractor`, `Chunker`, exception hierarchy — the building blocks every other module imports. Held to a strict mypy/ruff/coverage gate (see `CONTRIBUTING.md`). |
| [`pipeline/`](pipeline/) | Data pipeline | The 6-step corpus build: convert → chunk → classify → enrich → generate → embed. CLI: `python -m lai.pipeline.cli`. |
| [`search/`](search/) | Retrieval kernel | The recall-eval / RAG-retrieval functions in `eval.py` (`Corpus`, `retrieve_dense`, `retrieve_bm25`, `rrf_fuse`, `load_embeddings`) used by `serve_rag` and the eval scripts. |
| [`analyzer/`](analyzer/) | Contract analysis | The Qwen3.6-27B contract analyzer — playbooks, prompts, schema, cadastral NER, reconciler. |
| [`api/`](api/) | HTTP surface | `serve_rag.py`, the conversational chat backend (`:18000`). Single runtime application. |
| [`core/`](core/) | Core | Config, constants, logging, shared models, exceptions, utils. Imported by `pipeline` and `analyzer`. |

Not under `lai/`: the DDiQ report service lives in [`micro-services/`](../../micro-services/),
operational entry-point scripts in [`scripts/ops/`](../../scripts/ops/), and the web UI is
its own repo (`LAI-UI`).

## Removed during the v1 demo restructure

The `auth/`, `documents/`, `extraction/`, `generation/`, `infra/` packages and
the dead `api/main.py`, `api/pipeline.py`, and Postgres-backed `search/`
routers (`routes`, `repository`, `reranker`, `hybrid_search`, `query_analyzer`)
were deleted — they were scaffolding for an unstarted FastAPI shell that never
talked to the live 350 GB SQLite corpus. The equivalent capabilities will
return through `lai.common` and a forthcoming `lai.retrieval` package as part
of the v1.1 unification work.
