"""Unit tests for the statute → domain category registry."""

from __future__ import annotations

import pytest

from lai.common.connectors.statute_categories import (
    DEFAULT_DOMAIN,
    KNOWN_DOMAINS,
    categorize,
    mapped_slugs,
)

pytestmark = pytest.mark.unit


def test_known_law_maps_to_expected_domain() -> None:
    assert categorize("bimschg") == "immissionsschutzrecht"
    assert categorize("eeg_2014") == "energierecht"
    assert categorize("bbaug") == "baurecht"
    assert categorize("bgb") == "vertragsrecht"


def test_unmapped_law_falls_back_to_default() -> None:
    assert categorize("some_obscure_verordnung") == DEFAULT_DOMAIN


def test_categorize_is_case_insensitive() -> None:
    assert categorize("BImSchG") == categorize("bimschg")


def test_every_mapped_domain_is_a_known_domain() -> None:
    # Guards against a typo'd domain key drifting from classify.py's taxonomy.
    for slug in mapped_slugs():
        assert categorize(slug) in KNOWN_DOMAINS


def test_default_domain_is_not_a_known_domain() -> None:
    # `allgemein` is the catch-all, deliberately outside the classified set.
    assert DEFAULT_DOMAIN not in KNOWN_DOMAINS
