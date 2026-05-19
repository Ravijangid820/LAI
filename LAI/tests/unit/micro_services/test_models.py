"""Tests for :mod:`ddiq.models`.

These are shape + default tests on pure Pydantic. They don't touch
the LLM, DB, or HTTP — they verify that the data contracts at the
edge of the DDiQ pipeline (request / response shapes, default
values, serialization round-trips) match what the orchestrator and
UI both depend on.

A regression here typically means the UI silently degrades (a
defaulted-empty list disappears from JSON, an Optional becomes
required, etc.), which is hard to catch from end-to-end tests.
"""

from __future__ import annotations

import pytest

from ddiq.models import (
    AusgabeblattRow,
    AusgabeblattSection,
    CadastralParcel,
    DDiQReportData,
    DocumentOut,
    Evidence,
    Finding,
    GenerateReportRequest,
    GenerateReportResponse,
    GrundbuchCheck,
    InfraPoint,
    ProjectAreaRequest,
    ProjectAreaResponse,
    Quantification,
    RueckbauBond,
    TimelineEntry,
    UploadDocResponse,
    WEAStatus,
)


# ── Default-value contracts ──────────────────────────────────────────


class TestDefaults:
    """The orchestrator constructs DDiQReportData early in the pipeline
    and incrementally populates fields. Every list-valued field has to
    default to ``[]`` and every Optional to ``None`` — otherwise a
    mid-pipeline crash leaves an empty placeholder row that fails
    Pydantic validation on the way out."""

    def test_ddiq_report_data_starts_empty(self) -> None:
        r = DDiQReportData(
            projectName="P", preparedBy="b", preparedFor="f",
            date="2026-05-19", projectCenter={"lat": 53.0, "lng": 8.0},
        )
        assert r.sections == []
        assert r.weaStatuses == []
        assert r.infrastructure == []
        assert r.parcels == []
        assert r.findings == []
        assert r.timeline == []
        assert r.crossDocFindings == []
        assert r.grundbuchChecks == []
        assert r.rueckbauBond is None
        assert r.documentMap == []
        assert r.turbineCount == 0
        assert r.bundesland is None
        assert r.jurisdictionWarnings == []

    def test_finding_defaults(self) -> None:
        f = Finding(domain="Land", severity="yellow", text="t")
        assert f.evidence == []
        assert f.quantification is None
        assert f.legal_basis is None
        assert f.recommended_action is None
        # kind defaults to "section" — only the cross-doc and
        # placeholder paths set a different value.
        assert f.kind == "section"

    def test_rueckbau_bond_all_optional(self) -> None:
        """The 'not found in documents' path returns a RueckbauBond with
        every field None. This must not raise."""
        b = RueckbauBond()
        assert b.amount_eur is None
        assert b.provider is None
        assert b.beneficiary is None
        assert b.valid_until is None
        assert b.instrument_type is None
        assert b.sufficient is None
        assert b.evidence == []
        assert b.note is None

    def test_evidence_excerpt_defaults_to_empty(self) -> None:
        e = Evidence()
        assert e.doc_id is None
        assert e.doc_filename is None
        assert e.page is None
        assert e.excerpt == ""
        assert e.clause is None


# ── Round-trip serialisation ─────────────────────────────────────────


class TestRoundTrip:
    """Pydantic v2 ``model_dump`` / ``model_validate`` round-trip —
    the path Celery takes when shipping :class:`GenerateReportRequest`
    through Redis. If a field is added later without a default, this
    breaks first."""

    def test_request_roundtrip(self) -> None:
        req = GenerateReportRequest(
            document_ids=["abc-123", "def-456"],
            preset="wea_full",
            project_name="Windpark Test",
            prepared_for="Customer Ltd",
        )
        dumped = req.model_dump(mode="json")
        rehydrated = GenerateReportRequest.model_validate(dumped)
        assert rehydrated.document_ids == req.document_ids
        assert rehydrated.preset == req.preset
        assert rehydrated.project_name == req.project_name
        assert rehydrated.prepared_for == req.prepared_for

    def test_request_preset_defaults_to_full(self) -> None:
        req = GenerateReportRequest(document_ids=["x"])
        assert req.preset == "full"
        assert req.project_name is None
        assert req.prepared_for is None

    def test_finding_with_evidence_roundtrip(self) -> None:
        f = Finding(
            domain="Permits",
            severity="red",
            text="BImSchG-Genehmigung läuft am 2027-06-30 ab.",
            legal_basis="BImSchG §6",
            recommended_action="Verlängerungsantrag spätestens 6 Mt vor Ablauf",
            evidence=[Evidence(
                doc_id="permit-doc-1",
                doc_filename="Bescheid.pdf",
                excerpt="…Genehmigung gilt bis 30.06.2027…",
                clause="§6 BImSchG",
            )],
            quantification=Quantification(
                mw_affected=12.6,
                eur_impact_estimate=8_400_000.0,
                days_until_deadline=412,
                rationale="6 turbines × 2.1 MW",
            ),
            kind="deadline",
        )
        dumped = f.model_dump(mode="json")
        re = Finding.model_validate(dumped)
        assert re.text == f.text
        assert len(re.evidence) == 1
        assert re.evidence[0].doc_id == "permit-doc-1"
        assert re.quantification is not None
        assert re.quantification.mw_affected == 12.6


