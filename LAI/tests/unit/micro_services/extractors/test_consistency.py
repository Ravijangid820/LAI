"""Tests for :func:`ddiq.extractors.check_cross_doc_consistency`.

This extractor takes already-extracted ``sections`` / ``weas`` /
``parcels`` and asks the LLM to flag contradictions. The pure
fact-aggregation (turbine count, secured/not-secured tallies) is
verified by inspecting the prompt passed to ``llm_json``. The
parsing path is verified with staged LLM responses covering:

* clean array of inconsistencies
* dict-wrapped response (``inconsistencies`` / ``findings`` /
  ``data`` keys)
* malformed / empty / non-list returns
* severity normalization
* quantification carry-through

The extractor must NEVER raise on bad input — ``[]`` is the
fallback on any unrecoverable error.
"""

from __future__ import annotations

import json

from ddiq.extractors.consistency import check_cross_doc_consistency
from ddiq.models import (
    AusgabeblattRow,
    AusgabeblattSection,
    CadastralParcel,
    WEAStatus,
)


# ── Fixtures (local helpers) ─────────────────────────────────────────


def _section(title: str, rows: list[tuple[str, str, str]]) -> AusgabeblattSection:
    return AusgabeblattSection(
        id=title.lower().replace(" ", "_"),
        title=title,
        rows=[AusgabeblattRow(label=l, value=v, ampel=a) for l, v, a in rows],
    )


def _wea(name: str, status: str = "errichtet") -> WEAStatus:
    return WEAStatus(
        name=name, ampel="green", owner="op", parcel="12/4",
        contract="c", lat=53.0, lng=8.0, address="a",
        status_code=status,
    )


def _parcel(parcel_id: str, status: str = "secured") -> CadastralParcel:
    return CadastralParcel(
        id=parcel_id, parcelNumber=parcel_id, gemarkung="test", flur=1,
        polygon=[[53.0, 8.0]], status=status, owner="o", area=1000.0,
    )


# ── Tests ────────────────────────────────────────────────────────────


class TestPrompt:
    def test_prompt_carries_fact_aggregate(self, make_llm_json) -> None:
        """The aggregated facts dict (turbine count, parcel tally,
        section rows) is the LLM's working set — it must be in the
        prompt verbatim. A regression where the prompt is silently
        empty would make the LLM hallucinate inconsistencies
        unrelated to the actual data."""
        calls = make_llm_json([])
        check_cross_doc_consistency(
            sections=[_section("Overview", [("Status", "erteilt", "green")])],
            weas=[_wea("WEA-1"), _wea("WEA-2")],
            parcels=[_parcel("12/4"), _parcel("12/5", status="not_secured")],
            total_capacity_mw=14.5,
        )
        assert len(calls) == 1
        _, prompt = calls[0]
        # JSON dump appears inside the prompt; substring-check the
        # fact aggregate's expected leaves.
        assert '"wea_count": 2' in prompt
        assert '"parcel_count": 2' in prompt
        assert '"parcel_secured": 1' in prompt
        assert '"parcel_not_secured": 1' in prompt
        assert '"total_capacity_mw": 14.5' in prompt

    def test_prompt_includes_status_codes(self, make_llm_json) -> None:
        calls = make_llm_json([])
        check_cross_doc_consistency(
            sections=[],
            weas=[_wea("WEA-1", status="errichtet"), _wea("WEA-2", status="geplant")],
            parcels=[],
        )
        _, prompt = calls[0]
        assert '"errichtet"' in prompt
        assert '"geplant"' in prompt


