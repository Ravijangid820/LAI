"""Tests for :func:`ddiq.extractors.extract_rueckbau_bond` and
:func:`ddiq.extractors.check_grundbuch_match`.

Both extractors share the RAG-then-LLM-json pattern; the tests focus
on the path-specific behaviour:

* :func:`extract_rueckbau_bond`:
  - Returns :class:`RueckbauBond` with note='not found' when every
    field is null (the "absence is real, not a bug" contract).
  - Returns ``None`` only on transport failure / non-dict response.

* :func:`check_grundbuch_match`:
  - Empty parcel list → ``[]`` without calling the LLM.
  - Only secured + normalised parcels are sent.
  - The 25-parcel cap is honoured.
"""

from __future__ import annotations

import pytest

import ddiq.extractors.grundbuch as grundbuch_mod
import ddiq.extractors.rueckbau as rueckbau_mod
from ddiq.extractors.grundbuch import check_grundbuch_match
from ddiq.extractors.rueckbau import extract_rueckbau_bond
from ddiq.models import CadastralParcel, RueckbauBond


@pytest.fixture
def stub_rag(monkeypatch):
    """Pre-patch RAG in BOTH extractor modules so the tests don't
    touch the live embedding / search / rerank chain."""
    def fake(doc_ids, question, top_k=5):
        return ("(stub context)", [
            {"doc_id": "d1", "filename": "doc.pdf", "text": "..."},
        ])
    monkeypatch.setattr(rueckbau_mod, "rag_context_with_meta", fake)
    monkeypatch.setattr(grundbuch_mod, "rag_context_with_meta", fake)


def _parcel(id_: str, status: str = "secured", normalised: str = "test:1:12") -> CadastralParcel:
    return CadastralParcel(
        id=id_, parcelNumber="12/4", gemarkung="test", flur=1,
        polygon=[[53.0, 8.0]], status=status, owner="o", area=1000.0,
        contractRef="contract-1", normalizedId=normalised,
    )


# ── extract_rueckbau_bond ────────────────────────────────────────────


class TestRueckbau:
    def test_extracts_full_bond(self, make_llm_json, stub_rag) -> None:
        make_llm_json({
            "amount_eur": 250000.0,
            "provider": "Sparkasse",
            "beneficiary": "Gemeinde Test",
            "valid_until": "2030-01-01",
            "instrument_type": "Bürgschaft",
            "sufficient": True,
            "note": "Sufficient at 80k/MW",
            "evidence_chunks": [1],
        })
        out = extract_rueckbau_bond(doc_ids=["d1"])
        assert isinstance(out, RueckbauBond)
        assert out.amount_eur == 250000.0
        assert out.provider == "Sparkasse"
        assert out.beneficiary == "Gemeinde Test"
        assert out.valid_until == "2030-01-01"
        assert out.instrument_type == "Bürgschaft"
        assert out.sufficient is True
        assert len(out.evidence) == 1

    def test_not_found_returns_placeholder(self, make_llm_json, stub_rag) -> None:
        """All fields null → returns a :class:`RueckbauBond` with
        just a ``note`` so the UI shows "Rückbaubürgschaft not found"
        instead of silently omitting the section."""
        make_llm_json({
            "amount_eur": None,
            "provider": None,
            "valid_until": None,
            "instrument_type": None,
            "note": "Document set does not mention Rückbau",
        })
        out = extract_rueckbau_bond(doc_ids=["d1"])
        assert out is not None
        assert out.amount_eur is None
        assert out.note == "Document set does not mention Rückbau"

    def test_not_found_uses_default_note_when_missing(self, make_llm_json, stub_rag) -> None:
        make_llm_json({
            "amount_eur": None, "provider": None,
            "valid_until": None, "instrument_type": None,
        })
        out = extract_rueckbau_bond(doc_ids=["d1"])
        assert out is not None
        assert "not found" in (out.note or "").lower()

    def test_non_dict_returns_none(self, make_llm_json, stub_rag) -> None:
        """Bare list / string → ``None`` (distinct from the
        not-found placeholder)."""
        make_llm_json([1, 2, 3])
        assert extract_rueckbau_bond(doc_ids=["d1"]) is None

    def test_llm_raise_returns_none(self, monkeypatch, stub_rag) -> None:
        def boom(*a, **kw):
            raise RuntimeError("transport")
        monkeypatch.setattr(rueckbau_mod, "llm_json", boom)
        assert extract_rueckbau_bond(doc_ids=["d1"]) is None