# ── Field validation ─────────────────────────────────────────────────


class TestValidation:
    def test_wea_status_required_geo(self) -> None:
        """``lat`` / ``lng`` are mandatory on :class:`WEAStatus` —
        Pydantic raises on construction without them."""
        with pytest.raises(Exception):
            WEAStatus(  # type: ignore[call-arg] — exercising the missing-required path
                name="WEA-1", ampel="green", owner="o", parcel="p",
                contract="c", address="a",
            )

    def test_cadastral_parcel_polygon_is_list_of_pairs(self) -> None:
        p = CadastralParcel(
            id="p-1", parcelNumber="12/4", gemarkung="Test", flur=1,
            polygon=[[53.0, 8.0], [53.001, 8.0], [53.001, 8.001]],
            status="secured", owner="X", area=12345.0,
        )
        assert p.polygon[0] == [53.0, 8.0]
        # Defaults that the cadastral pipeline relies on:
        assert p.polygonSource == "estimated"
        assert p.confidence == 0.0
        assert p.normalizedId == ""

    def test_timeline_entry_minimum(self) -> None:
        t = TimelineEntry(
            kind="permit_expiry",
            date="2027-06-30",
            description="BImSchG permit Aktenzeichen 12-345 expires",
        )
        assert t.legal_basis is None
        assert t.evidence == []
        assert t.days_from_now is None
        assert t.urgency is None

    def test_project_area_request_default_name(self) -> None:
        req = ProjectAreaRequest(polygon=[[53.0, 8.0], [53.1, 8.1], [53.0, 8.1]])
        assert req.name == "User-Defined Area"


# ── Trivial-but-load-bearing shapes ──────────────────────────────────


def test_document_out_shape() -> None:
    d = DocumentOut(
        id="d1", name="x.pdf", size=12.5,
        uploadDate="2026-05-19", type="application/pdf",
        status="processed", category="Permit",
    )
    assert d.id == "d1"


def test_upload_doc_response_shape() -> None:
    u = UploadDocResponse(
        id="u1", filename="x.pdf", pages=10, chunks=24,
        status="ok", message="processed",
    )
    assert u.pages == 10


def test_ausgabeblatt_nesting() -> None:
    s = AusgabeblattSection(
        id="overview", title="Übersicht",
        rows=[
            AusgabeblattRow(label="Status", value="erteilt", ampel="green"),
            AusgabeblattRow(label="Frist", value="2027-06-30", ampel="yellow"),
        ],
    )
    assert len(s.rows) == 2
    assert s.rows[0].ampel == "green"


def test_infra_point_shape() -> None:
    p = InfraPoint(name="Substation A", type="substation", lat=53.0, lng=8.0)
    assert p.type == "substation"


def test_grundbuch_check_defaults() -> None:
    g = GrundbuchCheck(parcel_id="bremen:1:12_4")
    assert g.registered_owner is None
    assert g.lessor_name is None
    assert g.owner_match is None
    assert g.match_confidence == 0.0
    assert g.encumbrances == []
    assert g.evidence == []
    assert g.note is None


def test_generate_report_response_shape() -> None:
    r = GenerateReportResponse(
        report_id="r-1",
        report=DDiQReportData(
            projectName="P", preparedBy="b", preparedFor="f",
            date="2026-05-19", projectCenter={"lat": 53.0, "lng": 8.0},
        ),
        timings={"total_s": 42.5},
    )
    assert r.report_id == "r-1"
    assert r.timings["total_s"] == 42.5


def test_project_area_response_shape() -> None:
    r = ProjectAreaResponse(
        id="pa-1", name="Site A", polygon=[[53.0, 8.0]],
        centroid_lat=53.0, centroid_lng=8.0,
        area_km2=0.5, source="user_drawn",
    )
    assert r.source == "user_drawn"
