"""Tests for :mod:`lai.common.jurisdiction.validator`."""

from __future__ import annotations

import pytest

from lai.common.jurisdiction import (
    BUNDESLAND_BBOX,
    BUNDESLAND_KEYWORDS,
    GERMANY_BBOX,
    JurisdictionWarning,
    check_jurisdiction,
    detect_bundesland,
    point_in_bbox,
)


class TestBundeslandKeywordTable:
    @pytest.mark.unit
    def test_covers_all_16_states(self) -> None:
        assert len(BUNDESLAND_KEYWORDS) == 16

    @pytest.mark.unit
    def test_each_state_has_keywords(self) -> None:
        for state, kws in BUNDESLAND_KEYWORDS.items():
            assert len(kws) >= 1, f"{state} has no keywords"

    @pytest.mark.unit
    def test_state_keys_match_bbox_keys(self) -> None:
        assert set(BUNDESLAND_KEYWORDS.keys()) == set(BUNDESLAND_BBOX.keys())


class TestDetectBundesland:
    @pytest.mark.unit
    def test_empty_returns_none(self) -> None:
        assert detect_bundesland("") is None

    @pytest.mark.unit
    def test_obvious_state_name(self) -> None:
        assert detect_bundesland("The project is in Niedersachsen") == "niedersachsen"

    @pytest.mark.unit
    def test_landkreis_hint(self) -> None:
        # "Cuxhaven" is in the niedersachsen keyword list.
        assert detect_bundesland("Cuxhaven, Lower Saxony, Germany") == "niedersachsen"

    @pytest.mark.unit
    def test_bayern_via_bavaria(self) -> None:
        # English-speakers say "Bavaria"; the table covers both.
        assert detect_bundesland("Munich, Bavaria") == "bayern"

    @pytest.mark.unit
    def test_no_match_returns_none(self) -> None:
        assert detect_bundesland("Plain English with no German place") is None

    @pytest.mark.unit
    def test_case_insensitive(self) -> None:
        assert detect_bundesland("HANNOVER") == "niedersachsen"


class TestPointInBbox:
    @pytest.mark.unit
    def test_cuxhaven_in_niedersachsen(self) -> None:
        # Cuxhaven ≈ 53.87 N, 8.70 E.
        assert point_in_bbox(53.87, 8.70, BUNDESLAND_BBOX["niedersachsen"])

    @pytest.mark.unit
    def test_munich_in_bayern(self) -> None:
        # München ≈ 48.14 N, 11.58 E.
        assert point_in_bbox(48.14, 11.58, BUNDESLAND_BBOX["bayern"])

    @pytest.mark.unit
    def test_stuttgart_not_in_niedersachsen(self) -> None:
        # Stuttgart ≈ 48.78 N, 9.18 E — far south of Niedersachsen.
        assert not point_in_bbox(48.78, 9.18, BUNDESLAND_BBOX["niedersachsen"])

    @pytest.mark.unit
    def test_germany_bbox_accepts_all_states(self) -> None:
        # Every Bundesland's lat_min/lng_min/lat_max/lng_max must lie
        # inside GERMANY_BBOX (sanity-check the union bbox).
        lat_min_g, lat_max_g, lng_min_g, lng_max_g = GERMANY_BBOX
        for state, (lat_min, lat_max, lng_min, lng_max) in BUNDESLAND_BBOX.items():
            assert lat_min_g <= lat_min <= lat_max <= lat_max_g, state
            assert lng_min_g <= lng_min <= lng_max <= lng_max_g, state

    @pytest.mark.unit
    def test_paris_outside_germany_bbox(self) -> None:
        # Paris ≈ 48.86 N, 2.35 E — well outside Germany.
        assert not point_in_bbox(48.86, 2.35, GERMANY_BBOX)


