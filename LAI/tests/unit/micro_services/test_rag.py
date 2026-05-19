"""Tests for :mod:`ddiq.rag`.

:func:`evidence_from_chunks` is pure transformation — covered with
fixed inputs.

:func:`rerank` is covered by stubbing :func:`requests.post`; the
fallback-on-failure behaviour is the production-readiness contract.

:func:`rag_context` / :func:`rag_context_with_meta` orchestrate
``embed_single → search_doc_chunks → rerank`` — the integration
points are stubbed so the test verifies the wiring (which
arguments flow where, what each helper returns on each path).

:func:`search_doc_chunks` + :func:`get_all_text_for_docs` need a
real Postgres + pgvector instance to exercise meaningfully (they
build raw SQL with ``vector(4096)`` casts) and are covered by the
integration suite; here we only exercise the empty-input fast
paths.
"""

from __future__ import annotations

from typing import Any

import pytest

import ddiq.rag as ddiq_rag
from ddiq.models import Evidence


# ── evidence_from_chunks ─────────────────────────────────────────────


class TestEvidenceFromChunks:
    def test_resolves_one_indexed_indices(self, evidence_chunks) -> None:
        ev = ddiq_rag.evidence_from_chunks(evidence_chunks, [1, 3])
        assert len(ev) == 2
        assert ev[0].doc_id == "doc-A"
        assert ev[1].doc_id == "doc-C"

    def test_tolerates_string_indices(self, evidence_chunks) -> None:
        """The LLM sometimes returns ``"1"`` or ``"#1"`` or
        ``"chunk_1"`` instead of an integer — we strip non-digits
        and re-parse so the citation still resolves."""
        ev = ddiq_rag.evidence_from_chunks(evidence_chunks, ["1", "#2", "chunk_3"])
        assert [e.doc_id for e in ev] == ["doc-A", "doc-B", "doc-C"]

    def test_truncates_excerpt_to_300(self, evidence_chunks) -> None:
        """The Evidence excerpt is a UI snippet, hard-capped at 300
        chars so a long chunk doesn't blow up the report payload."""
        chunks = [{"doc_id": "X", "filename": "f", "text": "abc " * 200}]
        ev = ddiq_rag.evidence_from_chunks(chunks, [1])
        assert len(ev) == 1
        assert len(ev[0].excerpt) == 300

    def test_drops_out_of_range_indices(self, evidence_chunks) -> None:
        """Index 99 with 3 chunks → silently dropped, not an error.
        The LLM hallucinating a citation index must not raise."""
        ev = ddiq_rag.evidence_from_chunks(evidence_chunks, [1, 99, -5, 0])
        # 1 is valid; 99, -5, 0 are all out of range (1-based).
        assert len(ev) == 1
        assert ev[0].doc_id == "doc-A"

    def test_drops_unparseable_indices(self, evidence_chunks) -> None:
        # An empty-string index returns int('') which raises, but the
        # function swallows that with a generic except.
        ev = ddiq_rag.evidence_from_chunks(evidence_chunks, [None, "", "abc"])
        # None → str(None)="None" → strip non-digits → "" → int("") → raise → skip.
        # "" → same. "abc" → "" → same. All three drop.
        assert ev == []

    def test_handles_empty_inputs(self) -> None:
        assert ddiq_rag.evidence_from_chunks([], [1, 2]) == []
        assert ddiq_rag.evidence_from_chunks([{"doc_id": "X", "text": "t"}], None) == []  # type: ignore[arg-type]
        assert ddiq_rag.evidence_from_chunks([{"doc_id": "X", "text": "t"}], []) == []

    def test_missing_doc_id_yields_none(self) -> None:
        chunks = [{"filename": "f", "text": "t"}]  # no doc_id
        ev = ddiq_rag.evidence_from_chunks(chunks, [1])
        assert len(ev) == 1
        assert ev[0].doc_id is None
        assert ev[0].doc_filename == "f"

    def test_returns_evidence_objects(self, evidence_chunks) -> None:
        ev = ddiq_rag.evidence_from_chunks(evidence_chunks, [1])
        assert isinstance(ev[0], Evidence)


# ── rerank ───────────────────────────────────────────────────────────


class _StubResponse:
    def __init__(self, json_body: Any, status: int = 200) -> None:
        self._json = json_body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._json


