"""Unit tests for the pure statute-ingest helpers."""

from __future__ import annotations

import pytest

from lai.common.connectors._gii_parser import ParsedLaw, StatuteSection
from lai.common.connectors.statute_ingest import (
    content_hash,
    segments_from_parsed_law,
    stable_chunk_id,
    stable_doc_id,
)

pytestmark = pytest.mark.unit


def _law(jurabk: str | None = "TestG", sections: tuple[StatuteSection, ...] = ()) -> ParsedLaw:
    return ParsedLaw(jurabk=jurabk, long_title=None, sections=sections)


def test_stable_doc_id_is_deterministic_and_case_insensitive() -> None:
    assert stable_doc_id("bimschg") == stable_doc_id("BImSchG")
    assert stable_doc_id("bimschg") != stable_doc_id("bbaug")
    assert len(stable_doc_id("bimschg")) == 16


def test_content_hash_is_deterministic_and_change_sensitive() -> None:
    law = _law(
        sections=(
            StatuteSection(enbez="§ 1", titel="Zweck", text="(1) Erster Absatz."),
            StatuteSection(enbez="§ 2", titel=None, text="(1) Zweiter Absatz."),
        )
    )
    h1 = content_hash(law)
    h2 = content_hash(law)
    assert h1 == h2  # deterministic

    # Body edit flips the hash.
    edited_body = _law(
        sections=(
            StatuteSection(enbez="§ 1", titel="Zweck", text="(1) GEÄNDERT."),
            StatuteSection(enbez="§ 2", titel=None, text="(1) Zweiter Absatz."),
        )
    )
    assert content_hash(edited_body) != h1

    # Renamed enbez also flips the hash.
    renamed = _law(
        sections=(
            StatuteSection(enbez="§ 1a", titel="Zweck", text="(1) Erster Absatz."),
            StatuteSection(enbez="§ 2", titel=None, text="(1) Zweiter Absatz."),
        )
    )
    assert content_hash(renamed) != h1


def test_segments_from_parsed_law_drops_empty_and_carries_heading() -> None:
    law = _law(
        sections=(
            StatuteSection(enbez="§ 1", titel="Zweck", text="(1) Erster Absatz."),
            StatuteSection(enbez="Erster Abschnitt", titel="Allgemeines", text=""),
            StatuteSection(enbez=None, titel=None, text=""),  # purely empty → dropped
        )
    )
    segments = segments_from_parsed_law(law)
    assert len(segments) == 2

    para = segments[0]
    assert para["section"] == "§ 1"
    assert para["type"] == "text"
    assert para["text"].startswith("Zweck")  # heading prefixed
    assert "(1) Erster Absatz." in para["text"]

    heading_only = segments[1]
    assert heading_only["section"] == "Erster Abschnitt"
    assert heading_only["text"] == "Allgemeines"


def test_segments_fall_back_to_allgemein_when_no_marker() -> None:
    law = _law(sections=(StatuteSection(enbez=None, titel=None, text="Some loose text."),))
    segments = segments_from_parsed_law(law)
    assert segments[0]["section"] == "Allgemein"
    assert segments[0]["text"] == "Some loose text."


def test_stable_chunk_id_is_deterministic_and_kind_aware() -> None:
    a = stable_chunk_id("bimschg", "§ 1", 0, kind="p")
    b = stable_chunk_id("bimschg", "§ 1", 0, kind="p")
    assert a == b  # deterministic
    assert a != stable_chunk_id("bimschg", "§ 1", 0, kind="c")  # parent vs child differ
    assert a != stable_chunk_id("bimschg", "§ 1", 1, kind="p")  # index differs
    assert a != stable_chunk_id("bimschg", "§ 2", 0, kind="p")  # section differs
    assert len(a) == 24
