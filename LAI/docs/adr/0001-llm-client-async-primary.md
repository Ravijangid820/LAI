# 0001 — `lai.common.llm`: async-primary client surface

- **Status:** Accepted
- **Date:** 2026-05-16
- **Owner:** `lai.common.llm`

## Context

LAI's existing LLM-calling code spans three codebases with three different
async stances:

- `serve_rag.py` is mostly sync `def` endpoints, but the `/query` flow
  benefits from async fan-out (embed + retrieve + rerank + LLM can overlap),
  and streaming responses require an async generator.
- The DDiQ microservice (`ddiq_report.py`) uses a sync
  `ThreadPoolExecutor` worker — each report makes ~45 sequential
  `llm_call` / `llm_json` calls from a worker thread. The codebase is not
  ready for async-everywhere; the SQL driver is `psycopg2` (sync).
- `lai.analyzer.llm_client` is sync, called from a worker thread for the
  contract analyzer.

We are extracting one shared `LlmClient` for all three. The async stance has
to be decided once.

## Decision

`LlmClient` exposes **async as the primary surface** with a **thin sync
wrapper** for callers that cannot yet adopt async.

```python
class LlmClient:
    async def generate(self, prompt: str, ...) -> str: ...
    async def generate_json(self, schema: type[T], prompt: str, ...) -> T: ...

class SyncLlmClient:
    """Sync façade over LlmClient for legacy callers (DDiQ worker, analyzer).
    Internally runs the async client via asyncio.run on a dedicated loop."""
    def generate(self, prompt: str, ...) -> str: ...
    def generate_json(self, schema: type[T], prompt: str, ...) -> T: ...
```

The sync wrapper exists to keep the migration window short: DDiQ and the
analyzer keep working unchanged while the rest of the system goes async.

## Consequences

- `serve_rag.py` gets fan-out concurrency in `/query` without rewriting
  every call site as a coroutine itself.
- DDiQ and the analyzer continue to work without changing their threading
  model — they import `SyncLlmClient` instead of `LlmClient`.
- The HTTP transport (`httpx.AsyncClient`) is shared across the two
  surfaces, so connection pooling, retries, and metrics live in one place.
- Future migration of DDiQ to async (when its DB driver, executor, and
  routes are async-ready) is a one-line import swap: `SyncLlmClient` →
  `LlmClient`.
- A sync caller paying the `asyncio.run` overhead per call is fine at
  DDiQ's 2-worker concurrency level. If that becomes a hot path, the
  sync wrapper can reuse a single background loop.

## Alternatives considered

- **Sync-only client.** Simplest implementation, but `serve_rag.py`'s
  streaming path needs an async generator, and the embedding/retrieval
  fan-out in `/query` benefits materially from `asyncio.gather`. Rejected.
- **Async-only client, no sync wrapper.** Forces DDiQ to call
  `asyncio.run` at every call site (~45 per report) or rewrite the worker
  to an async event loop. Rejected because the migration is disruptive
  for no immediate benefit.
- **Two independent implementations (parallel sync + async classes).**
  Doubles the bug surface and the test matrix. Rejected.
