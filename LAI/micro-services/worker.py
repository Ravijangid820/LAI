"""Celery worker for DDiQ async report generation.

Replaces the in-process ``ThreadPoolExecutor(max_workers=2)`` in
``ddiq_report.py``. The thread-pool approach had two production
problems:

1. **No persistence across restarts.** A ``docker compose up`` while
   reports were running orphaned them mid-pipeline — the row stayed
   ``status='running'`` forever, and the UI polled forever. The old
   ``reap_orphans()`` worked around this by marking everything not-
   yet-done as ``failed`` at startup, losing in-flight work.
2. **Single-process concurrency ceiling of 2.** Scaling to more
   reports meant running more uvicorn workers, which the rest of the
   stack isn't designed for.

The Celery design fixes both:

* **Broker**: Redis at ``redis://lai_redis:6379/0`` (the runtime
  stack's existing instance). The ``redis`` + ``celery[redis]``
  deps are already declared in ``pyproject.toml``; this file adds
  them to ``micro-services/requirements.txt`` too.
* **acks_late=True** + **task_reject_on_worker_lost=True**: a worker
  crash mid-task returns the message to the queue; another worker
  picks it up. The row stays ``status='running'`` (correctly — it
  IS still running, just on a different worker).
* **task_ignore_result=True**: the report writes its result into the
  ``ddiq_reports`` row directly (the existing
  ``_run_report_generation_job`` design). Celery's result backend
  would store the full report JSON in Redis again — wasteful and
  redundant. The row IS the result.
* **Soft time limit 90 min, hard 120 min**: a runaway report gets
  SIGKILLed before it permanently pins a worker. Both are above
  the observed 30-60 min worst case.
* **Worker prefetch multiplier 1**: long-running tasks shouldn't be
  batched. One task per worker at a time, no greedy prefetch.

Same image as ``lai-backend`` — ``micro-services/Dockerfile`` builds
once; the compose service just overrides the ``CMD`` to run the
Celery worker instead of uvicorn.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from celery import Celery
from celery.signals import worker_ready

__all__ = ["app", "generate_report_task"]


# ── Broker / result backend ─────────────────────────────────────────
#
# ``LAI_CELERY_BROKER_URL`` env override lets the operator point at a
# different Redis (e.g. for staging-vs-prod isolation) without touching
# the compose file. The default matches the lai_network DNS.

_BROKER_URL: str = os.environ.get(
    "LAI_CELERY_BROKER_URL",
    "redis://lai_redis:6379/0",
)


# Single Celery app instance. ``include`` lists the modules that
# declare tasks — only this file in v1. Phase 2 connector additions
# (MaStR / Handelsregister) that are slow enough to want background
# execution would land here too.
app = Celery(
    "ddiq",
    broker=_BROKER_URL,
    # task_ignore_result=True (set below) means no result backend
    # configured — saves Redis memory.
    include=["worker"],
)

app.conf.update(
    # ── Queue routing ────────────────────────────────────────────
    task_default_queue="ddiq",
    task_routes={
        "ddiq.report.generate": {"queue": "ddiq"},
    },

    # ── Reliability ──────────────────────────────────────────────
    # acks_late: the message stays on the queue until the task
    # returns. A worker crash mid-task → the message is redelivered
    # to another worker. Paired with reject_on_worker_lost for the
    # specific case where a worker is killed by the OS.
    task_acks_late=True,
    task_reject_on_worker_lost=True,

    # One task per worker process at a time. Prevents Celery from
    # prefetching a backlog onto a worker that's already busy with a
    # 30-60 min report.
    worker_prefetch_multiplier=1,

    # ── Time limits ──────────────────────────────────────────────
    # Soft: the task gets a SoftTimeLimitExceeded exception and can
    # clean up. Hard: the worker process is SIGKILLed. Both above
    # the observed worst-case 60-min report runtime.
    task_soft_time_limit=90 * 60,
    task_time_limit=120 * 60,

    # ── Result backend off ───────────────────────────────────────
    # The report writes its result into ddiq_reports.report_data
    # directly. We don't need Celery to also persist it.
    task_ignore_result=True,

    # ── Serialisation ────────────────────────────────────────────
    task_serializer="json",
    accept_content=["json"],

    # ── Worker behaviour ─────────────────────────────────────────
    # Log every task start / end with the task id so an operator can
    # correlate against the ddiq_reports row.
    worker_send_task_events=True,
    task_send_sent_event=True,

    # ── Broker connection retry on startup ───────────────────────
    # Default since Celery 6 is no-retry-on-startup, which causes
    # the worker to crash if Redis takes a moment to come up. Re-
    # enabling matches the old (5.x) behaviour.
    broker_connection_retry_on_startup=True,
)

_log = logging.getLogger("ddiq.worker")


# ── The actual task ─────────────────────────────────────────────────


@app.task(
    name="ddiq.report.generate",
    bind=True,
    # ``autoretry_for=(Exception,)`` would mask real bugs as transients.
    # We don't enable it — the report job has its own typed-fallback
    # handling inside ``_run_report_generation_job`` (HTTPException →
    # 'failed' with the message; bare Exception → 'failed' with the
    # truncated str). Anything we'd want to retry is already retried
    # inside ``lai.common.{llm,embedding,reranker,connectors}``.
)
def generate_report_task(
    self: Any,  # noqa: ANN401 — Celery's bound-task ``self``
    report_id: str,
    req_dict: dict[str, Any],
    user_id: str,
) -> None:
    """Celery task wrapper around :func:`ddiq_report._run_report_generation_job`.

    Args:
        report_id: ``ddiq_reports.id`` of the pre-created queued row.
        req_dict: ``GenerateReportRequest.model_dump()`` from the
            API. JSON-serialisable so it survives Redis round-trip;
            we rehydrate to the Pydantic model inside the worker.
        user_id: ``CurrentUser.id`` as a string. The task threads
            this through every DB write so tenant isolation holds.

    Behaviour:
        On any uncaught exception, Celery's acks_late semantics put
        the message back on the queue. ``_run_report_generation_job``
        catches HTTPException + Exception internally and marks the
        row ``status='failed'`` itself — so an exception escaping
        this wrapper means the JOB CODE crashed, not a known-mode
        failure, and re-running is the right answer.

    Imports are deferred to call time so the worker process doesn't
    pay the FastAPI app's import cost (uvicorn, the router, etc.)
    just for being a worker. Celery starts in <1s this way.
    """
    # Deferred imports — the worker doesn't need uvicorn / FastAPI
    # but importing ddiq_report at module level would pull them in.
    from ddiq_report import (  # type: ignore[import-not-found]
        GenerateReportRequest,
        _run_report_generation_job,
    )

    _log.info(
        "ddiq.report.generate.start report_id=%s task_id=%s user_id=%s",
        report_id,
        getattr(self.request, "id", "?"),
        user_id,
    )

    # Rehydrate the request model. JSON round-trip strips any
    # non-serialisable shape so Pydantic re-validates the canonical
    # form.
    req = GenerateReportRequest.model_validate(req_dict)

    # The job function does the work AND owns its own try/except for
    # known failure modes (writes status='failed' to the row). We
    # let bare exceptions propagate so Celery can log + retry per
    # acks_late semantics — but the inner function should swallow
    # everything routine.
    _run_report_generation_job(report_id, req, user_id)

    _log.info(
        "ddiq.report.generate.complete report_id=%s task_id=%s",
        report_id,
        getattr(self.request, "id", "?"),
    )


@worker_ready.connect
def _on_worker_ready(sender: Any = None, **_: Any) -> None:  # noqa: ANN401
    """Log when a worker comes up — useful for the deploy runbook."""
    _log.info(
        "ddiq worker ready: hostname=%s broker=%s",
        getattr(sender, "hostname", "?"),
        _BROKER_URL,
    )
