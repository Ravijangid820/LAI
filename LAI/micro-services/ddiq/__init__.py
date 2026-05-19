"""DDiQ report package (H-5 refactor).

The legacy ``ddiq_report`` module was a 3,100-LOC single file mixing
Pydantic models, DB schema, extractors, and FastAPI routes. H-5
splits that into a package:

* :mod:`ddiq.models`  — Pydantic request/response + domain models
  (``DDiQReportData``, ``Finding``, ``Evidence``, etc.). No DDiQ
  runtime dependencies; safe to import from anywhere.
* :mod:`ddiq.db`      — Postgres schema, connection pool,
  ``get_conn()``, lifecycle hooks (``init_pool``, ``close_pool``,
  ``init_db``, ``reap_orphans``). Pure psycopg2 + stdlib.
* :mod:`ddiq.extractors` — per-domain LLM-driven extraction
  functions (timeline, Rückbau, Grundbuch, WEA, infrastructure,
  findings). Added in a follow-up commit.

``ddiq_report`` itself stays as the FastAPI router + orchestration
layer and re-exports the names the rest of the codebase still
imports from it (``GenerateReportRequest``, ``router``, …).
"""

__all__: list[str] = []
