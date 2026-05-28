"""Report-hygiene guardrail: leaked debug artifacts must not reach the
client deliverable.

- ``[#n]`` evidence-chunk indices the analyzer LLM cites belong in the
  structured evidence array, not in the prose a partner reads.
- The mixed-language ``[Sprache: …]`` reviewer marker is no longer surfaced
  (A8 re-prompting handles wrong-language cells; a bracket tag reads as a defect).
"""

from __future__ import annotations

from types import SimpleNamespace

import _guardrail as g


def test_scrub_strips_chunk_refs_from_value():
    r = g.scrub_row_value("Die Pacht beträgt 6,0 % [#3] der Einspeiseerlöse [#1, 5].")
    assert "[#" not in r.cleaned
    assert "Pacht" in r.cleaned and "6,0" in r.cleaned
    # original preserved for audit
    assert "[#3]" in r.original


def test_scrub_chunk_only_value_becomes_empty():
    r = g.scrub_row_value("[#2]")
    assert "[#" not in r.cleaned and not r.cleaned.strip()


def test_apply_to_rows_strips_chunk_refs_in_value_and_note():
    rows = [SimpleNamespace(value="30 Nutzungsjahre [#2].", note="Quelle [#4]", ampel="yellow")]
    g.apply_to_rows(rows, target_language="de", section_language_hint="de")
    assert "[#" not in (rows[0].value or "")
    assert "[#" not in (rows[0].note or "")


def test_no_sprache_marker_on_mixed_language():
    # An English cell in a German section: counted as mixed, but NO visible tag.
    rows = [SimpleNamespace(value="The lease term is 30 years.", note=None, ampel="yellow")]
    counts = g.apply_to_rows(rows, target_language="de", section_language_hint="de")
    assert counts["mixed_lang"] >= 1
    assert not rows[0].note or "Sprache" not in rows[0].note