class TestCheckJurisdictionBayern10H:
    """The lawyer's #2 v0 complaint: 10H rule cited for a non-Bayern project."""

    @pytest.mark.unit
    def test_10h_in_bayern_no_warning(self) -> None:
        answer = "Nach der 10H-Regel des Art. 82 BayBO gilt der Mindestabstand."
        assert check_jurisdiction(answer, "bayern") == []

    @pytest.mark.unit
    def test_10h_in_niedersachsen_warns(self) -> None:
        answer = "Nach der 10H-Regel des Art. 82 BayBO gilt der Mindestabstand."
        warnings = check_jurisdiction(answer, "niedersachsen")
        assert len(warnings) == 1
        w = warnings[0]
        assert isinstance(w, JurisdictionWarning)
        assert w.rule_bundesland == "bayern"
        assert w.expected_bundesland == "niedersachsen"
        assert "10H-Regel" in w.rule_label or "10H" in w.rule_label

    @pytest.mark.unit
    def test_10h_with_space_variants_caught(self) -> None:
        # The pattern handles "10 H", "10x H", and similar typographic
        # variants the model might produce.
        for snippet in ("10 H", "10xH", "10×H", "10H-Regelung"):
            answer = f"Der Abstand entspricht {snippet} der WEA-Höhe."
            warnings = check_jurisdiction(answer, "niedersachsen")
            assert warnings, f"failed to catch {snippet!r}"

    @pytest.mark.unit
    def test_baybo_citation_warns_for_non_bayern(self) -> None:
        answer = "Art. 82a BayBO regelt die Ausnahmen."
        warnings = check_jurisdiction(answer, "nordrhein-westfalen")
        assert len(warnings) == 1
        assert warnings[0].rule_bundesland == "bayern"

    @pytest.mark.unit
    def test_multiple_10h_mentions_dedupe(self) -> None:
        # Two patterns from the SAME rule fire — must produce ONE
        # warning, not duplicates.
        answer = "Die 10H-Regel gilt; siehe Art. 82 BayBO und Art. 82a BayBO."
        warnings = check_jurisdiction(answer, "hessen")
        assert len(warnings) == 1


class TestCheckJurisdictionNiedersachsen:
    @pytest.mark.unit
    def test_nbauo_in_niedersachsen_no_warning(self) -> None:
        answer = "Die NBauO regelt die Abstandsfläche."
        assert check_jurisdiction(answer, "niedersachsen") == []

    @pytest.mark.unit
    def test_nbauo_in_bayern_warns(self) -> None:
        answer = "Die NBauO regelt die Abstandsfläche."
        warnings = check_jurisdiction(answer, "bayern")
        assert len(warnings) == 1
        assert warnings[0].rule_bundesland == "niedersachsen"


class TestCheckJurisdictionEdgeCases:
    @pytest.mark.unit
    def test_empty_answer(self) -> None:
        assert check_jurisdiction("", "bayern") == []

    @pytest.mark.unit
    def test_unknown_bundesland_disables_check(self) -> None:
        # When the matter has no detected Bundesland, we cannot say
        # whether a rule is mis-cited. Returns empty so the UI shows no
        # warnings — better than false-positives.
        answer = "Nach 10H BayBO ist der Abstand zu berechnen."
        assert check_jurisdiction(answer, None) == []

    @pytest.mark.unit
    def test_excerpt_attached(self) -> None:
        answer = "Es gilt zunächst 10H der WEA-Höhe als Mindestabstand."
        warnings = check_jurisdiction(answer, "niedersachsen")
        assert len(warnings) == 1
        assert "10H" in warnings[0].excerpt or "10 H" in warnings[0].excerpt

    @pytest.mark.unit
    def test_expected_bundesland_case_insensitive(self) -> None:
        answer = "BayBO regelt das."
        # Upper-case input still works — validator normalises.
        warnings = check_jurisdiction(answer, "BAYERN")
        assert warnings == []  # no warning, since expected is bayern

    @pytest.mark.unit
    def test_multiple_rule_types_each_warn_once(self) -> None:
        # Answer cites both Bayern's 10H AND Niedersachsen's NBauO.
        # If the matter is in NRW, BOTH rules should warn — one each.
        answer = "Beachte 10H BayBO sowie die NBauO-Abstandsregeln für den Nachbarschaftsschutz."
        warnings = check_jurisdiction(answer, "nordrhein-westfalen")
        assert len(warnings) == 2
        rule_states = {w.rule_bundesland for w in warnings}
        assert rule_states == {"bayern", "niedersachsen"}


class TestResultShape:
    @pytest.mark.unit
    def test_warning_is_frozen(self) -> None:
        w = JurisdictionWarning(
            rule_label="x",
            rule_bundesland="bayern",
            expected_bundesland="hessen",
            excerpt="x",
        )
        with pytest.raises((AttributeError, TypeError)):
            w.rule_label = "y"  # type: ignore[misc]
