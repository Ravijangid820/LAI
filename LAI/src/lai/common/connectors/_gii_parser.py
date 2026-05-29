"""Pure parser for gesetze-im-internet.de law XML (``gii-norm.dtd`` 1.0).

A single law downloads as a ``xml.zip`` containing one XML document whose
root (``<dokumente>``) holds a flat sequence of ``<norm>`` elements:

* The first ("frame") norm carries law-level metadata — ``<jurabk>`` (the
  citable abbreviation, e.g. ``BImSchG``) and ``<langue>`` (the long title).
* Each subsequent norm is one citable unit: ``<metadaten>`` carries the
  ``<enbez>`` designation (``§ 1``, ``Eingangsformel``, ``Inhaltsübersicht``,
  …) and an optional ``<titel>``; the body lives in
  ``<textdaten><text><Content>`` as ``<P>`` paragraphs / tables.

The functions here are **pure** — no I/O, no globals, no side effects — so
they unit-test cheaply against fixture XML. The HTTP fetch + unzip sits in
:class:`lai.common.connectors.gesetze.GesetzeImInternetClient`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # nosec B405 — Element type only; parsing uses defusedxml below
from dataclasses import dataclass

from defusedxml.ElementTree import fromstring as _safe_fromstring

__all__ = [
    "ParsedLaw",
    "StatuteSection",
    "parse_law_xml",
]

# Block-level GII tags after which a newline preserves the document's
# paragraph/section structure for the downstream German-aware chunker.
_BLOCK_TAGS = frozenset({"P", "BR", "Title", "Ident", "row", "pre", "DT", "DD", "LA", "Citation"})

_WS_RUN = re.compile(r"[ \t  ]+")


@dataclass(frozen=True)
class StatuteSection:
    """One citable unit of a law (typically a ``§`` or ``Artikel``)."""

    enbez: str | None  # "§ 1", "Eingangsformel", "Inhaltsübersicht", or None
    titel: str | None  # human heading, e.g. "Zweck des Gesetzes"
    text: str  # flattened body text (may be empty for headings-only norms)


@dataclass(frozen=True)
class ParsedLaw:
    """A whole law: its citable abbreviation, long title, and sections."""

    jurabk: str | None  # "BImSchG"
    long_title: str | None  # the <langue> long title from the frame norm
    sections: tuple[StatuteSection, ...]


def _first_text(parent: ET.Element, tag: str) -> str | None:
    """Return the stripped text of ``parent``'s first ``tag`` child, or None."""
    el = parent.find(tag)
    if el is None or el.text is None:
        return None
    stripped = el.text.strip()
    return stripped or None


def _flatten(content: ET.Element) -> str:
    """Flatten a ``<Content>`` subtree into readable, paragraph-broken text.

    Walks the tree emitting each node's text + tail and inserting a newline
    after block-level elements, then normalises whitespace: spaces/tabs/NBSP
    runs collapse to one space, lines are trimmed, and blank-line runs are
    capped at one. This keeps ``§``/``Absatz`` paragraph boundaries that the
    chunker's section detector relies on, without leaking XML structure.
    """
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tag in _BLOCK_TAGS:
                parts.append("\n")
            if child.tail:
                parts.append(child.tail)

    walk(content)
    raw = "".join(parts)

    out: list[str] = []
    blank = 0
    for line in raw.splitlines():
        cleaned = _WS_RUN.sub(" ", line).strip()
        if cleaned:
            out.append(cleaned)
            blank = 0
        else:
            blank += 1
            if blank == 1 and out:
                out.append("")
    return "\n".join(out).strip()


def parse_law_xml(xml: bytes | str) -> ParsedLaw:
    """Parse one gesetze-im-internet.de law XML document.

    ``xml`` is the raw bytes/str of the single ``.xml`` file inside a law's
    ``xml.zip``. Norms with neither a heading nor body text (pure structural
    placeholders) are dropped; everything citable is kept, including the
    ``Inhaltsübersicht`` (table of contents) and ``Eingangsformel`` norms,
    since the caller decides what to ingest.

    Raises :class:`xml.etree.ElementTree.ParseError` on malformed XML.
    """
    root: ET.Element = _safe_fromstring(xml if isinstance(xml, str) else xml.decode("utf-8", errors="replace"))

    jurabk: str | None = None
    long_title: str | None = None
    sections: list[StatuteSection] = []

    for norm in root.findall(".//norm"):
        md = norm.find("metadaten")
        if md is not None:
            if jurabk is None:
                jurabk = _first_text(md, "jurabk")
            if long_title is None:
                long_title = _first_text(md, "langue")

        enbez = _first_text(md, "enbez") if md is not None else None
        titel = _first_text(md, "titel") if md is not None else None

        content = norm.find("textdaten/text/Content")
        body = _flatten(content) if content is not None else ""

        if not body and not titel and not enbez:
            continue
        sections.append(StatuteSection(enbez=enbez, titel=titel, text=body))

    return ParsedLaw(jurabk=jurabk, long_title=long_title, sections=tuple(sections))
