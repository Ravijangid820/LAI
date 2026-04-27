"""German cadastral NER — extract Parcel records for map plotting.

Two-stage:
  1) Cheap regex candidate detection — high recall, low precision.
  2) Schema-constrained LLM extraction on each candidate window.

See docs/analysis/CONTRACT_ANALYZER_V2.md §8.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from lai.analyzer.schema import Parcel


# ---------------------------------------------------------------------------
# Stage 1 — candidate detection
# ---------------------------------------------------------------------------

_PATTERNS = [
    re.compile(r"Flurst(?:ü|ue)cke?\s*(?:Nr\.?\s*)?[\d/]+(?:\s*,\s*[\d/]+)*", re.IGNORECASE),
    re.compile(r"\bFl\.?St\.?\s*[\d/]+", re.IGNORECASE),
    re.compile(r"Gemarkung\s+[A-ZÄÖÜ][\wäöüß\-]+", re.IGNORECASE),
    re.compile(r"\bFlur\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bParzelle\s+\d+\b", re.IGNORECASE),
]

# Window of context (chars) to feed the LLM around each candidate
_WINDOW = 600


@dataclass
class _Candidate:
    span: tuple[int, int]
    text: str
    page: Optional[int]


def find_candidates(text: str) -> list[_Candidate]:
    """Return non-overlapping context windows that look cadastral.

    Adjacent matches within 200 chars of each other are merged into one
    window so the LLM sees the full Gemarkung+Flur+Flurstück triple at once.
    """
    hits: list[tuple[int, int]] = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            hits.append((m.start(), m.end()))
    if not hits:
        return []
    hits.sort()

    merged: list[tuple[int, int]] = []
    cur_s, cur_e = hits[0]
    for s, e in hits[1:]:
        if s - cur_e < 200:
            cur_e = max(cur_e, e)
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))

    candidates: list[_Candidate] = []
    for s, e in merged:
        ws = max(0, s - _WINDOW // 2)
        we = min(len(text), e + _WINDOW // 2)
        candidates.append(_Candidate(span=(ws, we), text=text[ws:we], page=None))
    return candidates


# ---------------------------------------------------------------------------
# Stage 2 — LLM extraction
# ---------------------------------------------------------------------------

NER_SYSTEM = (
    "Du bist ein juristischer Datenextraktor. Aus dem folgenden Auszug aus "
    "einem deutschen Vertrag extrahiere ALLE genannten Flurstücke / Parzellen. "
    "Antworte AUSSCHLIESSLICH mit einer JSON-Liste; keine Erklärung, keine "
    "Markdown-Codeblöcke. Format pro Eintrag:\n"
    '{\n'
    '  "gemeinde": "<Stadt/Gemeindename oder null>",\n'
    '  "gemarkung": "<Gemarkungsname oder null>",\n'
    '  "flur": "<z.B. \\"2\\" oder null>",\n'
    '  "flurstueck": "<z.B. \\"47/3\\" oder null>",\n'
    '  "groesse_m2": <Zahl in m² oder null>,\n'
    '  "eigentuemer": "<Eigentümer oder null>",\n'
    '  "raw_mention": "<wörtlicher Ausschnitt aus dem Text>"\n'
    "}\n\n"
    "Wenn ein Eintrag mehrere Flurstücke nennt (z.B. '47/3, 47/4'), erzeuge je "
    "einen Listeneintrag pro Flurstück und übernimm Gemarkung/Flur in beide. "
    "Wenn keine Flurstücke vorhanden sind, antworte mit []."
)


JSON_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "gemeinde": {"type": ["string", "null"]},
            "gemarkung": {"type": ["string", "null"]},
            "flur": {"type": ["string", "null"]},
            "flurstueck": {"type": ["string", "null"]},
            "groesse_m2": {"type": ["number", "null"]},
            "eigentuemer": {"type": ["string", "null"]},
            "raw_mention": {"type": "string"},
        },
        "required": ["raw_mention"],
        "additionalProperties": False,
    },
}


# Type alias: callable that takes (system_prompt, user_text, json_schema) and
# returns the raw model output as a string. Caller injects this so we can
# unit-test without spinning up a model.
LLMCall = Callable[[str, str, Optional[dict]], str]


def _parse_json_lenient(s: str) -> object:
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        for ch in "[{":
            i = s.find(ch)
            if i >= 0:
                try:
                    return json.loads(s[i:])
                except json.JSONDecodeError:
                    continue
    return None


def _dedupe(parcels: list[Parcel]) -> list[Parcel]:
    seen: set[tuple] = set()
    out: list[Parcel] = []
    for p in parcels:
        key = (p.gemarkung or "", p.flur or "", p.flurstueck or "")
        if key == ("", "", "") or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def extract_parcels(text: str, llm_call: LLMCall) -> list[Parcel]:
    """Run the two-stage extraction. Empty list on no candidates."""
    candidates = find_candidates(text)
    if not candidates:
        return []

    parcels: list[Parcel] = []
    for cand in candidates:
        try:
            raw = llm_call(NER_SYSTEM, cand.text, JSON_SCHEMA)
        except Exception:
            continue
        parsed = _parse_json_lenient(raw)
        if not isinstance(parsed, list):
            continue
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                parcels.append(Parcel(
                    gemeinde=entry.get("gemeinde"),
                    gemarkung=entry.get("gemarkung"),
                    flur=str(entry["flur"]) if entry.get("flur") is not None else None,
                    flurstueck=str(entry["flurstueck"]) if entry.get("flurstueck") is not None else None,
                    groesse_m2=entry.get("groesse_m2"),
                    eigentuemer=entry.get("eigentuemer"),
                    raw_mention=str(entry.get("raw_mention", ""))[:500],
                    page=cand.page,
                ))
            except Exception:
                continue
    return _dedupe(parcels)
