# `lai` — package map

The LAI backend is a single installable Python package (`pip install -e .` from
`LAI/`). Every module imports as `from lai.<package>...` — no `sys.path` hacks.

Each subpackage is one **domain**. A developer assigned to a domain works inside
that package's directory; ownership is declared in [`.github/CODEOWNERS`](../../../.github/CODEOWNERS).

| Package | Domain | What lives here |
|---|---|---|
| [`pipeline/`](pipeline/) | Data pipeline | The 6-step corpus build: convert → chunk → classify → enrich → generate → embed. CLI: `python -m lai.pipeline.cli`. |
| [`search/`](search/) | Retrieval | Hybrid dense+BM25 search, reranking, query analysis, and the retrieval **eval** harness (`eval.py`). |
| [`analyzer/`](analyzer/) | Contract analysis | The Qwen3.6-27B contract analyzer — playbooks, prompts, schema, cadastral NER, reconciler. |
| [`documents/`](documents/) | Document ingestion | Upload → parse → chunk → embed for user-supplied PDFs/DOCX. |
| [`extraction/`](extraction/) | Structured extraction | Pulls geographic data (addresses, coordinates, parcel IDs) out of documents. |
| [`generation/`](generation/) | Answer generation | LLM answer synthesis, CRAG, citation verification, prompt building. |
| [`api/`](api/) | HTTP surface | FastAPI app shell + `serve_rag.py`, the conversational chat backend (`:18000`). |
| [`auth/`](auth/) | Auth | JWT issue/verify, user repository, auth routes. |
| [`core/`](core/) | Core | Config, constants, logging, shared models, exceptions, utils. Imported by everything. |
| [`infra/`](infra/) | Infrastructure | Thin clients for PostgreSQL, MinIO, Redis. |

Not under `lai/`: the DDiQ report service lives in [`micro-services/`](../../micro-services/),
operational entry-point scripts in [`scripts/ops/`](../../scripts/ops/), and the web UI is
its own repo (`LAI-UI`).
