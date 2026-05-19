"""Grundbuch lessor-vs-owner consistency (P1 #6).

Compare Pachtvertrag-lessor against the registered Eigentümer per
Grundbuch. A parcel can show ``secured`` under contract logic even
if the lessor has no legal title — this pass is the next layer of
validation. Also surfaces encumbrances (Belastungen) the LLM finds:
``Wegerecht``, ``§24 BauGB Vorkaufsrecht``, ``Hypothek``, ``Reallast``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ddiq.llm import EXTRACTION_SYSTEM, llm_json
from ddiq.models import CadastralParcel, GrundbuchCheck
from ddiq.rag import evidence_from_chunks, rag_context_with_meta

__all__ = ["check_grundbuch_match"]


_log = logging.getLogger("ddiq")


# Cap on how many parcels we run through the LLM. Grundbuch lookup
# is the most expensive single LLM pass per parcel. Lawyer-grade DD
# would check every one externally, but we surface the LLM's read on
# what's actually extractable from the supplied PDFs.
_MAX_PARCELS_PER_CHECK = 25


def check_grundbuch_match(
    doc_ids: list[str],
    parcels: list[CadastralParcel],
) -> list[GrundbuchCheck]:
    """Run the Grundbuch lessor/owner consistency pass.

    Returns ``[]`` when there are no secured parcels with a
    normalised ID — there's nothing to check against — or when the
    LLM returns nothing usable.
    """
    if not parcels:
        return []
    target = [
        p for p in parcels
        if p.status == "secured" and p.normalizedId
    ][:_MAX_PARCELS_PER_CHECK]
    if not target:
        return []
    ctx, reranked = rag_context_with_meta(
        doc_ids,
        "Grundbuch Eigentümer Eintragung Belastung Wegerecht Vorkaufsrecht Hypothek Reallast Pächter Verpächter",
        top_k=10,
    )
    parcel_list = [
        {
            "parcel_id": p.normalizedId,
            "owner_per_alkis": p.owner,
            "lessor_or_contract_ref": p.contractRef or "unknown",
        }
        for p in target
    ]
    prompt = f"""For each parcel in the list, judge whether the registered Grundbuch-
Eigentümer matches the Verpächter named in the Pachtvertrag, and list any
encumbrances (Belastungen) you can find in the documents.

Context:
{ctx}

Parcels:
{json.dumps(parcel_list, ensure_ascii=False)}

Return JSON array. Each entry:
{{"parcel_id":"...",
  "registered_owner":"Eigentümer per Grundbuch or null",
  "lessor_name":"Verpächter per Pachtvertrag or null",
  "owner_match":<true/false/null — null if undeterminable>,
  "match_confidence":<0.0..1.0>,
  "encumbrances":["Wegerecht zugunsten Gemeinde X","§24 BauGB Vorkaufsrecht",...],
  "note":"short explanation",
  "evidence_chunks":[1,4]}}

Only return parcels that appear in the supplied list. owner_match=null is fine
if the documents don't show enough — don't guess. encumbrances=[] is fine when
nothing is registered."""
    try:
        result: Any = llm_json(EXTRACTION_SYSTEM, prompt)
        if isinstance(result, dict):
            result = result.get("checks", result.get("data", []))
        if not isinstance(result, list):
            return []
        out: list[GrundbuchCheck] = []
        for r in result:
            pid = str(r.get("parcel_id", "")).strip()
            if not pid:
                continue
            try:
                conf = float(r.get("match_confidence", 0.0))
            except Exception:
                conf = 0.0
            out.append(GrundbuchCheck(
                parcel_id=pid,
                registered_owner=r.get("registered_owner"),
                lessor_name=r.get("lessor_name"),
                owner_match=r.get("owner_match"),
                match_confidence=conf,
                encumbrances=[str(x) for x in (r.get("encumbrances") or []) if x],
                note=r.get("note"),
                evidence=evidence_from_chunks(reranked, r.get("evidence_chunks", [])),
            ))
        return out
    except Exception as e:
        _log.error(f"Grundbuch check: {e}")
        return []
