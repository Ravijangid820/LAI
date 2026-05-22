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
from types import SimpleNamespace

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
        # No turbine-count keyword follows either number, so it falls back
        # to the leading bare integer.
        assert ddiq_report.parse_wea_count("3 errichtet, 4 geplant") == 3

    def test_prefers_count_phrase_over_leading_date(self) -> None:
        # The real Lamstedt "Number of WEA" cell opens with prose and a
        # date; the old first-digit grab returned a date fragment. We must
        # latch onto the explicit "10 Anlagen" total instead.
        cell = (
            "Aus der Änderungsgenehmigung vom 06.04.2005 geht hervor, dass "
            "für die Anlagen L 1 bis L 7, L 9, L 15 und L 16 (insgesamt 10 "
            "Anlagen) Auflagen erteilt wurden."
        )
        assert ddiq_report.parse_wea_count(cell) == 10

    def test_count_phrase_variants(self) -> None:
        assert ddiq_report.parse_wea_count("10 Windenergieanlagen") == 10
        assert ddiq_report.parse_wea_count("7 WKA errichtet") == 7
        assert ddiq_report.parse_wea_count("vorgesehen sind 5 Anlagen") == 5


# ── _looks_like_address ──────────────────────────────────────────────


class TestLooksLikeAddress:
    def test_applicant_address_line_is_rejected(self) -> None:
        # The exact smoke-test bug: the metadata LLM returned the
        # applicant's street line as the project name.
        assert ddiq_report._looks_like_address("Sönke-Nissen-Koog 58") is True

    def test_street_suffix_is_an_address(self) -> None:
        assert ddiq_report._looks_like_address("Vincent-Lübeck-Str.") is True
        assert ddiq_report._looks_like_address("Hauptstraße") is True

    def test_windpark_name_is_never_an_address(self) -> None:
        assert ddiq_report._looks_like_address("Windpark Lamstedt") is False
        # A park name with a number in it is still a project name.
        assert ddiq_report._looks_like_address("Windpark Nordsee 2") is False

    def test_plain_project_name(self) -> None:
        assert ddiq_report._looks_like_address("Lamstedt") is False

    def test_empty_is_not_an_address(self) -> None:
        assert ddiq_report._looks_like_address("") is False
        assert ddiq_report._looks_like_address(None) is False


# ── _plausible_rated_kw ──────────────────────────────────────────────


class TestPlausibleRatedKw:
    def test_realistic_value_passes(self) -> None:
        assert ddiq_report._plausible_rated_kw(2300) == 2300.0
        assert ddiq_report._plausible_rated_kw("4200") == 4200.0

    def test_order_of_magnitude_error_rejected(self) -> None:
        # The Lamstedt bug: E-70 (really 2300 kW) extracted as 22000 kW,
        # which made 8 turbines sum to a phantom 176 MW.
        assert ddiq_report._plausible_rated_kw(22000) is None

    def test_zero_and_negative_rejected(self) -> None:
        assert ddiq_report._plausible_rated_kw(0) is None
        assert ddiq_report._plausible_rated_kw(-5) is None

    def test_none_and_junk_become_none(self) -> None:
        assert ddiq_report._plausible_rated_kw(None) is None
        assert ddiq_report._plausible_rated_kw("") is None
        assert ddiq_report._plausible_rated_kw("n/a") is None

    def test_ceiling_is_inclusive(self) -> None:
        assert ddiq_report._plausible_rated_kw(10000) == 10000.0
        assert ddiq_report._plausible_rated_kw(10000.01) is None


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


class TestApplyCanonicalSpecs:
    """A10 — null per-WEA spec fields are back-filled from the canonical
    datasheet spec; a real per-turbine value is never overwritten."""

    def _wea(self, **kw):
        from ddiq.models import WEAStatus
        base = dict(name="WEA 1", ampel="green", owner="o", parcel="", contract="",
                    lat=53.0, lng=8.0, address="")
        base.update(kw)
        return WEAStatus(**base)

    def test_fills_null_fields(self) -> None:
        weas = [self._wea(), self._wea()]
        specs = {"manufacturer": "Enercon", "model": "E-138 EP3",
                 "hub_height_m": 131, "rotor_diameter_m": 138.6, "rated_power_kw": 4200}
        fills = ddiq_report._apply_canonical_specs(weas, specs)
        # 5 fields × 2 turbines, all null → 10 fills.
        assert fills == 10
        assert weas[0].manufacturer == "Enercon"
        assert weas[0].hub_height_m == 131.0
        assert weas[1].rated_power_kw == 4200.0

    def test_does_not_overwrite_real_values(self) -> None:
        weas = [self._wea(manufacturer="Vestas", hub_height_m=149.0)]
        specs = {"manufacturer": "Enercon", "hub_height_m": 131,
                 "rotor_diameter_m": 138.6}
        fills = ddiq_report._apply_canonical_specs(weas, specs)
        # manufacturer + hub_height already set → not touched; only
        # rotor_diameter (+ the two still-null fields stay null since
        # specs has no value for them) gets filled.
        assert weas[0].manufacturer == "Vestas"
        assert weas[0].hub_height_m == 149.0
        assert weas[0].rotor_diameter_m == 138.6
        assert fills == 1

    def test_empty_specs_is_noop(self) -> None:
        weas = [self._wea()]
        assert ddiq_report._apply_canonical_specs(weas, {}) == 0
        assert weas[0].manufacturer is None

    def test_null_spec_values_skipped(self) -> None:
        weas = [self._wea()]
        specs = {"manufacturer": None, "model": "", "hub_height_m": None}
        assert ddiq_report._apply_canonical_specs(weas, specs) == 0

    def test_unparseable_numeric_skipped(self) -> None:
        weas = [self._wea()]
        specs = {"hub_height_m": "tall", "rated_power_kw": 4200}
        fills = ddiq_report._apply_canonical_specs(weas, specs)
        assert fills == 1
        assert weas[0].hub_height_m is None
        assert weas[0].rated_power_kw == 4200.0


