"""Tests for :class:`lai.common.retrieval.client.RetrievalClient`.

The psycopg2 pool + connections are faked so the suite needs no live
Postgres. Metrics observed on an isolated :class:`CollectorRegistry`.
The fakes model just enough of the DB-API surface the client touches:
``getconn`` / ``putconn`` / ``closeall`` on the pool, ``cursor`` /
``rollback`` on the connection, ``execute`` / ``fetchall`` / ``fetchone``
on the cursor.
"""

from __future__ import annotations

import psycopg2
import pytest
from prometheus_client import CollectorRegistry

from lai.common.exceptions import (
    RetrievalDimensionError,
    RetrievalQueryError,
    RetrievalRetryExhaustedError,
)
from lai.common.retrieval import RetrievalClient, RetrievalConfig, RetrievalMetrics
from lai.common.retrieval.client import RetrievedChunk, _format_halfvec_literal


# ── Fakes ────────────────────────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows_by_kind: dict, fail_with: Exception | None = None):
        self._rows_by_kind = rows_by_kind
        self._fail_with = fail_with
        self._last: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if self._fail_with is not None and "embedding <=>" in sql:
            raise self._fail_with
        if "SELECT 1" in sql:
            self._last = [(1,)]
        elif "embedding <=>" in sql:
            self._last = self._rows_by_kind.get("dense", [])
        elif "corpus_parent_chunks" in sql:
            self._last = self._rows_by_kind.get("parents", [])
        else:
            self._last = []

    def fetchall(self):
        return self._last

    def fetchone(self):
        return self._last[0] if self._last else None


class _FakeConn:
    def __init__(self, rows_by_kind, fail_with=None, rollback_raises=False):
        self._rows_by_kind = rows_by_kind
        self._fail_with = fail_with
        self._rollback_raises = rollback_raises
        self.rolled_back = False

    def cursor(self):
        return _FakeCursor(self._rows_by_kind, self._fail_with)

    def rollback(self):
        if self._rollback_raises:
            raise psycopg2.OperationalError("rollback failed")
        self.rolled_back = True


class _FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.got = 0
        self.put = 0
        self.closed_conns = 0
        self.closeall_called = False

    def getconn(self):
        self.got += 1
        return self._conn

    def putconn(self, conn, close=False):
        self.put += 1
        if close:
            self.closed_conns += 1

    def closeall(self):
        self.closeall_called = True


def _client_with_pool(pool, *, max_retries=2):
    """Build a client whose ``_ensure_pool`` returns the fake pool."""
    cfg = RetrievalConfig()
    client = RetrievalClient(
        cfg, metrics=RetrievalMetrics(registry=CollectorRegistry()), max_retries=max_retries
    )
    client._pool = pool  # inject; bypass real psycopg2 pool creation
    return client


def _qvec(dim=4000):
    return [0.01 * i for i in range(dim)]


# ── Helper ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_format_halfvec_literal_truncates():
    lit = _format_halfvec_literal(_qvec(4100), 4000)
    assert lit.startswith("[")
    assert lit.endswith("]")
    assert lit.count(",") == 3999  # 4000 values → 3999 separators


@pytest.mark.unit
def test_format_halfvec_literal_too_short_raises():
    with pytest.raises(RetrievalDimensionError) as ei:
        _format_halfvec_literal([1.0, 2.0], 4000)
    assert ei.value.expected == 4000
    assert ei.value.actual == 2


# ── dense_search ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_dense_search_maps_rows_to_chunks():
    rows = [
        (101, 5, "child text A", 0.91),
        (102, None, "orphan child", 0.80),
    ]
    pool = _FakePool(_FakeConn({"dense": rows}))
    client = _client_with_pool(pool)
    hits = client.dense_search(_qvec(), top_k=2)
    assert [h.child_id for h in hits] == [101, 102]
    assert hits[0].parent_id == 5
    assert hits[1].parent_id is None  # orphan NULL preserved
    assert hits[0].similarity == pytest.approx(0.91)
    assert isinstance(hits[0], RetrievedChunk)
    # connection returned to pool + rolled back
    assert pool.put == 1


@pytest.mark.unit
def test_dense_search_dimension_error_not_retried():
    pool = _FakePool(_FakeConn({"dense": []}))
    client = _client_with_pool(pool)
    with pytest.raises(RetrievalDimensionError):
        client.dense_search([1.0, 2.0], top_k=5)  # too short
    # never borrowed a connection — failed before the query
    assert pool.got == 0


@pytest.mark.unit
def test_dense_search_query_error_not_retried():
    """A SQL-level error re-raises immediately, no retry."""
    conn = _FakeConn({}, fail_with=psycopg2.errors.UndefinedTable("no table"))
    pool = _FakePool(conn)
    client = _client_with_pool(pool, max_retries=3)
    with pytest.raises(RetrievalQueryError):
        client.dense_search(_qvec(), top_k=5)
    assert pool.got == 1  # exactly one attempt


@pytest.mark.unit
def test_dense_search_transient_error_retries_then_exhausts():
    """An OperationalError is transient → retried up to budget, then wrapped."""
    conn = _FakeConn({}, fail_with=psycopg2.OperationalError("server gone"))
    pool = _FakePool(conn)
    client = _client_with_pool(pool, max_retries=2)
    with pytest.raises(RetrievalRetryExhaustedError) as ei:
        client.dense_search(_qvec(), top_k=5)
    assert ei.value.attempts == 3  # 1 + 2 retries
    assert pool.got == 3


@pytest.mark.unit
def test_dense_search_top_k_validation():
    pool = _FakePool(_FakeConn({"dense": []}))
    client = _client_with_pool(pool)
    with pytest.raises(RetrievalQueryError, match="top_k"):
        client.dense_search(_qvec(), top_k=0)


# ── fetch_parent_texts ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_fetch_parent_texts_dedups_and_maps():
    rows = [(5, "parent five"), (9, "parent nine")]
    pool = _FakePool(_FakeConn({"parents": rows}))
    client = _client_with_pool(pool)
    out = client.fetch_parent_texts([5, 9, 5, None, 9])
    assert out == {5: "parent five", 9: "parent nine"}


@pytest.mark.unit
def test_fetch_parent_texts_empty_short_circuits():
    pool = _FakePool(_FakeConn({"parents": []}))
    client = _client_with_pool(pool)
    assert client.fetch_parent_texts([None]) == {}
    assert pool.got == 0  # never touched the DB


# ── ping ────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ping_success():
    pool = _FakePool(_FakeConn({}))
    client = _client_with_pool(pool)
    assert client.ping() is True


@pytest.mark.unit
def test_ping_failure_returns_false():
    conn = _FakeConn({}, fail_with=psycopg2.OperationalError("down"))

    # Make SELECT 1 fail too by raising on any execute.
    class _AlwaysFailCursor(_FakeCursor):
        def execute(self, sql, params=None):
            raise psycopg2.OperationalError("down")

    class _C(_FakeConn):
        def cursor(self):
            return _AlwaysFailCursor({})

    pool = _FakePool(_C({}))
    client = _client_with_pool(pool)
    assert client.ping() is False


# ── pool lifecycle ───────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_close_calls_closeall():
    pool = _FakePool(_FakeConn({}))
    client = _client_with_pool(pool)
    client.close()
    assert pool.closeall_called is True
    assert client._pool is None


@pytest.mark.unit
def test_negative_retries_rejected():
    with pytest.raises(ValueError, match="max_retries"):
        RetrievalClient(max_retries=-1)
