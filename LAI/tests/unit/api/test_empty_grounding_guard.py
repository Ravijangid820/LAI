"""Unit tests for serve_rag._empty_grounding_guard.

The guard stops a doc-scoped chat turn from being answered with LLM
boilerplate when there are no grounded sources, and tells the user WHY:
still processing (with page count), ingestion failed (with reason), or
indexed-but-nothing-relevant. No LLM / DB — persistence.list_matter_documents
is monkeypatched. Statuses mirror persistence: queued/processing/done/failed.
"""

from __future__ import annotations

import os

os.environ.setdefault("LAI_AUTH_JWT_ACCESS_SECRET", "test-secret-guard-unit-0123456789abcdef")

from lai.api import serve_rag as sr

DUMMY = [object()]  # a non-empty source list (truthiness is all the guard checks)


def _set_docs(monkeypatch, docs):
    monkeypatch.setattr(sr.persistence, "list_matter_documents", lambda sid, user_id=None: docs)


# ── modes that must never guard ──────────────────────────────────────────
def test_chat_mode_never_guards():
    assert sr._empty_grounding_guard("chat", [], [], "s", "u", "de") is None


def test_pure_corpus_rag_mode_not_guarded():
    assert sr._empty_grounding_guard("rag", [], [], "s", "u", "de") is None


# ── sources present → generate normally ──────────────────────────────────
def test_contract_with_matter_sources_generates():
    assert sr._empty_grounding_guard("contract", DUMMY, DUMMY, "s", "u", "de") is None


def test_ragcontract_with_corpus_only_still_generates():
    assert sr._empty_grounding_guard("rag+contract", [], DUMMY, "s", "u", "de") is None


# ── 1. still processing → progress message ───────────────────────────────
def test_processing_returns_progress_de(monkeypatch):
    _set_docs(monkeypatch, [{"status": "processing", "n_chunks": 0, "pages_done": 3, "pages_total": 10}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg and "wird gerade verarbeitet" in msg and "3/10" in msg


def test_processing_returns_progress_en(monkeypatch):
    _set_docs(monkeypatch, [{"status": "processing", "n_chunks": 0, "pages_done": 4, "pages_total": 10}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "en")
    assert msg and "still being processed" in msg and "4/10" in msg


def test_queued_zero_chunks_is_processing(monkeypatch):
    _set_docs(monkeypatch, [{"status": "queued", "n_chunks": 0, "pages_done": 0, "pages_total": 0}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg and "verarbeitet" in msg
    # no page-count parenthetical when total is unknown yet (the explanatory
    # "Seite für Seite" phrase is still present, but not "(Seite X/Y)")
    assert "(Seite" not in msg


def test_processing_takes_priority_over_failed(monkeypatch):
    # one doc failed, another still processing → tell the user to wait
    _set_docs(
        monkeypatch,
        [
            {"status": "failed", "n_chunks": 0, "error": "boom"},
            {"status": "processing", "n_chunks": 0, "pages_done": 2, "pages_total": 9},
        ],
    )
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg and "verarbeitet" in msg and "2/9" in msg


# ── 2. failed (and nothing usable) → honest failure + reason ─────────────
def test_failed_status_returns_failure_with_reason_de(monkeypatch):
    _set_docs(monkeypatch, [{"status": "failed", "n_chunks": 0, "error": "pdftoppm render error"}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg and "konnte nicht verarbeitet werden" in msg
    assert "pdftoppm render error" in msg
    assert "verarbeitet wird" not in msg  # not the processing wording


def test_done_but_zero_chunks_is_failure(monkeypatch):
    # the live "ALT F III" case: status done but extraction produced nothing
    _set_docs(monkeypatch, [{"status": "done", "n_chunks": 0, "pages_done": 21, "pages_total": 21}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "en")
    assert msg and "couldn’t process your document" in msg


def test_failed_with_no_error_omits_reason(monkeypatch):
    _set_docs(monkeypatch, [{"status": "failed", "n_chunks": 0, "error": None}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg and "konnte nicht verarbeitet werden" in msg and "Grund:" not in msg


# ── 3. usable docs exist but query found nothing → "no relevant passage" ──
def test_indexed_with_chunks_returns_no_content(monkeypatch):
    _set_docs(monkeypatch, [{"status": "done", "n_chunks": 72, "pages_done": 10, "pages_total": 10}])
    msg = sr._empty_grounding_guard("contract", [], [], "s", "u", "de")
    assert msg and "keine relevante" in msg
    assert "verarbeitet" not in msg and "konnte nicht" not in msg


def test_usable_doc_plus_failed_doc_prefers_no_content(monkeypatch):
    # one good doc + one failed doc, retrieval empty → there IS content,
    # so don't claim the upload failed; say nothing relevant was found.
    _set_docs(
        monkeypatch,
        [
            {"status": "done", "n_chunks": 72, "pages_done": 10, "pages_total": 10},
            {"status": "failed", "n_chunks": 0, "error": "boom"},
        ],
    )
    msg = sr._empty_grounding_guard("rag+contract", [], [], "s", "u", "de")
    assert msg and "keine relevante" in msg
    assert "konnte nicht verarbeitet" not in msg
