"""Tests for :mod:`ddiq.extractors.findings`.

The findings chapter is built one entry at a time (Track A item 2):
for every red/yellow row in the analysed sections, one dedicated
LLM call returns a single Finding. The per-row design is the
production-readiness contract: if the LLM emits malformed JSON for
one issue, only that issue degrades to a placeholder; the rest of
the chapter is unaffected.

These tests verify:

* The prompt builder serialises the issue dict cleanly (no
  f-string brace collisions, capacity hint included only when
  given).
* :func:`_finding_from_llm_obj` filters bad shapes (non-dict,
  missing text, list with single-element fallback).
* :func:`_placeholder_finding_for_issue` carries section + label
  so the lawyer can find the source row.
* :func:`generate_findings` produces the green "no issues" finding
  when nothing is flagged, hits the placeholder path when the LLM
  returns garbage, and does NOT crash when ``llm_json`` raises.
"""

from __future__ import annotations

from ddiq.extractors.findings import (
    _finding_from_llm_obj,
    _findings_prompt_for_issue,
    _placeholder_finding_for_issue,
    generate_findings,
)
from ddiq.models import (
    AusgabeblattRow,
    AusgabeblattSection,
    Evidence,
    Finding,
)


# ── Local helpers ────────────────────────────────────────────────────


def _row(label: str, value: str, ampel: str) -> AusgabeblattRow:
    return AusgabeblattRow(label=label, value=value, ampel=ampel)


def _section_with_flag(label: str, ampel: str = "red") -> AusgabeblattSection:
    """One section with one flagged row."""
    return AusgabeblattSection(
        id="s1", title="Permits",
        rows=[
            _row(label="OK row", value="erteilt", ampel="green"),
            _row(label=label, value="ungültig", ampel=ampel),
        ],
    )


# ── _findings_prompt_for_issue ───────────────────────────────────────


class TestFindingsPrompt:
    def test_includes_issue_json(self) -> None:
        prompt = _findings_prompt_for_issue(
            {"section": "Permits", "label": "Status", "value": "ungültig"},
        )
        # The issue dict is serialised verbatim so the LLM has the
        # row it's drafting for. Substring-check the key fields.
        assert '"section": "Permits"' in prompt
        assert '"label": "Status"' in prompt
        assert '"value": "ungültig"' in prompt

    def test_capacity_hint_only_when_given(self) -> None:
        prompt_no_cap = _findings_prompt_for_issue({"label": "x"})
        prompt_with_cap = _findings_prompt_for_issue({"label": "x"}, total_capacity_mw=12.6)
        assert "total capacity" not in prompt_no_cap.lower()
        assert "12.6 MW" in prompt_with_cap

    def test_capacity_zero_does_not_show(self) -> None:
        """Falsy total → no hint (the orchestrator passes 0 or None
        when no capacity figure was extracted)."""
        prompt = _findings_prompt_for_issue({"label": "x"}, total_capacity_mw=0)
        assert "MW" not in prompt or "total capacity" not in prompt.lower()


# ── _finding_from_llm_obj ────────────────────────────────────────────