class TestRerank:
    def test_reorders_by_score(self, monkeypatch) -> None:
        chunks = [
            {"text": "alpha"},
            {"text": "beta"},
            {"text": "gamma"},
        ]
        # Reranker returns indices in arbitrary order with scores.
        body = [
            {"index": 2, "score": 0.9},  # gamma — best
            {"index": 0, "score": 0.5},  # alpha — second
            {"index": 1, "score": 0.1},  # beta — worst
        ]
        captured: dict[str, Any] = {}

        def fake_post(url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _StubResponse(body)

        monkeypatch.setattr("ddiq.rag.requests.post", fake_post)
        out = ddiq_rag.rerank("query text", chunks, top_k=2)
        # Two highest-scored chunks, in score order.
        assert out == [chunks[2], chunks[0]]
        assert "/rerank" in captured["url"]
        assert captured["json"]["query"] == "query text"
        assert captured["json"]["texts"] == ["alpha", "beta", "gamma"]
        assert captured["json"]["truncate"] is True

    def test_falls_back_on_http_error(self, monkeypatch) -> None:
        """Reranker outage → just return the top-k chunks unchanged.
        Losing the rerank step is acceptable; crashing the whole
        report mid-pipeline is not."""
        chunks = [{"text": f"chunk-{i}"} for i in range(5)]

        def boom(url, json=None, timeout=None):
            return _StubResponse({}, status=500)

        monkeypatch.setattr("ddiq.rag.requests.post", boom)
        out = ddiq_rag.rerank("q", chunks, top_k=3)
        assert out == chunks[:3]

    def test_falls_back_on_transport_error(self, monkeypatch) -> None:
        import requests as _req

        def transport_die(url, json=None, timeout=None):
            raise _req.ConnectionError("dns")

        monkeypatch.setattr("ddiq.rag.requests.post", transport_die)
        chunks = [{"text": "x"}, {"text": "y"}]
        assert ddiq_rag.rerank("q", chunks, top_k=1) == [chunks[0]]


# ── rag_context / rag_context_with_meta ──────────────────────────────


class TestRagContext:
    def _stub_pipeline(self, monkeypatch, chunks: list[dict[str, Any]]):
        """Stub embed_single → search_doc_chunks → rerank so the
        function-under-test sees the controlled chunk list."""
        embed_calls: list[str] = []
        search_calls: list[tuple[list[str], list[float], int]] = []
        rerank_calls: list[tuple[str, int]] = []

        def fake_embed(text: str) -> list[float]:
            embed_calls.append(text)
            return [0.0] * 16

        def fake_search(doc_ids, query_embedding, top_k=15, user_id=None):
            search_calls.append((list(doc_ids), list(query_embedding), top_k))
            return chunks

        def fake_rerank(query, c, top_k=5):
            rerank_calls.append((query, top_k))
            return c[:top_k]

        monkeypatch.setattr(ddiq_rag, "embed_single", fake_embed)
        monkeypatch.setattr(ddiq_rag, "search_doc_chunks", fake_search)
        monkeypatch.setattr(ddiq_rag, "rerank", fake_rerank)
        return embed_calls, search_calls, rerank_calls

    def test_rag_context_returns_joined_text(self, monkeypatch, evidence_chunks) -> None:
        self._stub_pipeline(monkeypatch, evidence_chunks)
        out = ddiq_rag.rag_context(["d1"], "BImSchG?", top_k=2)
        assert "[Doc: BImSchG-Bescheid.pdf]" in out
        assert "[Doc: Pachtvertrag-Flur-12.pdf]" in out
        assert "Bürgschaft" not in out  # third chunk dropped by top_k=2

    def test_rag_context_handles_no_chunks(self, monkeypatch) -> None:
        self._stub_pipeline(monkeypatch, [])
        out = ddiq_rag.rag_context(["d1"], "x")
        assert out == "(No relevant content found)"

    def test_rag_context_with_meta_returns_chunks(self, monkeypatch, evidence_chunks) -> None:
        self._stub_pipeline(monkeypatch, evidence_chunks)
        text, chunks = ddiq_rag.rag_context_with_meta(["d1"], "q", top_k=2)
        # The numbered [#1] prefix is how the LLM cites back evidence.
        assert "[#1 | Doc: BImSchG-Bescheid.pdf]" in text
        assert "[#2 | Doc: Pachtvertrag-Flur-12.pdf]" in text
        assert len(chunks) == 2

    def test_rag_context_with_meta_empty_returns_tuple(self, monkeypatch) -> None:
        self._stub_pipeline(monkeypatch, [])
        text, chunks = ddiq_rag.rag_context_with_meta(["d1"], "q")
        assert text == "(No relevant content found)"
        assert chunks == []

    def test_truncates_chunk_text_at_800(self, monkeypatch) -> None:
        long_chunks = [{"doc_id": "X", "filename": "f", "text": "x" * 2000}]
        self._stub_pipeline(monkeypatch, long_chunks)
        out = ddiq_rag.rag_context(["d1"], "q", top_k=1)
        # The chunk body must be capped — full 2000 chars would blow
        # up the prompt size when N chunks are joined.
        assert len(out) < 1000


# ── DB-touching functions — empty-input paths only ───────────────────


def test_search_doc_chunks_empty_doc_ids_returns_empty() -> None:
    """The function short-circuits on ``doc_ids=[]`` BEFORE touching
    the DB pool, so this works in pure-unit mode."""
    assert ddiq_rag.search_doc_chunks([], [0.0] * 16) == []


@pytest.mark.parametrize("user_id", [None, "u-1"])
def test_search_doc_chunks_empty_with_user_id(user_id) -> None:
    assert ddiq_rag.search_doc_chunks([], [0.0] * 16, user_id=user_id) == []
