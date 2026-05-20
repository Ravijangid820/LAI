"""Tests for the pure helper functions still living in ``ddiq_report``.

These are the small, dependency-free utilities the orchestrator uses:
value cleaning, section lookups, the WEA-count regex, the estimated-
parcel-polygon geometry, the parcel-reference regex, and the
SECTION_QUESTIONS catalogue structure. None touch the LLM, DB, or
network — they're exercised directly.

``import ddiq_report`` requires the auth-secret env var set at
collection time; the package conftest handles that.
"""

from __future__ import annotations

import math

import ddiq_report


# ── clean_value ──────────────────────────────────────────────────────


class TestCleanValue:
    def test_passes_real_values_through(self) -> None:
        assert ddiq_report.clean_value("Windpark Lamstedt") == "Windpark Lamstedt"

    def test_strips_whitespace(self) -> None:
        assert ddiq_report.clean_value("  erteilt  ") == "erteilt"

    def test_null_like_tokens_become_fallback(self) -> None:
        """The LLM returns a grab-bag of null sentinels; every one
        must collapse to the human-readable fallback so the UI never
        shows a raw 'null' / 'n/a' to a lawyer."""
        for token in ("null", "None", "N/A", "na", "nil", "undefined", "", "  "):
            assert ddiq_report.clean_value(token) == "Not specified in documents"

    def test_case_insensitive_null_detection(self) -> None:
        assert ddiq_report.clean_value("NULL") == "Not specified in documents"
        assert ddiq_report.clean_value("Null") == "Not specified in documents"

    def test_custom_fallback(self) -> None:
        assert ddiq_report.clean_value("null", fallback="—") == "—"

    def test_non_string_coerced(self) -> None:
        assert ddiq_report.clean_value(42) == "42"
        assert ddiq_report.clean_value(0) == "0"


# ── get_section_value ────────────────────────────────────────────────


class TestGetSectionValue:
    def _sections(self):
        from ddiq.models import AusgabeblattRow, AusgabeblattSection
        return [
            AusgabeblattSection(id="overview", title="Übersicht", rows=[
                AusgabeblattRow(label="Location", value="Cuxhaven, Niedersachsen"),
                AusgabeblattRow(label="Project Name", value="Windpark Test"),
            ]),
            AusgabeblattSection(id="land", title="Land", rows=[
                AusgabeblattRow(label="Term", value="25 Jahre"),
            ]),
        ]

    def test_finds_value(self) -> None:
        out = ddiq_report.get_section_value(self._sections(), "overview", "Location")
        assert out == "Cuxhaven, Niedersachsen"

    def test_missing_section_returns_empty(self) -> None:
        assert ddiq_report.get_section_value(self._sections(), "economics", "X") == ""

    def test_missing_label_returns_empty(self) -> None:
        assert ddiq_report.get_section_value(self._sections(), "overview", "Nope") == ""

    def test_empty_sections(self) -> None:
        assert ddiq_report.get_section_value([], "overview", "Location") == ""


# ── parse_wea_count ──────────────────────────────────────────────────


class TestParseWeaCount:
    def test_extracts_leading_int(self) -> None:
        assert ddiq_report.parse_wea_count("6 Windenergieanlagen") == 6

    def test_extracts_embedded_int(self) -> None:
        assert ddiq_report.parse_wea_count("insgesamt 12 WEA geplant") == 12

    def test_no_number_returns_zero(self) -> None:
        assert ddiq_report.parse_wea_count("keine Angabe") == 0

    def test_empty_returns_zero(self) -> None:
        assert ddiq_report.parse_wea_count("") == 0

    def test_first_number_wins(self) -> None:
        # The regex grabs the first run of digits.
        assert ddiq_report.parse_wea_count("3 errichtet, 4 geplant") == 3


# ── make_parcel_polygon ──────────────────────────────────────────────


class TestMakeParcelPolygon:
    def test_returns_four_corners(self) -> None:
        poly = ddiq_report.make_parcel_polygon(53.0, 8.0)
        assert len(poly) == 4
        assert all(len(pt) == 2 for pt in poly)

    def test_centered_on_input_point(self) -> None:
        """The polygon centroid should be (approximately) the input
        lat/lng — it's an estimated box drawn around the WEA mast."""
        lat, lng = 53.5, 8.5
        poly = ddiq_report.make_parcel_polygon(lat, lng, area_ha=2.5)
        cx = sum(p[0] for p in poly) / 4
        cy = sum(p[1] for p in poly) / 4
        assert cx == lat
        assert abs(cy - lng) < 1e-9

    def test_larger_area_makes_bigger_box(self) -> None:
        small = ddiq_report.make_parcel_polygon(53.0, 8.0, area_ha=1.0, rotation_seed=0)
        big = ddiq_report.make_parcel_polygon(53.0, 8.0, area_ha=10.0, rotation_seed=0)

        def span(poly):
            lats = [p[0] for p in poly]
            return max(lats) - min(lats)

        assert span(big) > span(small)

    def test_rotation_seed_changes_shape(self) -> None:
        """Different seeds rotate the box so adjacent estimated parcels
        don't render as identical rectangles."""
        a = ddiq_report.make_parcel_polygon(53.0, 8.0, rotation_seed=0)
        b = ddiq_report.make_parcel_polygon(53.0, 8.0, rotation_seed=5)
        assert a != b

    def test_rotation_wraps_at_quarter_pi(self) -> None:
        # angle = (seed * 0.3) % (pi/4); seed chosen so angle stays in range.
        poly = ddiq_report.make_parcel_polygon(53.0, 8.0, rotation_seed=100)
        # Just assert it produced a valid 4-corner polygon (no crash on
        # large seed) — the modulo keeps the angle bounded.
        assert len(poly) == 4
        assert all(math.isfinite(c) for pt in poly for c in pt)