class TestFindingFromLlmObj:
    def test_happy_path(self) -> None:
        obj = {
            "domain": "Permits",
            "severity": "red",
            "text": "Permit expired",
            "legal_basis": "BImSchG §6",
            "recommended_action": "Renew",
            "quantification": {
                "mw_affected": 4.2,
                "eur_impact_estimate": 2_800_000.0,
                "days_until_deadline": -10,
                "rationale": "Already expired",
            },
        }
        f = _finding_from_llm_obj(obj, source_issue={})
        assert isinstance(f, Finding)
        assert f.domain == "Permits"
        assert f.severity == "red"
        assert f.legal_basis == "BImSchG §6"
        assert f.quantification is not None
        assert f.quantification.mw_affected == 4.2
        assert f.kind == "section"

    def test_evidence_pulled_from_source_issue(self) -> None:
        """Evidence isn't requested from the LLM — it's attached from
        the source issue (which carries the per-row ``_evidence``
        anchors set during section analysis)."""
        issue = {
            "evidence": [
                {"doc_id": "d1", "doc_filename": "f1.pdf", "excerpt": "snippet"},
                {"doc_id": "d2"},
            ],
        }
        f = _finding_from_llm_obj({"text": "x"}, source_issue=issue)
        assert f is not None
        assert len(f.evidence) == 2
        assert f.evidence[0].doc_id == "d1"
        assert f.evidence[1].doc_id == "d2"

    def test_non_dict_returns_none(self) -> None:
        assert _finding_from_llm_obj("not a dict", source_issue={}) is None  # type: ignore[arg-type]
        assert _finding_from_llm_obj([1, 2, 3], source_issue={}) is None  # type: ignore[arg-type]
        assert _finding_from_llm_obj(None, source_issue={}) is None  # type: ignore[arg-type]

    def test_empty_text_returns_none(self) -> None:
        assert _finding_from_llm_obj({"text": ""}, source_issue={}) is None
        assert _finding_from_llm_obj({"text": "   "}, source_issue={}) is None
        assert _finding_from_llm_obj({}, source_issue={}) is None

    def test_invalid_severity_coerces_to_yellow(self) -> None:
        f = _finding_from_llm_obj(
            {"text": "x", "severity": "spicy"},
            source_issue={},
        )
        assert f is not None
        assert f.severity == "yellow"

    def test_missing_domain_defaults_to_general(self) -> None:
        f = _finding_from_llm_obj({"text": "x"}, source_issue={})
        assert f is not None
        assert f.domain == "General"

    def test_quantification_dropped_when_all_null(self) -> None:
        f = _finding_from_llm_obj(
            {
                "text": "x",
                "quantification": {
                    "mw_affected": None,
                    "eur_impact_estimate": None,
                    "days_until_deadline": None,
                    "rationale": "no impact data",
                },
            },
            source_issue={},
        )
        assert f is not None
        assert f.quantification is None


# ── _placeholder_finding_for_issue ───────────────────────────────────


class TestPlaceholder:
    def test_carries_section_and_label(self) -> None:
        f = _placeholder_finding_for_issue(
            3,
            {
                "section": "Permits",
                "label": "BImSchG Status",
                "evidence": [
                    {"doc_id": "d1", "doc_filename": "f.pdf", "excerpt": "x"},
                ],
            },
        )
        assert "issue #3" in f.text
        assert "Permits" in f.text
        assert "BImSchG Status" in f.text
        assert f.severity == "yellow"
        assert f.domain == "General"
        assert f.kind == "section"
        # Evidence from the source issue is carried so the lawyer
        # can still click through to the source row.
        assert len(f.evidence) == 1
        assert f.evidence[0].doc_id == "d1"

    def test_unknown_section_label_use_question_marks(self) -> None:
        """The placeholder must never error on a sparse issue dict."""
        f = _placeholder_finding_for_issue(1, {})
        assert "?" in f.text


# ── generate_findings ────────────────────────────────────────────────


