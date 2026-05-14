# `lai.generation` — answer generation

Turns retrieved context into grounded answers.

| Module | Role |
|---|---|
| `prompt_builder.py` | Assembles the generation prompt from query + retrieved context. |
| `llm_client.py` | LLM client for answer generation. |
| `crag.py` | Corrective RAG — retrieval-quality grading + correction. |
| `citation_verifier.py` | Verifies generated citations are grounded in the context. |

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
