"""Unit tests for the wait-and-answer helpers in serve_rag.

When a doc-scoped chat turn arrives while the uploaded scan is still being
OCR'd, the endpoint waits (bounded) for ingestion to finish and then
re-retrieves, instead of refusing with "still processing". These test the
two helpers that drive that wait — with a fake clock so no real sleeping.
"""

from __future__ import annotations

import os

os.environ.setdefault("LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-wait-unit-0123456789abcdef")

from lai.api import serve_rag as sr


class _Clock:
    """Fake monotonic clock: sleep() advances time instead of blocking."""

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


def _docs(monkeypatch, value):
    """value: a list (static) or a callable() -> list (sequence)."""
    if callable(value):
        monkeypatch.setattr(sr.persistence, "list_matter_documents", lambda sid, user_id=None: value())
    else:
        monkeypatch.setattr(sr.persistence, "list_matter_documents", lambda sid, user_id=None: value)


def test_is_processing_true_false_empty(monkeypatch):
    _docs(monkeypatch, [{"status": "processing"}])
    assert sr._matter_is_processing("s", "u") is True
    _docs(monkeypatch, [{"status": "queued"}])
    assert sr._matter_is_processing("s", "u") is True
    _docs(monkeypatch, [{"status": "done", "n_chunks": 5}])
    assert sr._matter_is_processing("s", "u") is False
    _docs(monkeypatch, [])
    assert sr._matter_is_processing("s", "u") is False


def test_await_ready_true_when_indexing_completes(monkeypatch):
    monkeypatch.setattr(sr, "time", _Clock())
    seq = [
        [{"status": "processing", "n_chunks": 0}],
        [{"status": "processing", "n_chunks": 0}],
        [{"status": "done", "n_chunks": 36}],  # ingestion finished
        [{"status": "done", "n_chunks": 36}],  # final readiness check
    ]
    state = {"i": 0}

    def nxt():
        v = seq[min(state["i"], len(seq) - 1)]
        state["i"] += 1
        return v

    _docs(monkeypatch, nxt)
    assert sr._await_matter_ready("s", "u", 180) is True


def test_await_ready_false_on_timeout(monkeypatch):
    monkeypatch.setattr(sr, "time", _Clock())
    _docs(monkeypatch, [{"status": "processing", "n_chunks": 0}])  # never finishes
    assert sr._await_matter_ready("s", "u", 5) is False


def test_await_ready_false_when_failed(monkeypatch):
    monkeypatch.setattr(sr, "time", _Clock())
    _docs(monkeypatch, [{"status": "failed", "n_chunks": 0, "error": "x"}])
    assert sr._await_matter_ready("s", "u", 180) is False


def test_await_ready_false_when_done_but_zero_chunks(monkeypatch):
    monkeypatch.setattr(sr, "time", _Clock())
    _docs(monkeypatch, [{"status": "done", "n_chunks": 0}])
    assert sr._await_matter_ready("s", "u", 180) is False


def test_await_ready_zero_timeout_disables_wait(monkeypatch):
    # 0 (or negative) timeout → no wait, returns False immediately.
    assert sr._await_matter_ready("s", "u", 0) is False


def test_matter_progress_sums_ingesting_docs(monkeypatch):
    _docs(
        monkeypatch,
        [
            {"status": "processing", "pages_done": 4, "pages_total": 10},
            {"status": "queued", "pages_done": 0, "pages_total": 6},
            {"status": "done", "pages_done": 12, "pages_total": 12},  # excluded
        ],
    )
    assert sr._matter_progress("s", "u") == (4, 16)


def test_matter_progress_no_docs(monkeypatch):
    _docs(monkeypatch, [])
    assert sr._matter_progress("s", "u") == (0, 0)


def test_build_turn_msgs_modes():
    # mode selection is the contract that must not drift between /query and
    # /query/stream (and the in-stream rebuild). msgs just needs to be built.
    for use_rag, use_contract, expected in [
        (True, True, "rag+contract"),
        (True, False, "rag"),
        (False, True, "contract"),
        (False, False, "chat"),
    ]:
        mode, msgs = sr._build_turn_msgs(use_rag, use_contract, "Was gilt?", [], [], [], "", "de")
        assert mode == expected
        assert isinstance(msgs, list)
        assert msgs
