"""Cross-document consistency check (P0 #3).

Detects contradictions BETWEEN the analysed documents — the classic
DD red flag that pure-RAG Q&A misses because each question runs in
isolation. Examples: BImSchG permit count ≠ lease parcel count,
lease term shorter than EEG-award duration, lessor names
inconsistent.

Unlike the per-domain extractors, this one operates on already-
extracted ``sections`` / ``weas`` / ``parcels`` data rather than
hitting RAG itself — the inputs ARE the cross-document evidence.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ddiq.llm import EXTRACTION_SYSTEM, llm_json
from ddiq.models import (
    AusgabeblattSection,
    CadastralParcel,
    Finding,
    Quantification,
    WEAStatus,
)

__all__ = ["check_cross_doc_consistency"]


_log = logging.getLogger("ddiq")


def check_cross_doc_consistency(
    sections: list[AusgabeblattSection],
    weas: list[WEAStatus],
    parcels: list[CadastralParcel],
    total_capacity_mw: Optional[float] = None,
) -> list[Finding]:
    """Detect contradictions BETWEEN the analysed documents.

    Cross-doc findings now carry **evidence** harvested from the
    section rows that triggered each inconsistency. Previously every
    cross-doc Finding had ``evidence=[]`` (the Pydantic default), which
    landed all of them in the FE's "Document not specified" bucket
    regardless of how grounded the observation was. The LLM is now
    asked to cite ``evidence_rows: [{section, label}, ...]``; each
    cited (section, label) pair is resolved back to the corresponding
    Ausgabeblatt row's ``evidence`` list and merged into the Finding.
    """
    # Build a lookup from (section_title, row_label) → row.evidence so
    # cited rows can be resolved in O(1). Label matching is exact —
    # the LLM is given the canonical labels in the facts payload, so
    # any cite should be a verbatim copy.
    row_index: dict[tuple[str, str], list[Any]] = {}
    for s in sections:
        for r in s.rows:
            ev = list(getattr(r, "evidence", None) or [])
            if ev:
                row_index[(s.title, r.label)] = ev

    facts: dict[str, Any] = {
        "sections": [
            {"section": s.title, "label": r.label, "value": r.value, "ampel": r.ampel}
            for s in sections for r in s.rows
        ],
        "wea_count": len(weas),
        "wea_status_codes": [w.status_code for w in weas if w.status_code],
        "parcel_count": len(parcels),
        "parcel_secured": sum(1 for p in parcels if p.status == "secured"),
        "parcel_not_secured": sum(1 for p in parcels if p.status == "not_secured"),
        "total_capacity_mw": total_capacity_mw,
    }
    prompt = f"""You are doing the cross-document consistency check on a wind-park DD.
Scan these extracted facts for contradictions, missing-document red flags, or
inconsistencies that a Berufsanwalt would immediately challenge.

Facts:
{json.dumps(facts, ensure_ascii=False, indent=2)}

Look for:
- Turbine count differs across BImSchG-Bescheid / Pachtvertrag / EEG-Zuschlag
- Total MW from sections doesn't match (#turbines × rated power)
- Pachtdauer < expected operational life (typically 25 yr)
- Lessor / Verpächter names inconsistent across leases
- Project Company in Pachtvertrag ≠ Antragstellerin im BImSchG-Antrag
- Number of secured parcels < number of WEA (each WEA needs Standort + Zuwegung)
- BImSchG permit erteilt but no Pachtvertrag for one or more parcels
- EEG-Zuschlag erteilt but Inbetriebnahme-Frist conflicts with construction status
- Cited capacity in Erläuterungsbericht ≠ EEG-Zuschlag MW
- Missing core document type: BImSchG-Bescheid / Pachtvertrag / Netzanschluss / Rückbaubürgschaft

Return JSON array. Each entry:
{{"text":"clear factual statement of the inconsistency",
  "severity":"red|yellow",
  "domain":"Land|Permits|Economics|Regulatory|General",
  "legal_basis":"if applicable",
  "recommended_action":"what to do about it",
  "quantification":{{"mw_affected":..,"eur_impact_estimate":..,"days_until_deadline":..,"rationale":".."}},
  "evidence_rows":[{{"section":"<exact section title from Facts>","label":"<exact row label from Facts>"}}, ...]}}

Use ``evidence_rows`` to cite the Ausgabeblatt rows that ground this
inconsistency. Cite at least one row whenever the observation derives
from a row's value or ampel. Leave ``evidence_rows`` as ``[]`` only for
purely structural counts (e.g. "wea_count vs parcel_count") that come
from the count fields above rather than a specific row.

Return [] if no inconsistencies found. Never fabricate — only flag what the
facts clearly contradict."""
    try:
        result: Any = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict):
            result = result.get(
                "inconsistencies",
                result.get("findings", result.get("data", [])),
            )
        if not isinstance(result, list):
            return []
        out: list[Finding] = []
        for r in result:
            text = str(r.get("text", "")).strip()
            if not text:
                continue
            q_raw = r.get("quantification") or {}
            quant: Optional[Quantification] = None
            if isinstance(q_raw, dict) and any(
                q_raw.get(k) is not None
                for k in ("mw_affected", "eur_impact_estimate", "days_until_deadline")
            ):
                quant = Quantification(
                    mw_affected=q_raw.get("mw_affected"),
                    eur_impact_estimate=q_raw.get("eur_impact_estimate"),
                    days_until_deadline=q_raw.get("days_until_deadline"),
                    rationale=q_raw.get("rationale"),
                )

            # Resolve LLM-cited (section,label) pairs back to the
            # actual rows' Evidence. Dedupe by (doc_id, excerpt) so
            # the same chunk isn't attached twice if two cited rows
            # both pulled it. A bad cite (unknown section/label) is
            # silently skipped — defensive against LLM hallucinating
            # row identifiers — but logged so the dropped-cite rate
            # surfaces if the prompt starts drifting.
            evidence_list: list[Any] = []
            seen: set[tuple[Any, Any]] = set()
            cited = r.get("evidence_rows") or []
            dropped_cites: list[Any] = []
            if isinstance(cited, list):
                for c in cited:
                    if not isinstance(c, dict):
                        continue
                    key = (
                        str(c.get("section") or "").strip(),
                        str(c.get("label") or "").strip(),
                    )
                    row_ev = row_index.get(key)
                    if not row_ev:
                        dropped_cites.append(c)
                        continue
                    for e in row_ev:
                        dk = (
                            getattr(e, "doc_id", None) or getattr(e, "doc_filename", None),
                            (getattr(e, "excerpt", None) or "")[:50],
                        )
                        if dk in seen:
                            continue
                        seen.add(dk)
                        evidence_list.append(e)
            if dropped_cites:
                _log.warning(
                    "cross-doc: dropped %d/%d evidence_rows cites that "
                    "didn't match any analysed row (LLM cited a section "
                    "or label that doesn't exist): %r",
                    len(dropped_cites), len(cited), dropped_cites,
                )

            out.append(Finding(
                domain=str(r.get("domain", "General")),
                severity=(
                    r.get("severity")
                    if r.get("severity") in ("red", "yellow", "green")
                    else "yellow"
                ),
                text=text,
                legal_basis=r.get("legal_basis"),
                recommended_action=r.get("recommended_action"),
                quantification=quant,
                kind="cross_document",
                evidence=evidence_list,
            ))
        return out
    except Exception as e:
        _log.error(f"Cross-doc consistency: {e}")
        return []