class TestRelanguageText:
    """A8 — mixed-language text is re-rendered in the target language by
    a focused LLM call; best-effort so failures never blank a cell."""

    def test_returns_llm_output(self, patch_llm_singletons) -> None:
        patch_llm_singletons.responses = ["Die Genehmigung wurde erteilt."]
        out = ddiq_report._relanguage_text(
            "The Genehmigung wurde erteilt.", target_language="de",
        )
        assert out == "Die Genehmigung wurde erteilt."
        # System prompt must name the target language + the preserve rule.
        sys_, _, _, _ = patch_llm_singletons.calls[0]
        assert "German" in sys_
        assert "BImSchG" in sys_  # the preserve-statutes instruction

    def test_english_target(self, patch_llm_singletons) -> None:
        patch_llm_singletons.responses = ["The permit was granted."]
        ddiq_report._relanguage_text("Die permit was granted.", target_language="en")
        sys_, _, _, _ = patch_llm_singletons.calls[0]
        assert "English" in sys_

    def test_empty_input_is_noop(self, patch_llm_singletons) -> None:
        assert ddiq_report._relanguage_text("", "de") == ""
        assert ddiq_report._relanguage_text("   ", "de") == "   "
        # No LLM call for empty input.
        assert patch_llm_singletons.calls == []

    def test_llm_failure_returns_original(self, monkeypatch, mock_llm_client) -> None:
        import ddiq.llm as ddiq_llm
        mock_llm_client.raise_on_call = True
        monkeypatch.setattr(ddiq_llm, "_LLM_CLIENT", mock_llm_client)
        original = "The Genehmigung wurde erteilt."
        # llm_call swallows LlmError → returns "" → _relanguage_text keeps original.
        assert ddiq_report._relanguage_text(original, "de") == original

    def test_empty_llm_output_returns_original(self, patch_llm_singletons) -> None:
        patch_llm_singletons.responses = ["   "]  # whitespace-only
        original = "Mixed text hier"
        assert ddiq_report._relanguage_text(original, "de") == original


class TestNeedsRelanguage:
    """A8 (v3 fix) — re-language is triggered by a WHOLLY wrong-language
    cell, not just a mid-sentence mix. The §14 v3 run had a fully-English
    finding slip past the old 'mixed'-only check."""

    def test_wrong_language_triggers(self) -> None:
        # Pure English in a German report → must re-language.
        en = "The modification permit was formally granted under the relevant act."
        assert ddiq_report._needs_relanguage(en, "de") is True

    def test_mixed_triggers(self) -> None:
        mixed = "Die Genehmigung is not yet bestandskräftig and the operator must renew it before the deadline."
        assert ddiq_report._needs_relanguage(mixed, "de") is True

    def test_target_language_left_alone(self) -> None:
        de = "Die BImSchG-Genehmigung wurde am 20.07.2007 erteilt und ist bestandskräftig."
        assert ddiq_report._needs_relanguage(de, "de") is False

    def test_unknown_left_alone(self) -> None:
        # Too short / numeric → unknown → don't touch.
        assert ddiq_report._needs_relanguage("12/4", "de") is False
        assert ddiq_report._needs_relanguage("", "de") is False

    def test_english_target_flips(self) -> None:
        de = "Die Genehmigung wurde erteilt und ist seit drei Monaten bestandskräftig."
        assert ddiq_report._needs_relanguage(de, "en") is True


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


class TestDropGeocodeOutlierWeas:
    """A4: count/capacity must use the real WEA cluster, not turbines a ruling
    cites from other cases that geocoded far away. The helper only reads
    ``.lat``/``.lng``, so lightweight stubs suffice."""

    @staticmethod
    def _w(lat: float, lng: float):
        return SimpleNamespace(lat=lat, lng=lng)

    def _cluster(self, n: int):
        # Tight Lamstedt cluster (~1 km spread, well within 25 km).
        return [self._w(53.63 + 0.001 * i, 9.10 + 0.001 * i) for i in range(n)]

    def test_drops_far_outliers(self) -> None:
        weas = self._cluster(8) + [self._w(53.09, 8.78),   # Bremen ~60 km
                                   self._w(48.14, 11.58),   # Munich
                                   self._w(52.52, 13.40)]   # Berlin
        kept = ddiq_report._drop_geocode_outlier_weas(weas)
        assert len(kept) == 8

    def test_keeps_ungeocoded_drops_outlier(self) -> None:
        weas = self._cluster(5) + [self._w(48.14, 11.58)] + [self._w(0.0, 0.0)]
        kept = ddiq_report._drop_geocode_outlier_weas(weas)
        assert len(kept) == 6                                 # 5 cluster + ungeocoded
        assert any(w.lat == 0 for w in kept)                  # ungeocoded kept
        assert not any(abs(w.lat - 48.14) < 0.01 for w in kept)  # Munich dropped

    def test_two_or_fewer_geocoded_unchanged(self) -> None:
        weas = [self._w(53.63, 9.10), self._w(48.14, 11.58)]  # 2 geocoded, far apart
        assert len(ddiq_report._drop_geocode_outlier_weas(weas)) == 2

    def test_tight_cluster_keeps_all(self) -> None:
        weas = self._cluster(10)
        assert len(ddiq_report._drop_geocode_outlier_weas(weas)) == 10
