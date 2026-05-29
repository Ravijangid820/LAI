"""Unit tests for the gesetze-im-internet.de law-XML parser."""

from __future__ import annotations

import pytest
from defusedxml.ElementTree import ParseError

from lai.common.connectors._gii_parser import parse_law_xml

pytestmark = pytest.mark.unit

# A minimal but structurally faithful GII document: a frame norm carrying
# law-level metadata, one real § with multi-paragraph body, a heading-only
# norm (kept), and a purely structural norm (dropped).
_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<dokumente>
  <norm builddate="20260101" doknr="frame">
    <metadaten>
      <jurabk>TestG</jurabk>
      <langue>Gesetz zum Testen von Dingen</langue>
    </metadaten>
    <textdaten><text format="XML"><Content/></text></textdaten>
  </norm>
  <norm doknr="s1">
    <metadaten>
      <jurabk>TestG</jurabk>
      <enbez>§ 1</enbez>
      <titel>Zweck</titel>
    </metadaten>
    <textdaten><text format="XML"><Content>
      <P>(1) Erster Absatz   mit Text.</P>
      <P>(2) Zweiter Absatz.<BR/>Mit Umbruch.</P>
    </Content></text></textdaten>
  </norm>
  <norm doknr="heading">
    <metadaten>
      <jurabk>TestG</jurabk>
      <enbez>Erster Abschnitt</enbez>
      <titel>Allgemeines</titel>
    </metadaten>
    <textdaten><text format="XML"><Content/></text></textdaten>
  </norm>
  <norm doknr="structural">
    <metadaten><jurabk>TestG</jurabk></metadaten>
    <textdaten><text format="XML"><Content/></text></textdaten>
  </norm>
</dokumente>"""


def test_extracts_law_level_metadata() -> None:
    law = parse_law_xml(_SAMPLE)
    assert law.jurabk == "TestG"
    assert law.long_title == "Gesetz zum Testen von Dingen"


def test_keeps_citable_norms_drops_structural() -> None:
    law = parse_law_xml(_SAMPLE)
    # § 1 (body) + the heading-only norm are kept; the structural norm and
    # the bodyless frame norm are dropped.
    enbeze = [s.enbez for s in law.sections]
    assert enbeze == ["§ 1", "Erster Abschnitt"]


def test_section_fields_and_whitespace_normalisation() -> None:
    law = parse_law_xml(_SAMPLE)
    para = next(s for s in law.sections if s.enbez == "§ 1")
    assert para.titel == "Zweck"
    # Runs of internal whitespace collapse to a single space.
    assert "Erster Absatz mit Text." in para.text
    # Paragraph + <BR/> boundaries become newlines.
    lines = para.text.splitlines()
    assert lines[0] == "(1) Erster Absatz mit Text."
    assert "Mit Umbruch." in para.text


def test_heading_only_norm_has_empty_body() -> None:
    law = parse_law_xml(_SAMPLE)
    heading = next(s for s in law.sections if s.enbez == "Erster Abschnitt")
    assert heading.titel == "Allgemeines"
    assert heading.text == ""


def test_accepts_bytes_and_str_equivalently() -> None:
    from_str = parse_law_xml(_SAMPLE)
    from_bytes = parse_law_xml(_SAMPLE.encode("utf-8"))
    assert from_bytes == from_str


def test_missing_metadata_yields_none() -> None:
    law = parse_law_xml(
        "<dokumente><norm><textdaten><text><Content><P>Body only.</P></Content></text></textdaten></norm></dokumente>"
    )
    assert law.jurabk is None
    assert law.long_title is None
    assert len(law.sections) == 1
    assert law.sections[0].enbez is None
    assert law.sections[0].text == "Body only."


def test_malformed_xml_raises() -> None:
    with pytest.raises(ParseError):
        parse_law_xml("<dokumente><norm></dokumente>")