# ── extract_parcel_refs ──────────────────────────────────────────────


class TestExtractParcelRefs:
    def test_extracts_flurstueck(self) -> None:
        text = "Das Flurstück 12/4 in der Gemarkung Lamstedt Flur 3 ist betroffen."
        out = ddiq_report.extract_parcel_refs(text)
        assert len(out) == 1
        assert out[0]["parcelNumber"] == "12/4"
        assert out[0]["gemarkung"] == "Lamstedt"
        assert out[0]["flur"] == 3

    def test_parcel_without_gemarkung_or_flur(self) -> None:
        out = ddiq_report.extract_parcel_refs("Flurstück 7/2 wird gepachtet.")
        assert len(out) == 1
        assert out[0]["parcelNumber"] == "7/2"
        assert out[0]["gemarkung"] == ""
        assert out[0]["flur"] == 0

    def test_deduplicates_same_number(self) -> None:
        text = "Flurstück 12/4 ... später nochmal Flurstück 12/4 erwähnt."
        out = ddiq_report.extract_parcel_refs(text)
        nums = [p["parcelNumber"] for p in out]
        assert nums == ["12/4"]

    def test_multiple_distinct_parcels(self) -> None:
        text = "Flurstück 12/4 und Parzelle 99/1 sowie Grundstück 5/7."
        out = ddiq_report.extract_parcel_refs(text)
        nums = {p["parcelNumber"] for p in out}
        assert nums == {"12/4", "99/1", "5/7"}

    def test_no_match_returns_empty(self) -> None:
        assert ddiq_report.extract_parcel_refs("Kein Flurstück hier genannt.") == []

    def test_umlaut_and_ascii_u_forms_match(self) -> None:
        """The regex character class is ``Flurst[üu]ck`` — it matches
        the proper umlaut ``Flurstück`` and the single-``u`` ASCII
        fallback ``Flurstuck``. (The ``ue`` digraph form ``Flurstueck``
        is NOT matched — documented limitation, see the next test.)"""
        for token in ("Flurstück", "Flurstuck"):
            out = ddiq_report.extract_parcel_refs(f"{token} 8/3 in Gemarkung Test")
            assert len(out) == 1, f"{token!r} should match"
            assert out[0]["parcelNumber"] == "8/3"

    def test_ue_digraph_not_matched(self) -> None:
        """Known limitation: the ``ue`` ASCII transliteration of ``ü``
        is not in the regex character class, so ``Flurstueck`` does
        not match. Captured here so a future regex change is a
        deliberate decision, not an accident."""
        assert ddiq_report.extract_parcel_refs("Flurstueck 8/3") == []


# ── SECTION_QUESTIONS structure ──────────────────────────────────────


class TestWeaLocationHelpers:
    """A7 — geocode/display strings are built from structured location
    fields, never the freeform paragraph that caused the Lamstedt→Bremen
    geocode failure."""

    def test_geocode_query_from_structured_fields(self) -> None:
        w = {"gemeinde": "Lamstedt", "landkreis": "Cuxhaven", "bundesland": "Niedersachsen"}
        q = ddiq_report._wea_geocode_query(w)
        assert q == "Lamstedt, Cuxhaven, Niedersachsen, Deutschland"

    def test_geocode_query_skips_nullish_fields(self) -> None:
        w = {"gemeinde": "Hude", "landkreis": "", "bundesland": "null"}
        assert ddiq_report._wea_geocode_query(w) == "Hude, Deutschland"

    def test_geocode_query_falls_back_to_short_address(self) -> None:
        w = {"address": "Cuxhaven, Niedersachsen"}
        assert ddiq_report._wea_geocode_query(w) == "Cuxhaven, Niedersachsen"

    def test_geocode_query_drops_paragraph_address(self) -> None:
        """The failure mode A7 fixes: a multi-sentence paragraph must NOT
        be fed to the geocoder — return empty so no geocode is attempted
        (the caller falls back to project_center)."""
        para = (
            "Die Windenergieanlage befindet sich im Außenbereich der Gemeinde "
            "Lamstedt. Sie ist über die Kreisstraße K50 erschlossen. Der "
            "nächste Ort ist Cuxhaven."
        )
        assert ddiq_report._wea_geocode_query({"address": para}) == ""

    def test_geocode_query_empty_when_nothing(self) -> None:
        assert ddiq_report._wea_geocode_query({}) == ""

    def test_display_address_prefers_structured(self) -> None:
        w = {"gemeinde": "Lamstedt", "bundesland": "Niedersachsen", "address": "ignored long form"}
        assert ddiq_report._wea_display_address(w) == "Lamstedt, Niedersachsen"

    def test_display_address_truncates_paragraph_fallback(self) -> None:
        para = "x" * 200
        out = ddiq_report._wea_display_address({"address": para})
        assert len(out) == 80

    def test_display_address_empty(self) -> None:
        assert ddiq_report._wea_display_address({}) == ""