# ── check_grundbuch_match ────────────────────────────────────────────


class TestGrundbuch:
    def test_empty_parcels_returns_empty(self, make_llm_json, stub_rag) -> None:
        calls = make_llm_json([])
        assert check_grundbuch_match(doc_ids=["d1"], parcels=[]) == []
        # No LLM call should fire when there's nothing to check.
        assert calls == []

    def test_unsecured_parcels_skipped(self, make_llm_json, stub_rag) -> None:
        """The LLM only sees secured parcels — running Grundbuch on
        unsecured ones is wasted; the contract logic ALREADY says
        they're missing."""
        calls = make_llm_json([])
        check_grundbuch_match(
            doc_ids=["d1"],
            parcels=[_parcel("p1", status="not_secured")],
        )
        # Only unsecured parcel → no LLM call.
        assert calls == []

    def test_missing_normalised_id_skipped(self, make_llm_json, stub_rag) -> None:
        calls = make_llm_json([])
        check_grundbuch_match(
            doc_ids=["d1"],
            parcels=[_parcel("p1", normalised="")],
        )
        assert calls == []

    def test_caps_at_25_parcels(self, make_llm_json, stub_rag) -> None:
        """The most expensive single LLM call in the pipeline; the
        25-parcel cap is the per-call ceiling."""
        parcels = [_parcel(f"p{i}", normalised=f"test:1:{i}") for i in range(40)]
        calls = make_llm_json([])
        check_grundbuch_match(doc_ids=["d1"], parcels=parcels)
        # One LLM call (not 40 — they go in one prompt). And the
        # serialised parcel list in the prompt body must reference at
        # most 25 distinct ids. Inspect the first 25 (kept) and the
        # 26th (must NOT appear) — substring-checking individual ids
        # is more robust than counting because the prompt template
        # also contains the literal schema example ``"parcel_id":"..."``.
        assert len(calls) == 1
        _, prompt = calls[0]
        # The first 25 normalised ids are ``test:1:0`` … ``test:1:24``.
        for i in range(25):
            assert f'"test:1:{i}"' in prompt, f"id {i} missing from prompt"
        # 26th and beyond were truncated.
        for i in range(25, 40):
            assert f'"test:1:{i}"' not in prompt, f"id {i} leaked past cap"

    def test_parses_clean_response(self, make_llm_json, stub_rag) -> None:
        make_llm_json([
            {
                "parcel_id": "bremen:1:12_4",
                "registered_owner": "Müller GmbH",
                "lessor_name": "Müller GmbH",
                "owner_match": True,
                "match_confidence": 0.95,
                "encumbrances": ["Wegerecht zugunsten Gemeinde"],
                "note": "match",
                "evidence_chunks": [1],
            },
        ])
        out = check_grundbuch_match(
            doc_ids=["d1"],
            parcels=[_parcel("p1", normalised="bremen:1:12_4")],
        )
        assert len(out) == 1
        assert out[0].parcel_id == "bremen:1:12_4"
        assert out[0].owner_match is True
        assert out[0].match_confidence == 0.95
        assert out[0].encumbrances == ["Wegerecht zugunsten Gemeinde"]

    def test_confidence_invalid_float_defaults_zero(self, make_llm_json, stub_rag) -> None:
        make_llm_json([
            {"parcel_id": "x", "match_confidence": "high"},  # str → float fails
        ])
        out = check_grundbuch_match(
            doc_ids=["d1"], parcels=[_parcel("p1")],
        )
        assert out[0].match_confidence == 0.0

    def test_empty_parcel_id_dropped(self, make_llm_json, stub_rag) -> None:
        make_llm_json([
            {"parcel_id": "", "owner_match": True},
            {"parcel_id": "real", "owner_match": False},
        ])
        out = check_grundbuch_match(doc_ids=["d1"], parcels=[_parcel("p1")])
        assert len(out) == 1
        assert out[0].parcel_id == "real"

    def test_dict_wrapped_response(self, make_llm_json, stub_rag) -> None:
        make_llm_json({"checks": [{"parcel_id": "x"}]})
        out = check_grundbuch_match(doc_ids=["d1"], parcels=[_parcel("p1")])
        assert len(out) == 1

    def test_llm_raise_returns_empty(self, monkeypatch, stub_rag) -> None:
        def boom(*a, **kw):
            raise RuntimeError("transport")
        monkeypatch.setattr(grundbuch_mod, "llm_json", boom)
        assert check_grundbuch_match(doc_ids=["d1"], parcels=[_parcel("p1")]) == []
