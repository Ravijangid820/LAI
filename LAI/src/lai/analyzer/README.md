# `lai.analyzer` — contract analysis

The contract analyzer (Qwen3.6-27B, thinking mode) — extracts structured findings
from German wind-energy contracts against playbooks.

| Module | Role |
|---|---|
| `pipeline.py` | Analyzer orchestration — the end-to-end analyze flow. |
| `playbooks.py` | Per-contract-type analysis playbooks. |
| `prompts.py` | Prompt templates. |
| `schema.py` | Pydantic schemas for analyzer I/O. |
| `llm_client.py` | LLM client (separate from `serve_rag`'s — JSON-guided decoding, thinking mode). |
| `cadastral_ner.py` | Cadastral named-entity recognition. |
| `reconciler.py` | Reconciles / dedupes extracted findings. |

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
