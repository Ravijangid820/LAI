"""Unit tests for serve_rag._empty_grounding_guard.

The guard is what stops a doc-scoped chat turn from being answered with
LLM boilerplate when there are no grounded sources (the verified failure:
a freshly-uploaded scan answered before its OCR/indexing finished). No
LLM / DB — persistence.list_matter_documents is monkeypatched.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-guard-unit-0123456789abcdef"
)

from lai.api import serve_rag as sr  # noqa: E402

DUMMY = [object()]  # a non-empty source list (truthiness is all the guard checks)


def _set_docs(monkeypatch, docs):
    monkeypatch.setattr(
        sr.persistence, "list_matter_documents", lambda sid, user_id=None: docs
    )


def test_chat_mode_never_guards():
    assert sr._empty_grounding_guard("chat", [], [], "s", "u", "de") is None


def test_pure_corpus_rag_mode_not_guarded():
    # A corpus-only turn (no Matter) is intentionally left alone.
    assert sr._empty_grounding_guard("rag", [], [], "s", "u", "de") is None


def test_contract_with_matter_sources_generates():
    assert sr._empty_grounding_guard("contract", DUMMY, DUMMY, "s", "u", "de") is None


def test_ragcontract_with_corpus_only_still_generates():
    # matter empty but corpus present → rag_sources non-empty → grounded.
    assert sr._empty_grounding_guard("rag+contract", [], DUMMY, "s", "u", "de") is None


def test_contract_empty_while_processing_returns_progress_de(monkeypatch):
    _set_docs(monkeypatch, [{"status": "processing", "n_chunks": 0,
                             "pages_done": 3, "pages_total": 10}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg is not None
    assert "wird noch verarbeitet" in msg and "3/10" in msg


def test_contract_empty_while_processing_returns_progress_en(monkeypatch):
    _set_docs(monkeypatch, [{"status": "processing", "n_chunks": 0,
                             "pages_done": 4, "pages_total": 10}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "en")
    assert msg is not None
    assert "still being processed" in msg and "4/10" in msg


def test_contract_empty_when_indexed_returns_no_content(monkeypatch):
    # Doc fully indexed but retrieval found nothing → "no relevant passage",
    # NOT a "still processing" message, and NOT an LLM answer.
    _set_docs(monkeypatch, [{"status": "done", "n_chunks": 72,
                             "pages_done": 10, "pages_total": 10}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg is not None
    assert "keine relevante" in msg
    assert "verarbeitet" not in msg


def test_ragcontract_empty_everything_guards(monkeypatch):
    _set_docs(monkeypatch, [{"status": "done", "n_chunks": 72,
                             "pages_done": 10, "pages_total": 10}])
    msg = sr._empty_grounding_guard("rag+contract", [], [], "s", "u", "de")
    assert msg is not None and "keine relevante" in msg


def test_chunks_present_but_status_lagging_treated_as_processing(monkeypatch):
    # n_chunks==0 means not searchable yet even if status isn't 'done'.
    _set_docs(monkeypatch, [{"status": "queued", "n_chunks": 0,
                             "pages_done": 0, "pages_total": 10}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg is not None and "verarbeitet" in msg
