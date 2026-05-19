"""Tests for :func:`ddiq.extractors.extract_timeline`.

Timeline extraction takes a question via :func:`rag_context_with_meta`
and asks the LLM to return date-bound entries. The extractor tags each
entry with ``days_from_now`` + ``urgency`` based on TODAY — the
mid-pipeline date arithmetic is its main correctness contract.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import ddiq.extractors.timeline as timeline_mod
from ddiq.extractors.timeline import extract_timeline


@pytest.fixture
def stub_rag(monkeypatch):
    """Stub :func:`rag_context_with_meta` so the extractor doesn't try
    to embed / search / rerank. Returns a fixed (context, reranked)
    pair; tests don't care about the context body — only that the
    function reaches ``llm_json``."""
    def fake(doc_ids, question, top_k=5):
        return ("(stub context)", [
            {"doc_id": "d1", "filename": "permit.pdf", "text": "..."},
        ])
    monkeypatch.setattr(timeline_mod, "rag_context_with_meta", fake)


def test_returns_empty_when_no_timeline_found(make_llm_json, stub_rag) -> None:
    make_llm_json([])
    out = extract_timeline(doc_ids=["d1"], full_text="")
    assert out == []


def test_parses_clean_entries(make_llm_json, stub_rag) -> None:
    far_future = (date.today() + timedelta(days=500)).isoformat()
    make_llm_json([
        {
            "kind": "permit_expiry",
            "date": far_future,
            "description": "BImSchG permit expires",
            "legal_basis": "BImSchG §6",
            "evidence_chunks": [1],
        },
    ])
    out = extract_timeline(doc_ids=["d1"], full_text="")
    assert len(out) == 1
    assert out[0].kind == "permit_expiry"
    assert out[0].date == far_future
    assert out[0].urgency == "future"
    assert out[0].days_from_now is not None
    assert out[0].days_from_now > 180


def test_urgency_buckets(make_llm_json, stub_rag) -> None:
    """The urgency tag is the field the UI sorts by — every bucket
    must map to its expected band."""
    today = date.today()
    make_llm_json([
        {"kind": "expired", "date": (today - timedelta(days=10)).isoformat(),
         "description": "past due"},
        {"kind": "urgent", "date": (today + timedelta(days=15)).isoformat(),
         "description": "soon"},
        {"kind": "soon", "date": (today + timedelta(days=60)).isoformat(),
         "description": "two months"},
        {"kind": "future", "date": (today + timedelta(days=400)).isoformat(),
         "description": "next year"},
    ])
    out = extract_timeline(doc_ids=["d1"], full_text="")
    urgencies = [t.urgency for t in out]
    # Sort order: ascending by days_from_now → expired, urgent, soon, future.
    assert urgencies == ["expired", "urgent", "soon", "future"]


def test_non_iso_date_passes_through_without_urgency(make_llm_json, stub_rag) -> None:
    """Free-text dates (e.g. "Q4 2026") survive but get no urgency
    band — better to keep the row visible than drop it."""
    make_llm_json([
        {"kind": "construction_milestone", "date": "Q4 2026",
         "description": "Tower delivery"},
    ])
    out = extract_timeline(doc_ids=["d1"], full_text="")
    assert len(out) == 1
    assert out[0].date == "Q4 2026"
    assert out[0].urgency is None
    assert out[0].days_from_now is None


def test_empty_date_drops_row(make_llm_json, stub_rag) -> None:
    """A timeline entry without a date is useless; drop rather than
    surface a date-less row in the UI."""
    make_llm_json([
        {"kind": "x", "date": "", "description": "no date"},
        {"kind": "y", "date": (date.today() + timedelta(days=10)).isoformat(),
         "description": "real"},
    ])
    out = extract_timeline(doc_ids=["d1"], full_text="")
    assert len(out) == 1
    assert out[0].description == "real"


def test_dict_wrapped_response(make_llm_json, stub_rag) -> None:
    make_llm_json({"timeline": [
        {"kind": "x", "date": (date.today() + timedelta(days=1)).isoformat(),
         "description": "wrapped"},
    ]})
    out = extract_timeline(doc_ids=["d1"], full_text="")
    assert len(out) == 1


def test_string_response_returns_empty(make_llm_json, stub_rag) -> None:
    make_llm_json("not a list")
    assert extract_timeline(doc_ids=["d1"], full_text="") == []


def test_evidence_chunks_resolved(make_llm_json, stub_rag) -> None:
    make_llm_json([
        {"kind": "x", "date": (date.today() + timedelta(days=1)).isoformat(),
         "description": "x", "evidence_chunks": [1]},
    ])
    out = extract_timeline(doc_ids=["d1"], full_text="")
    assert len(out[0].evidence) == 1
    assert out[0].evidence[0].doc_id == "d1"


def test_llm_raise_returns_empty(monkeypatch, stub_rag) -> None:
    def boom(*a, **kw):
        raise RuntimeError("transport")
    monkeypatch.setattr(timeline_mod, "llm_json", boom)
    assert extract_timeline(doc_ids=["d1"], full_text="") == []