class TestGenerateFindings:
    def test_no_flagged_rows_returns_green_finding(self, make_llm_json) -> None:
        """When every row is green, the function emits ONE finding
        (severity green, kind section, "No material issues …"). The
        LLM is not called."""
        make_llm_json([], queue=True)  # would record any call; expecting zero
        green = [
            AusgabeblattSection(id="s", title="S", rows=[
                _row("a", "ok", "green"),
                _row("b", "fine", "green"),
            ]),
        ]
        out = generate_findings(doc_ids=["d1"], sections=green)
        assert len(out) == 1
        assert out[0].severity == "green"
        assert out[0].domain == "General"

    def test_one_finding_per_flagged_row(self, make_llm_json) -> None:
        make_llm_json([
            {"text": "first finding", "severity": "red"},
            {"text": "second finding", "severity": "yellow"},
        ], queue=True)
        sections = [
            AusgabeblattSection(id="s1", title="Permits", rows=[
                _row("a", "v", "red"),
                _row("b", "v", "yellow"),
                _row("c", "v", "green"),  # not flagged → skipped
            ]),
        ]
        # max_workers=1: queue-mode responses are popped in call order,
        # so force sequential execution for a deterministic mapping.
        out = generate_findings(doc_ids=["d1"], sections=sections, max_workers=1)
        assert len(out) == 2
        assert out[0].text == "first finding"
        assert out[1].text == "second finding"

    def test_placeholder_when_obj_is_empty(self, make_llm_json) -> None:
        """The LLM returns ``{}`` (the documented hard-failure
        fallback path) → ``_finding_from_llm_obj`` returns None →
        :func:`_placeholder_finding_for_issue` fills the slot."""
        make_llm_json([{}], queue=True)
        sections = [
            AusgabeblattSection(id="s1", title="Permits", rows=[
                _row("Permit Status", "v", "red"),
            ]),
        ]
        out = generate_findings(doc_ids=["d1"], sections=sections, max_workers=1)
        assert len(out) == 1
        assert "Extraction failed for issue #1" in out[0].text
        assert "Permit Status" in out[0].text

    def test_partial_failures_leave_good_findings_intact(self, make_llm_json) -> None:
        """The "partial degradation" property — the reliability win
        the per-row design buys. ONE bad LLM response only
        downgrades ITS slot; the other findings come through."""
        make_llm_json([
            {"text": "good first", "severity": "red"},
            {},  # bad — placeholder
            {"text": "good third", "severity": "yellow"},
        ], queue=True)
        sections = [
            AusgabeblattSection(id="s", title="X", rows=[
                _row("r1", "v", "red"),
                _row("r2", "v", "yellow"),
                _row("r3", "v", "red"),
            ]),
        ]
        out = generate_findings(doc_ids=["d1"], sections=sections, max_workers=1)
        assert len(out) == 3
        assert out[0].text == "good first"
        assert "Extraction failed" in out[1].text
        assert out[2].text == "good third"

    def test_llm_json_raising_is_swallowed(self, monkeypatch) -> None:
        """Transport-level crash in ``llm_json`` (not the usual ``{}``
        return) must NOT escape the per-row try/except — that row
        falls through to placeholder and the loop continues."""
        import ddiq.extractors.findings as f

        def boom(*a, **kw):
            raise RuntimeError("transport died")

        monkeypatch.setattr(f, "llm_json", boom)
        sections = [
            AusgabeblattSection(id="s", title="X", rows=[
                _row("r1", "v", "red"),
            ]),
        ]
        out = generate_findings(doc_ids=["d1"], sections=sections)
        assert len(out) == 1
        assert "Extraction failed" in out[0].text

    def test_list_response_single_element_taken(self, make_llm_json) -> None:
        """If the LLM ignores the "single object" instruction and
        returns ``[{...}]`` we still accept the first element rather
        than placeholdering the whole row."""
        make_llm_json([
            [{"text": "wrapped in array", "severity": "red"}],
        ], queue=True)
        sections = [
            AusgabeblattSection(id="s", title="X", rows=[_row("r1", "v", "red")]),
        ]
        out = generate_findings(doc_ids=["d1"], sections=sections, max_workers=1)
        assert len(out) == 1
        assert out[0].text == "wrapped in array"

    def test_parallel_default_preserves_order(self, make_llm_json) -> None:
        """E1: with the default (parallel) executor, every flagged row
        gets the SAME canned response (non-queue mode), so ordering is
        deterministic and all rows resolve — proving the concurrent path
        assembles results in flagged-row order without dropping any."""
        make_llm_json({"text": "parallel finding", "severity": "yellow"})
        sections = [
            AusgabeblattSection(id="s", title="X", rows=[
                _row(f"r{i}", "v", "red") for i in range(8)
            ]),
        ]
        out = generate_findings(doc_ids=["d1"], sections=sections)  # default workers
        assert len(out) == 8
        assert all(f.text == "parallel finding" for f in out)
