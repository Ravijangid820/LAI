"""Rückbaubürgschaft extraction (P1 #9).

§35 Abs. 5 BauGB requires the operator to post a decommissioning
bond. Recurring DD red flag — without verifying it, the project can
be blocked at financial close. Pull amount, provider, beneficiary,
validity from the Auflagen of the BImSchG-Bescheid or a separate
Bürgschaftsurkunde.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ddiq.llm import EXTRACTION_SYSTEM, llm_json
from ddiq.models import RueckbauBond
from ddiq.rag import evidence_from_chunks, rag_context_with_meta

__all__ = ["extract_rueckbau_bond"]


_log = logging.getLogger("ddiq")


def extract_rueckbau_bond(doc_ids: list[str]) -> Optional[RueckbauBond]:
    """Extract the Rückbaubürgschaft facts. Returns ``None`` on
    transport / parse failure (not the same as "not found in
    documents", which returns a :class:`RueckbauBond` with null
    fields and a ``note``)."""
    ctx, reranked = rag_context_with_meta(
        doc_ids,
        "Rückbau Bürgschaft Sicherheitsleistung Hinterlegung Konzernbürgschaft §35 BauGB Abriss Beseitigung",
        top_k=6,
    )
    prompt = f"""Extract the Rückbaubürgschaft (decommissioning bond) facts.

Context:
{ctx}

Return JSON object:
{{"amount_eur": <number or null>,
  "provider": "<bank/insurer/parent or null>",
  "beneficiary": "<usually Standortgemeinde>",
  "valid_until": "YYYY-MM-DD or null",
  "instrument_type": "Bürgschaft|Hinterlegung|Konzernbürgschaft|null",
  "sufficient": <true/false/null — your read on whether the amount covers
   expected Rückbaukosten (typical 80-150k €/MW)>,
  "note": "one-sentence assessment",
  "evidence_chunks":[1,3]}}

If no Rückbau bond is mentioned, return null fields with note='not found in documents'.
Never fabricate amounts."""
    try:
        result: Any = llm_json(EXTRACTION_SYSTEM, prompt)
        if not isinstance(result, dict):
            return None
        if all(
            result.get(k) is None
            for k in ("amount_eur", "provider", "valid_until", "instrument_type")
        ):
            # Nothing real extracted; surface a placeholder so the
            # lawyer knows the absence is intentional, not a UI bug.
            return RueckbauBond(
                note=str(result.get(
                    "note",
                    "Rückbaubürgschaft not found in supplied documents.",
                ))
            )
        return RueckbauBond(
            amount_eur=result.get("amount_eur"),
            provider=result.get("provider"),
            beneficiary=result.get("beneficiary"),
            valid_until=result.get("valid_until"),
            instrument_type=result.get("instrument_type"),
            sufficient=result.get("sufficient"),
            note=result.get("note"),
            evidence=evidence_from_chunks(reranked, result.get("evidence_chunks", [])),
        )
    except Exception as e:
        _log.error(f"Rückbaubürgschaft extraction: {e}")
        return None
