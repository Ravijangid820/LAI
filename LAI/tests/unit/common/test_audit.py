"""Unit tests for :mod:`lai.common.audit` — the best-effort append-only writer.

No real database: the asyncpg connection and the psycopg2 pool are faked, so
these assert the SQL/params and, crucially, that every failure is swallowed
(audit must never break the request it describes).
"""

from __future__ import annotations

from typing import Any

import pytest

from lai.common import audit

pytestmark = pytest.mark.unit


class _FakeAsyncConn:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail = fail

    async def execute(self, sql: str, *args: Any) -> str:
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((sql, *args))
        return "INSERT 0 1"


class _FakeCursor:
    def __init__(self, sink: list[tuple[Any, ...]]) -> None:
        self._sink = sink

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self._sink.append((sql, params))


class _FakeConn:
    def __init__(self, sink: list[tuple[Any, ...]]) -> None:
        self.autocommit = False
        self._sink = sink

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._sink)


class _FakePool:
    def __init__(self, sink: list[tuple[Any, ...]]) -> None:
        self._sink = sink
        self.put_count = 0

    def getconn(self) -> _FakeConn:
        return _FakeConn(self._sink)

    def putconn(self, conn: _FakeConn) -> None:
        self.put_count += 1


# ── _detail_json ────────────────────────────────────────────────────────────


def test_detail_json_none() -> None:
    assert audit._detail_json(None) is None


def test_detail_json_dict() -> None:
    out = audit._detail_json({"a": 1, "b": "x"})
    assert out is not None
    assert '"a": 1' in out


def test_detail_json_unserialisable_returns_none() -> None:
    circular: dict[str, Any] = {}
    circular["self"] = circular  # json.dumps raises ValueError on the cycle
    assert audit._detail_json(circular) is None


# ── record (async) ────────────────────────────────────────────────────────


async def test_record_inserts() -> None:
    conn = _FakeAsyncConn()
    await audit.record(conn, action="login", user_id="u1", outcome="success", latency_ms=12)
    assert len(conn.calls) == 1
    sql, *args = conn.calls[0]
    assert "INSERT INTO audit_log" in sql
    assert "login" in args
    assert "success" in args


async def test_record_swallows_failure() -> None:
    conn = _FakeAsyncConn(fail=True)
    await audit.record(conn, action="query")  # must NOT raise
    assert conn.calls == []


# ── record_sync ─────────────────────────────────────────────────────────────


def test_record_sync_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    sink: list[tuple[Any, ...]] = []
    pool = _FakePool(sink)
    monkeypatch.setattr(audit, "_get_pool", lambda: pool)
    audit.record_sync(action="report", user_id="u2", session_id="r1", latency_ms=42)
    assert len(sink) == 1
    sql, params = sink[0]
    assert "INSERT INTO audit_log" in sql
    assert params[2] == "report"  # action is the 3rd bound column
    assert pool.put_count == 1  # connection returned to the pool


def test_record_sync_swallows_pool_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("no db")

    monkeypatch.setattr(audit, "_get_pool", _boom)
    audit.record_sync(action="query")  # must NOT raise


# ── _get_pool (lazy + cached) ────────────────────────────────────────────────


def test_get_pool_lazy_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg2.pool

    created: list[int] = []

    class _FakeTCP:
        def __init__(self, *a: Any, **k: Any) -> None:
            created.append(1)

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", _FakeTCP)
    monkeypatch.setattr(audit, "_pool", None)
    p1 = audit._get_pool()
    p2 = audit._get_pool()
    assert p1 is p2  # cached
    assert len(created) == 1  # built exactly once


# ── query (read) ────────────────────────────────────────────────────────────


class _FakeFetchConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetch_args: tuple[Any, ...] | None = None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_args = (sql, *args)
        return self.rows


async def test_query_maps_rows_and_parses_detail() -> None:
    conn = _FakeFetchConn([{"id": 1, "action": "login", "detail": '{"email": "a@b.c"}'}])
    out = await audit.query(conn, org_id="o1", action="login", limit=10, offset=0)
    assert out[0]["action"] == "login"
    assert out[0]["detail"] == {"email": "a@b.c"}  # jsonb string parsed back to a dict
    assert conn.fetch_args is not None
    sql, *args = conn.fetch_args
    assert "FROM audit_log" in sql
    assert args == ["o1", "login", None, 10, 0]


async def test_query_detail_none_or_unparseable() -> None:
    conn = _FakeFetchConn([{"id": 2, "detail": None}, {"id": 3, "detail": "not json"}])
    out = await audit.query(conn)
    assert out[0]["detail"] is None
    assert out[1]["detail"] is None  # unparseable detail collapses to None
