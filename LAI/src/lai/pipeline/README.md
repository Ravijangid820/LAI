# `lai.pipeline` — data processing pipeline

Builds the RAG corpus and fine-tuning data from raw legal documents, in 6 steps.

**Run:** `python -m lai.pipeline.cli step1..step6` (see `cli.py` for flags).

| Module | Step / role |
|---|---|
| `cli.py` | Entry point — orchestrates and resumes all 6 steps. |
| `convert.py` | Step 1 — raw docs → normalized text. |
| `chunk.py` | Step 2 — text → parent/child chunks. |
| `classify.py` | Step 3 — chunk classification. |
| `enrich.py` | Step 4 — context-prefix enrichment. |
| `generate.py` | Step 5 — synthetic training-data generation. |
| `embed.py` | Step 6 — child-chunk embeddings (supports `--embed-urls` for parallel GPUs). |
| `local_storage.py` | SQLite (`--local` mode) storage layer. |

Operational wrappers for resuming long-running steps: [`scripts/ops/resume_step5.sh`](../../../scripts/ops/resume_step5.sh), [`resume_step6.sh`](../../../scripts/ops/resume_step6.sh).

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
