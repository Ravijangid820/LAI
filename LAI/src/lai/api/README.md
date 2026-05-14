# `lai.api` — HTTP surface

The FastAPI application layer.

| Module | Role |
|---|---|
| `serve_rag.py` | The conversational chat backend (`:18000`). Loads the embedding corpus + reranker, serves `POST /query`, `POST /upload`, `POST /analyze-contract`, session endpoints. Run: `python -m lai.api.serve_rag --port 18000` (normally via [`scripts/ops/start.sh`](../../../scripts/ops/start.sh) or `start-host.sh`). |
| `main.py` | FastAPI app shell — mounts domain routers. |
| `pipeline.py` | API-side pipeline glue. |

The DDiQ report API is a separate service — see [`micro-services/`](../../../micro-services/).

Owner: see [`.github/CODEOWNERS`](../../../../.github/CODEOWNERS).