class TestBackfillWeaOwner:
    """A6 — per-WEA owner placeholders are filled from the canonical
    project company so every turbine reports one consistent owner."""

    def _weas(self, owners: list[str]):
        from ddiq.models import WEAStatus
        return [
            WEAStatus(name=f"WEA {i}", ampel="green", owner=o, parcel="", contract="",
                      lat=53.0, lng=8.0, address="")
            for i, o in enumerate(owners)
        ]

    def test_backfills_placeholders(self) -> None:
        weas = self._weas(["", "Unknown", "See contracts", "Real GmbH"])
        n = ddiq_report._backfill_wea_owner(weas, "Windpark Lamstedt GmbH & Co. KG")
        assert n == 3
        assert weas[0].owner == "Windpark Lamstedt GmbH & Co. KG"
        assert weas[1].owner == "Windpark Lamstedt GmbH & Co. KG"
        assert weas[2].owner == "Windpark Lamstedt GmbH & Co. KG"
        # A real per-row owner is left untouched.
        assert weas[3].owner == "Real GmbH"

    def test_no_company_is_noop(self) -> None:
        weas = self._weas(["", "Unknown"])
        assert ddiq_report._backfill_wea_owner(weas, None) == 0
        assert weas[0].owner == ""

    def test_placeholder_company_is_noop(self) -> None:
        weas = self._weas([""])
        assert ddiq_report._backfill_wea_owner(weas, "Unknown") == 0

    def test_case_insensitive_placeholder_match(self) -> None:
        weas = self._weas(["UNKNOWN", "  see contracts  "])
        n = ddiq_report._backfill_wea_owner(weas, "ACME KG")
        assert n == 2


class TestProjectFacts:
    """A6 — the canonical facts object surfaces the reconciled capacity
    (which was previously computed but never stored) + identity."""

    def test_construct_and_dump(self) -> None:
        from ddiq.models import ProjectFacts
        f = ProjectFacts(
            projectName="Windpark Lamstedt", preparedFor="Investor AG",
            projectCompany="Lamstedt GmbH & Co. KG",
            projectCenter={"lat": 53.62, "lng": 9.15},
            bundesland="niedersachsen", turbineCount=6, totalCapacityMw=25.2,
        )
        d = f.model_dump()
        assert d["totalCapacityMw"] == 25.2
        assert d["turbineCount"] == 6
        assert d["bundesland"] == "niedersachsen"
        assert d["projectCompany"] == "Lamstedt GmbH & Co. KG"

    def test_unknown_values_stay_none(self) -> None:
        from ddiq.models import ProjectFacts
        f = ProjectFacts(
            projectName="P", preparedFor="C",
            projectCenter={"lat": 0.0, "lng": 0.0},
        )
        assert f.projectCompany is None
        assert f.bundesland is None
        assert f.totalCapacityMw is None
        assert f.turbineCount == 0


class TestSectionQuestions:
    def test_has_four_sections(self) -> None:
        assert set(ddiq_report.SECTION_QUESTIONS) == {
            "overview", "land", "permits", "economics",
        }

    def test_every_question_has_required_keys(self) -> None:
        """Each question drives one Ausgabeblatt row + RAG query — it
        must carry label, question, and anchor (the statutory hook
        that keeps the LLM grounded). A missing key would silently
        produce an unlabelled or anchorless row."""
        for section, questions in ddiq_report.SECTION_QUESTIONS.items():
            assert questions, f"section {section} has no questions"
            for q in questions:
                assert q["label"], f"{section}: question missing label"
                assert q["question"], f"{section}: {q['label']} missing question text"
                assert q["anchor"], f"{section}: {q['label']} missing anchor"

    def test_labels_unique_within_section(self) -> None:
        """Row labels key into get_section_value — duplicates within a
        section would make the lookup ambiguous."""
        for section, questions in ddiq_report.SECTION_QUESTIONS.items():
            labels = [q["label"] for q in questions]
            assert len(labels) == len(set(labels)), f"dup label in {section}"