class TestParsing:
    def test_clean_array_response(self, make_llm_json) -> None:
        make_llm_json([
            {
                "text": "Turbine count differs across BImSchG and Pachtvertrag",
                "severity": "red", "domain": "Permits",
                "legal_basis": "BImSchG §6",
                "recommended_action": "Reconcile sources",
            },
        ])
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        assert len(out) == 1
        assert out[0].kind == "cross_document"
        assert out[0].severity == "red"
        assert out[0].domain == "Permits"
        assert out[0].legal_basis == "BImSchG §6"

    def test_dict_wrapped_response_inconsistencies_key(self, make_llm_json) -> None:
        """Some prompts return the array wrapped as
        ``{"inconsistencies": [...]}`` instead of a bare array — the
        extractor accepts both shapes."""
        make_llm_json({"inconsistencies": [
            {"text": "x", "severity": "yellow"},
        ]})
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        assert len(out) == 1
        assert out[0].text == "x"

    def test_dict_wrapped_response_findings_key(self, make_llm_json) -> None:
        make_llm_json({"findings": [{"text": "x"}]})
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        assert len(out) == 1

    def test_dict_wrapped_response_data_key(self, make_llm_json) -> None:
        make_llm_json({"data": [{"text": "x"}]})
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        assert len(out) == 1

    def test_unrecognized_dict_returns_empty(self, make_llm_json) -> None:
        # Dict with no known wrapper key → falls back to ``[]`` → list
        # check fails → returns ``[]``.
        make_llm_json({"weird_key": [{"text": "x"}]})
        assert check_cross_doc_consistency(sections=[], weas=[], parcels=[]) == []

    def test_severity_normalization(self, make_llm_json) -> None:
        """Anything outside (red, yellow, green) coerces to ``yellow``
        so the UI ampel widget never has to handle an unknown state."""
        make_llm_json([
            {"text": "a", "severity": "critical"},  # unknown → yellow
            {"text": "b", "severity": "yellow"},
            {"text": "c", "severity": "red"},
            {"text": "d", "severity": "green"},
            {"text": "e"},  # missing → yellow
        ])
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        assert [f.severity for f in out] == ["yellow", "yellow", "red", "green", "yellow"]

    def test_quantification_attached(self, make_llm_json) -> None:
        make_llm_json([
            {
                "text": "Bond too small",
                "severity": "red",
                "quantification": {
                    "mw_affected": 12.6,
                    "eur_impact_estimate": 250000.0,
                    "rationale": "80k/MW shortfall",
                },
            },
        ])
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        assert out[0].quantification is not None
        assert out[0].quantification.mw_affected == 12.6
        assert out[0].quantification.eur_impact_estimate == 250000.0
        assert out[0].quantification.rationale == "80k/MW shortfall"

    def test_quantification_dropped_when_all_null(self, make_llm_json) -> None:
        """The model often returns ``{"mw_affected": null, ...}`` for
        narrow textual findings — drop the whole object so the UI
        doesn't render an empty card."""
        make_llm_json([
            {
                "text": "narrow text-only finding",
                "quantification": {
                    "mw_affected": None,
                    "eur_impact_estimate": None,
                    "days_until_deadline": None,
                    "rationale": "n/a",
                },
            },
        ])
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        assert out[0].quantification is None


class TestErrorPaths:
    def test_empty_text_dropped(self, make_llm_json) -> None:
        make_llm_json([
            {"text": "", "severity": "red"},
            {"text": "real finding", "severity": "red"},
        ])
        out = check_cross_doc_consistency(sections=[], weas=[], parcels=[])
        # Only the non-empty one survives.
        assert len(out) == 1
        assert out[0].text == "real finding"

    def test_llm_json_returns_string_returns_empty(self, make_llm_json) -> None:
        make_llm_json("not a list")
        assert check_cross_doc_consistency(sections=[], weas=[], parcels=[]) == []

    def test_llm_json_raises_returns_empty(self, monkeypatch) -> None:
        """The whole function is wrapped in ``try / except Exception``
        for the production-grade contract: no extractor failure is
        allowed to kill the orchestrator."""
        import ddiq.extractors.consistency as cons
        def boom(*a, **kw):
            raise RuntimeError("model crashed")
        monkeypatch.setattr(cons, "llm_json", boom)
        assert check_cross_doc_consistency(sections=[], weas=[], parcels=[]) == []
