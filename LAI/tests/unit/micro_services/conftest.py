"""Pytest fixtures for the DDiQ microservice test suite.

The :mod:`ddiq` package lives under ``LAI/micro-services/`` rather
than in the ``src/lai/`` editable install — it's the application
code that ships in the ``micro-services-backend`` Docker image, not
part of the reusable ``lai.common`` library. We add it to
``sys.path`` here so the test runner (executed from the repo root)
can ``import ddiq`` without an editable install.

Fixtures provided:

* :func:`mock_llm_client` / :func:`mock_embedding_client` — drop-in
  doubles for the singletons in :mod:`ddiq.llm`. Tests that need
  controlled LLM / embedding responses monkeypatch the singletons
  to these fakes via :func:`patch_llm_singletons`.

* :func:`isolated_metric_registry` — a fresh
  :class:`prometheus_client.CollectorRegistry` so tests don't
  collide on the global registry (the same pattern
  ``tests/unit/common/connectors/test_nominatim.py`` uses).

* :func:`make_llm_json` — small factory that builds a
  ``ddiq.llm.llm_json`` stub returning a caller-supplied object. The
  extractor tests use it to drive the pipeline without standing up
  the live analyzer LLM.

* :func:`evidence_chunks` — minimal reranker-style chunk dicts (the
  shape :func:`ddiq.rag.evidence_from_chunks` accepts), so tests can
  construct Evidence-aware fixtures without faking the whole RAG
  pass.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest


# ── sys.path bootstrap ───────────────────────────────────────────────
# Tests run with the repo root as cwd, but ``ddiq`` lives in
# ``LAI/micro-services/``. Prepend that to sys.path so ``import ddiq``
# resolves the in-tree package rather than failing.

_MICROSERVICES_DIR = Path(__file__).resolve().parents[3] / "micro-services"
if str(_MICROSERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_MICROSERVICES_DIR))


# ── Import-time env bootstrap ────────────────────────────────────────
# ``import ddiq_report`` transitively imports ``auth_dep``, which
# constructs ``AuthConfig()`` at module load. That config has NO
# default for the JWT secret (the deliberate fail-closed pattern — a
# missing secret must crash startup, not silently use a dev default)
# and validates a >=32-char minimum. So any test module that imports
# ``ddiq_report`` needs these set BEFORE collection.
#
# ``setdefault`` so a real env (CI / a developer's shell) is never
# clobbered. The value here is a throwaway used only to satisfy the
# constructor — no token is ever issued or verified in these unit
# tests.
os.environ.setdefault(
    "LAI_AUTH_JWT_ACCESS_SECRET",
    "unit-test-secret-0123456789abcdef0123456789abcdef",
)
os.environ.setdefault("DB_PASSWORD", "unit-test-db-password")


# ── Pre-import ddiq_report under warning suppression ─────────────────
# ``ddiq_report`` still registers startup/shutdown hooks via the
# deprecated ``@router.on_event`` decorator (FastAPI now recommends
# lifespan handlers — tracked as a follow-up). The decorator emits a
# DeprecationWarning at IMPORT time, and the project's pytest config
# promotes warnings to errors, which would fail collection of any test
# module that does ``import ddiq_report``.
#
# We import it once here, under a local warning filter, so the module
# is cached in ``sys.modules`` before the test modules import it (a
# cache hit re-runs no decorators, emits no warning). The global
# ``error`` filter stays fully in force for actual test execution —
# this only neutralises the one known import-time decorator warning,
# rather than weakening the gate project-wide.
import warnings  # noqa: E402

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import ddiq_report  # noqa: F401 — cached for the test modules
    except Exception:
        # If the import fails for a real reason (missing dep, etc.),
        # let the individual test module surface it with a clear error
        # rather than masking it here.
        pass


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeLlmClient:
    """Stand-in for :class:`lai.common.llm.SyncLlmClient` used by
    :mod:`ddiq.llm`'s singletons during tests.

    Records every call as ``(system, user, temperature, max_tokens)``
    so tests can assert against the prompt; ``responses`` is a queue
    of strings to return one-per-call. When exhausted, returns ``""``
    (matches the real client's behaviour when retries are exhausted —
    :func:`ddiq.llm.llm_call` returns ``""`` on :class:`LlmError`).

    Set ``raise_on_call=True`` to make :func:`generate` raise
    :class:`lai.common.exceptions.LlmError` instead, exercising the
    ``except LlmError`` branch.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        raise_on_call: bool = False,
    ) -> None:
        self.responses: list[str] = list(responses or [])
        self.calls: list[tuple[str, str, float, int]] = []
        self.raise_on_call = raise_on_call

    def generate(
        self,
        messages: list[Any],
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> str:
        from lai.common.exceptions import LlmError
        # messages is list[ChatMessage]; first is system, second is user.
        system = messages[0].content if len(messages) > 0 else ""
        user = messages[1].content if len(messages) > 1 else ""
        self.calls.append((system, user, temperature, max_tokens))
        if self.raise_on_call:
            raise LlmError("forced test failure")
        if not self.responses:
            return ""
        return self.responses.pop(0)


class _FakeEmbeddingClient:
    """Stand-in for :class:`lai.common.embedding.SyncEmbeddingClient`.

    Returns a fixed-dimension vector of ``index/100`` so a test can
    distinguish vectors by their first element. ``calls`` records each
    text passed in.
    """

    def __init__(self, dimension: int = 4096) -> None:
        self.dimension = dimension
        self.calls: list[str] = []

    def embed(self, texts: list[str]) -> list[Any]:
        # Match SyncEmbeddingClient.embed's return shape:
        # list[EmbeddingResult(index, embedding)].
        from types import SimpleNamespace
        out: list[Any] = []
        for i, t in enumerate(texts):
            self.calls.append(t)
            out.append(SimpleNamespace(
                index=i,
                embedding=[i / 100.0] * self.dimension,
            ))
        return out

    def embed_one(self, text: str) -> list[float]:
        self.calls.append(text)
        return [len(self.calls) / 100.0] * self.dimension


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm_client() -> _FakeLlmClient:
    """Return a fresh :class:`_FakeLlmClient` with an empty response queue.

    Tests that want pre-canned responses pass them via the
    :func:`make_llm_json` factory or push them onto ``.responses``
    directly.
    """
    return _FakeLlmClient()


@pytest.fixture
def mock_embedding_client() -> _FakeEmbeddingClient:
    return _FakeEmbeddingClient()


@pytest.fixture
def patch_llm_singletons(
    monkeypatch: pytest.MonkeyPatch,
    mock_llm_client: _FakeLlmClient,
    mock_embedding_client: _FakeEmbeddingClient,
) -> _FakeLlmClient:
    """Swap :mod:`ddiq.llm`'s singletons for the fakes.

    Both ``_LLM_CLIENT`` and ``_EMBEDDING_CLIENT`` are module-level;
    monkey-patching them is the standard way to isolate tests from
    the real analyzer LLM. Returns the LLM fake so the caller can
    push responses or assert against ``.calls``.
    """
    import ddiq.llm as _ddiq_llm

    monkeypatch.setattr(_ddiq_llm, "_LLM_CLIENT", mock_llm_client)
    monkeypatch.setattr(_ddiq_llm, "_EMBEDDING_CLIENT", mock_embedding_client)
    return mock_llm_client


@pytest.fixture
def isolated_metric_registry():
    """Isolated Prometheus :class:`CollectorRegistry` per test."""
    from prometheus_client import CollectorRegistry
    return CollectorRegistry()


@pytest.fixture
def evidence_chunks() -> list[dict[str, Any]]:
    """Minimal reranker-output shape used by
    :func:`ddiq.rag.evidence_from_chunks`. Three chunks so tests can
    exercise valid-index, out-of-range, and string-index cases.
    """
    return [
        {
            "doc_id": "doc-A",
            "filename": "BImSchG-Bescheid.pdf",
            "text": "§6 BImSchG-Genehmigung erteilt am 2024-03-15. " * 5,
        },
        {
            "doc_id": "doc-B",
            "filename": "Pachtvertrag-Flur-12.pdf",
            "text": "Pachtvertrag mit Eigentümer X über Flurstück 12/4. " * 5,
        },
        {
            "doc_id": "doc-C",
            "filename": "Ruckbau-Buergschaft.pdf",
            "text": "Bürgschaft über 250.000 € zugunsten der Gemeinde. " * 5,
        },
    ]


@pytest.fixture
def make_llm_json(monkeypatch: pytest.MonkeyPatch):
    """Factory that patches :func:`ddiq.llm.llm_json` to return a
    pre-baked object — the unit of control for extractor tests.

    Usage::

        def test_x(make_llm_json):
            make_llm_json([{"text": "foo"}])
            ...  # call code that hits llm_json internally

    The returned ``patch(...)`` function can be called multiple times
    per test to stage a sequence of responses (each call replaces the
    previous patch). For the single-response case, pass the object
    directly. For a queue, pass a list and a counter is maintained
    internally.
    """
    def patch(response: Any, *, queue: bool = False) -> list[tuple[str, str]]:
        """Patch ``llm_json`` (in ddiq.llm AND in every extractor
        module that has already imported it). Returns a list that
        gets appended to with ``(system, user)`` per call.

        Two modes:

        * ``queue=False`` (default): every call returns ``response``
          verbatim. Use this when the extractor makes ONE LLM call
          (most extractors); ``response`` is whatever the LLM would
          return — a dict, a list, a string, etc.
        * ``queue=True``: ``response`` is a list consumed one
          element per call. Use this when the extractor makes
          multiple LLM calls (e.g. ``generate_findings`` runs one
          per flagged row). Returns ``{}`` after the queue
          exhausts — matches :func:`ddiq.llm.llm_json`'s
          documented total-failure fallback.
        """
        calls: list[tuple[str, str]] = []

        if queue:
            q: list[Any] = list(response)

            def _stub(system: str, user: str, temperature: float = 0.0) -> Any:
                calls.append((system, user))
                if not q:
                    return {}
                return q.pop(0)
        else:
            single: Any = response

            def _stub(system: str, user: str, temperature: float = 0.0) -> Any:
                calls.append((system, user))
                return single

        # Patch the canonical location plus every module that did
        # ``from ddiq.llm import llm_json`` (the import binds a name
        # in the consumer's namespace, so patching ddiq.llm alone
        # misses them).
        import ddiq.llm
        monkeypatch.setattr(ddiq.llm, "llm_json", _stub)

        for modname in (
            "ddiq.extractors.timeline",
            "ddiq.extractors.consistency",
            "ddiq.extractors.rueckbau",
            "ddiq.extractors.grundbuch",
            "ddiq.extractors.findings",
            # ddiq_report binds llm_json too (analyze_section,
            # _generate_report_core metadata pass, etc.) — patch it so a
            # test exercising those never hits the real analyzer LLM.
            "ddiq_report",
        ):
            try:
                mod = __import__(modname, fromlist=["llm_json"])
            except ImportError:
                continue
            if hasattr(mod, "llm_json"):
                monkeypatch.setattr(mod, "llm_json", _stub)

        return calls

    return patch


# ── Fake Postgres for the orchestrator-side helpers (H-6b) ───────────
# Functions still in ``ddiq_report`` (geocode_address, alkis_query_parcels,
# _find_existing_report, _update_report_progress, _persist_report_jsonb)
# all go through ``ddiq_report.get_conn()``. The fakes below let tests
# stage cursor results + capture the executed SQL without a real
# Postgres. The cursor supports BOTH call shapes used in the code:
#   - ``conn = get_conn(); cur = conn.cursor()``  (geocode_address)
#   - ``with get_conn() as conn: with conn.cursor() as cur:``  (the rest)


class FakeCursor:
    """Minimal psycopg2 cursor double.

    Records every ``execute(sql, params)`` so a test can assert on the
    SQL + bound params. ``fetchone`` / ``fetchall`` return whatever the
    test staged. Usable as a context manager (``with conn.cursor()``)
    and via the plain ``cur = conn.cursor()`` form.
    """

    def __init__(self, fetchone: Any = None, fetchall: Any = None) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._fetchone = fetchone
        self._fetchall = fetchall
        self.rowcount = 0
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> Any:
        return self._fetchone

    def fetchall(self) -> Any:
        return self._fetchall or []

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeConn:
    """Minimal psycopg2 connection double. ``cursor()`` ignores the
    ``cursor_factory`` kwarg (RealDictCursor vs default) since the
    fake cursor returns whatever shape the test staged."""

    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:  # pragma: no cover — context-manager error path
        pass

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


@pytest.fixture
def fake_db(monkeypatch):
    """Patch ``ddiq_report.get_conn`` with a :class:`FakeConn`.

    Returns an ``install(fetchone=..., fetchall=...)`` callable that
    stages the cursor's return values and returns the
    ``(conn, cursor)`` pair so the test can assert on
    ``cursor.executed`` / ``conn.committed``.
    """
    import ddiq_report

    def install(fetchone: Any = None, fetchall: Any = None) -> tuple[FakeConn, FakeCursor]:
        cur = FakeCursor(fetchone=fetchone, fetchall=fetchall)
        conn = FakeConn(cur)
        monkeypatch.setattr(ddiq_report, "get_conn", lambda: conn)
        return conn, cur

    return install
